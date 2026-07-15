"""evidence.* / claim.*：證據包與主張工具（Protocol §9.2）。"""
from __future__ import annotations

from typing import Dict, List

from ..platform import classics_tools
from ..claims.counterevidence import counter_search_obligations
from ..claims.records import CLAIM_TYPES, ClaimRecord, claim_id_for
from .contracts import EvidenceContract, ToolContractV2


def _export_packet(passage_ids: List[str], topic: str = "") -> Dict:
    return classics_tools().t_export_evidence_packet(
        passage_ids=passage_ids, topic=topic)


def t_build_packet(passage_ids: List[str], topic: str = "") -> Dict:
    out = _export_packet(passage_ids=passage_ids, topic=topic)
    if isinstance(out, dict):
        out = dict(out)
        out["tool"] = "evidence.build_packet"
    return out


def t_verify_packet(passage_ids: List[str], topic: str = "") -> Dict:
    """重驗一組段落證據（重新物化並逐字對照）。"""
    out = _export_packet(passage_ids=passage_ids, topic=topic)
    if out.get("error") or not out.get("available", True):
        return {**out, "tool": "evidence.verify_packet"}
    packet = out.get("packet") or {}
    return {"tool": "evidence.verify_packet", "available": True,
            "packet_id": packet.get("packet_id"),
            "verification": packet.get("verification"),
            "n_records": packet.get("n_records", 0),
            "missing_passage_ids": out.get("missing_passage_ids", []),
            "library_fingerprint": packet.get("library_fingerprint", "")}


def t_claim_compile(claim_text: str, claim_type: str,
                    supporting_evidence: List[str] = None,
                    coverage_id: str = "") -> Dict:
    """把一條主張物化為 ClaimRecord 草稿（核驗由 claim.verify 完成）。"""
    if claim_type not in CLAIM_TYPES:
        return {"error": f"非法 claim_type：{claim_type}",
                "available_types": list(CLAIM_TYPES)}
    claim = ClaimRecord(
        claim_id=claim_id_for(claim_text, claim_type),
        claim_text=claim_text,
        claim_type=claim_type,
        scope_id=coverage_id,
        supporting_evidence=list(supporting_evidence or []))
    return {"tool": "claim.compile", "available": True,
            "claim": claim.to_dict(),
            "note": "draft 狀態：須經 claim.verify 核驗後才能進入綜合表達"}


def t_claim_verify(claim_text: str, claim_type: str,
                   supporting_passage_ids: List[str] = None,
                   counter_search_performed: bool = False,
                   coverage: Dict = None) -> Dict:
    """自包含主張核驗：按 passage_id 重新物化證據 → 臨時台賬 →
    ClaimVerifier 四項核驗 + 策略引擎裁定。

    這是無狀態工具面的核驗入口；run 內核驗由 claim_verify 節點承擔
    （其台賬證據帶完整 Broker 綁定）。"""
    from ..claims.records import ClaimRecord, claim_id_for
    from ..claims.verifier import ClaimVerifier
    from ..evidence.coverage import SearchCoverage
    from ..evidence.ledger import TypedEvidenceLedger, mint_broker_token
    from ..evidence.records import from_legacy_p_record
    if claim_type not in CLAIM_TYPES:
        return {"error": f"非法 claim_type：{claim_type}",
                "available_types": list(CLAIM_TYPES)}
    pids = list(dict.fromkeys(supporting_passage_ids or []))[:20]
    materialized = _export_packet(passage_ids=pids, topic="claim.verify") \
        if pids else {"passage_evidence": [], "available": True}
    if materialized.get("error") or \
            materialized.get("available", True) is False:
        return {**materialized, "tool": "claim.verify"}
    ledger = TypedEvidenceLedger("")
    tok = mint_broker_token("capability_broker")
    ev_ids: List[str] = []
    for rec in materialized.get("passage_evidence", []):
        try:
            from ._shared import work_registry
            v2 = from_legacy_p_record(rec, corpus_version="",
                                      work_registry=work_registry())
        except ValueError as exc:
            return {"tool": "claim.verify",
                    "error": f"證據物化失敗：{exc}"}
        v2.tool_call_id = "claim.verify"
        v2.span_id = "claim.verify"
        v2.registered_by = "capability_broker"
        ledger.register("verify", v2, tok)
        ev_ids.append(v2.evidence_id)
    claim = ClaimRecord(
        claim_id=claim_id_for(claim_text, claim_type),
        claim_text=claim_text, claim_type=claim_type,
        supporting_evidence=ev_ids,
        counter_search_performed=bool(counter_search_performed))
    cov = None
    if isinstance(coverage, dict) and coverage.get("coverage_id"):
        try:
            cov = SearchCoverage.from_dict(coverage)
        except (TypeError, ValueError) as exc:
            return {"tool": "claim.verify",
                    "error": f"覆蓋記錄非法：{exc}"}
    verifier = ClaimVerifier(ledger)
    verifier.verify(claim, coverage=cov)
    return {"tool": "claim.verify", "available": True,
            "claim": claim.to_dict(),
            "status": claim.status,
            "n_evidence_materialized": len(ev_ids),
            "missing_passage_ids": materialized.get("missing_passage_ids",
                                                    []),
            "note": "無狀態核驗：工具契約類條款（minimum_tools）按未執行"
                    "評估——run 內核驗以 claim_verify 節點為權威"}


