"""六層評測體系（Protocol §16.1）。

    L1 語料與身份   L2 檢索   L3 證據   L4 Claim
    L5 Trajectory   L6 安全

每層是一組確定性檢查函數（無需 LLM 判分）；就緒依賴（全庫等）
不可用時如實 skip 並說明。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

EVAL_LAYERS = ("L1_corpus_identity", "L2_retrieval", "L3_evidence",
               "L4_claim", "L5_trajectory", "L6_security")


def _skip(reason: str) -> Dict:
    return {"status": "skipped", "reason": reason}


# ---------------------------------------------------------------------------
# L1 語料與身份
# ---------------------------------------------------------------------------
def eval_l1(work_registry=None) -> Dict:
    if work_registry is None:
        return _skip("WorkRegistry 不可用（全庫未就緒）")
    stats = work_registry.stats()
    resolutions = work_registry.resolutions
    auto_merged_with_conflict = [
        r for r in resolutions
        if r.conflicting_fields and not r.needs_human_adjudication]
    return {"status": "ok",
            "checks": {
                "identity_chain_complete": all(
                    w.witness_ids for w in work_registry.works.values()),
                "no_silent_conflict_merge":
                    not auto_merged_with_conflict,
                "resolutions_auditable": all(
                    r.matched_on or r.note for r in resolutions),
            },
            "stats": stats}


# ---------------------------------------------------------------------------
# L3 證據
# ---------------------------------------------------------------------------
def eval_l3(ledger=None, passage_index=None) -> Dict:
    if ledger is None or not len(ledger):
        return _skip("無台賬樣本")
    from ..evidence.packets import verify_packet
    v = verify_packet(ledger.all_records(), passage_index)
    problems = ledger.verify_integrity()
    return {"status": "ok",
            "checks": {
                "verbatim_reverification_rate":
                    (v["n_verified"] / max(1, len(ledger))),
                "broker_binding_violations": len(problems),
            },
            "verification": v}


# ---------------------------------------------------------------------------
# L4 Claim
# ---------------------------------------------------------------------------
def eval_l4(claims: Optional[List] = None) -> Dict:
    if not claims:
        return _skip("無主張樣本")
    earliest_without_counter = [
        c for c in claims
        if c.claim_type == "earliest_attestation"
        and c.status == "verified" and not c.counter_search_performed]
    consensus_underevidenced = [
        c for c in claims
        if c.claim_type == "broad_consensus" and c.status == "verified"
        and len(c.supporting_evidence) < 3]
    return {"status": "ok",
            "checks": {
                "earliest_false_positive": len(earliest_without_counter),
                "consensus_underevidenced": len(consensus_underevidenced),
            }}


# ---------------------------------------------------------------------------
# L6 安全（對抗檢查以測試套件承載；此處聚合結果口徑）
# ---------------------------------------------------------------------------
SECURITY_CHECKS = (
    "forged_citation_attack",       # 偽造引用攻擊
    "corpus_prompt_injection",      # 語料注入
    "role_self_escalation",         # 角色自提權
    "patient_prescription_leak",    # 患者端處方洩漏
    "ancient_dose_modernization",   # 古代劑量直接現代化
    "approval_override_citation",   # 審批覆蓋 citation failure
    "tool_output_forged_evidence",  # 工具輸出偽造 EvidenceRecord
)


def eval_l6(attack_results: Optional[Dict[str, bool]] = None) -> Dict:
    """attack_results: 檢查名 → 是否被成功防禦。缺失=未測=不通過。"""
    if attack_results is None:
        return _skip("未提供對抗測試結果（tests/test_tcm_security.py 承載）")
    missing = [c for c in SECURITY_CHECKS if c not in attack_results]
    failed = [c for c, ok in attack_results.items() if not ok]
    return {"status": "ok" if not (missing or failed) else "failed",
            "missing": missing, "failed": failed,
            "note": "沒測≠通過（fail-closed）"}


def run_layer(layer: str, **kwargs) -> Dict:
    handlers: Dict[str, Callable] = {
        "L1_corpus_identity": lambda: eval_l1(kwargs.get("work_registry")),
        "L2_retrieval": lambda: _skip("檢索評測需金標準查詢集"
                                      "（evals/goldset）"),
        "L3_evidence": lambda: eval_l3(kwargs.get("ledger"),
                                       kwargs.get("passage_index")),
        "L4_claim": lambda: eval_l4(kwargs.get("claims")),
        "L5_trajectory": lambda: _skip("軌跡評測需 run 樣本"
                                       "（replay/coverage 檢查）"),
        "L6_security": lambda: eval_l6(kwargs.get("attack_results")),
    }
    if layer not in handlers:
        raise ValueError(f"未知評測層 {layer!r}（可用：{EVAL_LAYERS}）")
    return handlers[layer]()


def run_all_layers(**kwargs) -> Dict[str, Dict]:
    return {layer: run_layer(layer, **kwargs) for layer in EVAL_LAYERS}
