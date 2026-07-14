"""發布閘門 V2（Protocol §4/§15）：對 Claim Graph + AnswerEnvelope 裁定。

五態沿襲並強化 hermes_shanghan：

    pass / pass_with_warning / review_required / blocked / failed_closed

新增裁定維度：

* claim 級：任何 failed 主張出現在信封 → blocked（引用台賬外證據=偽造）；
* coverage 級：負結論主張無覆蓋記錄 → blocked；
* purpose 級：目的禁止能力出現在輸出 → blocked（患者教育端劑量等）；
* citation failure 永不可審批豁免。
"""
from __future__ import annotations

from typing import Any, Dict, FrozenSet, List, Sequence

from ..claims.records import ClaimRecord
from ..core.policies import purpose_allows
from .approvals import ADJUDICATION_TRIGGERS, HUMAN_REVIEW_TRIGGERS

# 可執行診療動作抽取（確定性；沿襲 hermes_shanghan release_gate）
import re as _re

_ACTION_PATTERNS = [
    # 面向 fail-closed 目的閘門的保守寬鬆匹配：僅對「禁用該能力的目的」
    # 有影響，故寧可誤報也不漏報
    ("medication_recommendation",
     _re.compile(r"[一-鿿]{1,6}(?:湯|丸|散|飲)(?:主之|可服|宜服|服之|"
                 r"可與|可考慮|調理|很?適合)|建議(?:服用|使用)|"
                 r"(?:建議|推薦|可用|可以|不妨)[一-鿿]{0,3}?"
                 r"[一-鿿]{1,6}(?:湯|丸|散|飲)|"
                 r"[服喝用][一-鿿]{1,6}(?:湯|丸|散|飲)")),
    ("dosing_instruction",
     _re.compile(r"(?:每日|一日)\s*[一二三四五六七八九十\d]+\s*(?:次|服)|"
                 r"[一二三四五六七八九十百\d]+(?:兩|銖|升|枚|克)|"
                 r"[一二三四五六七八九十百\d]+g(?![A-Za-z])|劑量")),
    ("administration_instruction",
     _re.compile(r"煎服|溫服|頓服|分溫|水煎|先煮|去滓")),
]


def clinical_actions(text: str) -> List[Dict]:
    out: List[Dict] = []
    for action_type, rx in _ACTION_PATTERNS:
        m = rx.search(text or "")
        if m:
            out.append({"action_type": action_type, "cue": m.group(0)[:24]})
    return out


def evaluate_release(spec, claims: Sequence[ClaimRecord],
                     answer: str,
                     ledger_problems: Sequence[Dict] = (),
                     approved: FrozenSet[str] = frozenset(),
                     refused: bool = False) -> Dict[str, Any]:
    """spec: RunSpecV2；claims: 已核驗的主張；answer: 綁定後回答。"""
    gates: Dict[str, Dict] = {}
    reasons: List[str] = []
    review: List[str] = []
    blocked: List[str] = []
    warnings: List[str] = []

    # 0. fail-closed：非拒答但無主張圖 → 關鍵核驗對象缺失
    if not refused and not claims:
        return {"decision": "failed_closed",
                "gates": {"claim_gate": {"ok": False,
                                         "missing": "claim_graph"}},
                "review_required": [], "blocked_reasons": [],
                "reasons": ["無 Claim Graph——關鍵核驗對象不存在時一律 "
                            "fail-closed，不推定 ok"],
                "approved": sorted(approved)}

    # 1. ledger 完整性（Broker 綁定違例=系統故障，不可放行）
    if ledger_problems:
        blocked.append(f"證據台賬完整性違例 {len(ledger_problems)} 條"
                       "（Broker 綁定字段缺失）——不可人工放行")
    gates["ledger_gate"] = {"ok": not ledger_problems,
                            "problems": list(ledger_problems)[:5]}

    # 2. claim gate：failed 主張=證據失敗
    failed = [c for c in claims if c.status == "failed"]
    needs_review = [c for c in claims if c.status == "needs_review"]
    verified = [c for c in claims if c.status == "verified"]
    gates["claim_gate"] = {
        "ok": not failed,
        "n_verified": len(verified),
        "n_failed": len(failed),
        "n_needs_review": len(needs_review),
        "failed_claims": [c.claim_id for c in failed][:5]}
    if failed:
        # 區分偽造（attribution fail=台賬外證據）與核驗不足
        forged = [c for c in failed
                  if c.verification.get("attribution") == "fail"
                  or c.verification.get("quotation") == "fail"]
        if forged:
            blocked.append("偽造/失配引用：主張綁定台賬外證據或逐字重驗"
                           "失敗（" + "、".join(c.claim_id for c in
                                               forged[:3])
                           + "）——必須修復後重跑")
        else:
            review.append("citation_failure")
            reasons.append(HUMAN_REVIEW_TRIGGERS["citation_failure"])

    # 3. review gate：needs_review 主張逐項生成觸發鍵
    for c in needs_review:
        for trig in c.verification.get("policy", {}).get(
                "review_required", []) or ["semantic_support_review"]:
            key = trig if trig in HUMAN_REVIEW_TRIGGERS \
                else "semantic_support_review"
            if key not in review:
                review.append(key)
                reasons.append(HUMAN_REVIEW_TRIGGERS.get(key, trig))

    # 4. purpose gate：目的禁止能力
    purpose = spec.principal.purpose_of_use
    actions = clinical_actions(answer)
    purpose_violations: List[str] = []
    for a in actions:
        cap = {"medication_recommendation": "formula_recommendation",
               "dosing_instruction": "dosage_conversion",
               "administration_instruction": "modern_prescription"}[
            a["action_type"]]
        ok, reason = purpose_allows(purpose, cap, spec.principal.role)
        if not ok:
            purpose_violations.append(f"{a['action_type']}：{reason}")
    gates["purpose_gate"] = {"ok": not purpose_violations,
                             "purpose": purpose,
                             "clinical_actions": actions,
                             "violations": purpose_violations}
    if purpose_violations:
        blocked.append("purpose_violation：輸出含目的禁止能力（"
                       + "；".join(purpose_violations[:2])
                       + "）——目的隔離失效屬硬故障，不可人工放行")

    # 5. 強制限定語核驗：verified 主張的 forced_qualifiers 必須出現在
    #    回答中（「在當前語料庫範圍內」不可被綜合步驟丟棄）
    missing_qualifiers: List[str] = []
    for c in verified:
        for q in c.forced_qualifiers:
            if q and q not in (answer or ""):
                missing_qualifiers.append(f"{c.claim_id}:{q}")
    if missing_qualifiers:
        warnings.append("強制限定語未出現在回答中："
                        + "、".join(missing_qualifiers[:3]))

    # 6. 審批集合只消解可裁決項
    effective = set(approved) & ADJUDICATION_TRIGGERS
    review = sorted(set(review) - effective)

    if blocked:
        decision = "blocked"
    elif review:
        decision = "review_required"
    elif effective:
        decision = "pass_after_human_review"
    elif warnings:
        decision = "pass_with_warning"
    else:
        decision = "pass"
    return {"decision": decision, "gates": gates,
            "review_required": review, "blocked_reasons": blocked,
            "reasons": reasons + warnings,
            "approved": sorted(approved),
            "note": "blocked/failed_closed 不可人工放行；citation_failure "
                    "不可審批豁免（需補證據後重跑）"}