def t_claim_find_counterevidence(claim_text: str, claim_type: str,
                                 query_forms: List[str]) -> Dict:
    """主張的反證搜索義務清單（由 counterevidence 節點逐項執行）。"""
    if claim_type not in CLAIM_TYPES:
        return {"error": f"非法 claim_type：{claim_type}",
                "available_types": list(CLAIM_TYPES)}
    claim = ClaimRecord(claim_id=claim_id_for(claim_text, claim_type),
                        claim_text=claim_text, claim_type=claim_type)
    obligations = counter_search_obligations(claim, list(query_forms or []))
    return {"tool": "claim.find_counterevidence", "available": True,
            "claim_id": claim.claim_id,
            "n_obligations": len(obligations),
            "obligations": obligations,
            "note": "義務清單必須逐項執行並回填覆蓋記錄，"
                    "counter_search_performed 才能置真"}


def register(reg) -> None:
    ec = EvidenceContract(returns_primary_text=True,
                          evidence_role="primary_text_returned",
                          minimum_locator=["work_id", "passage_id",
                                           "char_start", "char_end"])
    meta_ec = EvidenceContract(returns_primary_text=False,
                               evidence_role="metadata_only")
    reg.add(ToolContractV2(
        name="evidence.build_packet",
        description="按 passage_id 物化證據包（整段記錄 + 逐字重驗 + "
                    "庫指紋）——論文/審計/跨代理傳遞可直接引用。",
        input_schema={"type": "object", "properties": {
            "passage_ids": {"type": "array", "items": {"type": "string"}},
            "topic": {"type": "string"}},
            "required": ["passage_ids"]},
        func=t_build_packet,
        use_when=["把檢索結果固化為可傳遞的證據包"],
        evidence_contract=ec,
        failure_modes=["corpus_unavailable", "passage_not_found"]))
    reg.add(ToolContractV2(
        name="evidence.verify_packet",
        description="重驗一組段落證據（重新物化並逐字對照+quote_hash）。",
        input_schema={"type": "object", "properties": {
            "passage_ids": {"type": "array", "items": {"type": "string"}},
            "topic": {"type": "string"}},
            "required": ["passage_ids"]},
        func=t_verify_packet,
        use_when=["發布前/審計時重驗證據完整性"],
        evidence_contract=meta_ec,
        failure_modes=["corpus_unavailable", "verification_failed"]))
    reg.add(ToolContractV2(
        name="claim.compile",
        description="把一條主張物化為 ClaimRecord 草稿（綁定證據 id 與"
                    "覆蓋記錄）。",
        input_schema={"type": "object", "properties": {
            "claim_text": {"type": "string"},
            "claim_type": {"type": "string",
                           "enum": list(CLAIM_TYPES)},
            "supporting_evidence": {"type": "array",
                                    "items": {"type": "string"}},
            "coverage_id": {"type": "string"}},
            "required": ["claim_text", "claim_type"]},
        func=t_claim_compile,
        use_when=["形成結構化主張（先 claims 後 prose）"],
        evidence_contract=meta_ec,
        failure_modes=["invalid_claim_type"]))
    reg.add(ToolContractV2(
        name="claim.verify",
        description="自包含主張核驗：按 passage_id 重新物化證據並跑"
                    "attribution/quotation/semantic/coverage 四項 + 策略"
                    "引擎裁定。run 內核驗以 claim_verify 節點為權威。",
        input_schema={"type": "object", "properties": {
            "claim_text": {"type": "string"},
            "claim_type": {"type": "string", "enum": list(CLAIM_TYPES)},
            "supporting_passage_ids": {"type": "array",
                                       "items": {"type": "string"}},
            "counter_search_performed": {"type": "boolean",
                                         "default": False},
            "coverage": {"type": "object"}},
            "required": ["claim_text", "claim_type"]},
        func=t_claim_verify,
        use_when=["核驗一條既有主張（審讀/複核場景）"],
        do_not_use_when=["研究 run 內部（用 claim_verify 節點）"],
        evidence_contract=meta_ec,
        failure_modes=["invalid_claim_type", "corpus_unavailable",
                       "passage_not_found"]))
    reg.add(ToolContractV2(
        name="claim.find_counterevidence",
        description="生成主張的反證搜索義務清單（查什麼/用什麼工具/"
                    "什麼結果推翻主張）。",
        input_schema={"type": "object", "properties": {
            "claim_text": {"type": "string"},
            "claim_type": {"type": "string", "enum": list(CLAIM_TYPES)},
            "query_forms": {"type": "array", "items": {"type": "string"}}},
            "required": ["claim_text", "claim_type", "query_forms"]},
        func=t_claim_find_counterevidence,
        use_when=["counterevidence_search 節點編排反證工序"],
        evidence_contract=meta_ec,
        failure_modes=["invalid_claim_type"]))
