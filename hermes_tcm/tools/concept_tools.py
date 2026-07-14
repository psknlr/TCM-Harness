"""concept.*：術語與概念工具（Protocol §9.2）。"""
from __future__ import annotations

from typing import Dict

from hermes_shanghan.classics.tools import (t_concept_drift as _drift,
                                            t_resolve_term as _resolve)

from .contracts import EvidenceContract, ToolContractV2
from ._shared import coverage_from_search


def t_resolve_term(term: str, max_scan: int = 120) -> Dict:
    out = _resolve(term=term, max_scan=max_scan)
    if out.get("error") or not out.get("available", True):
        return {**out, "tool": "concept.resolve_term"}
    out["tool"] = "concept.resolve_term"
    return out


def t_drift(term: str, category: str = "", max_scan: int = 300) -> Dict:
    out = _drift(term=term, category=category, max_scan=max_scan)
    if out.get("error") or not out.get("available", True):
        return {**out, "tool": "concept.drift"}
    out["tool"] = "concept.drift"
    cov = coverage_from_search(out, [term], time_ordered=True)
    out["coverage"] = cov.to_dict()
    # 必須避免的錯誤之四的結構化聲明：本工具產出的是頻次分佈，
    # 不能單獨支持語義漂移主張（策略引擎 forbid_frequency_only 執行）
    out["claim_constraint"] = {
        "supports": "frequency_distribution",
        "does_not_support": "semantic_drift_without_passage_evidence"}
    return out


def register(reg) -> None:
    ec = EvidenceContract(returns_primary_text=True,
                          evidence_role="primary_text_returned",
                          minimum_locator=["work_id", "passage_id"])
    reg.add(ToolContractV2(
        name="concept.resolve_term",
        description="術語解析：異體字折疊形、變體字符、全庫出現概況"
                    "（分類/朝代分佈）。",
        input_schema={"type": "object", "properties": {
            "term": {"type": "string"},
            "max_scan": {"type": "integer", "default": 120}},
            "required": ["term"]},
        func=t_resolve_term,
        use_when=["解釋術語寫法/分佈", "檢索前確定異體折疊形"],
        do_not_use_when=["需要首見結論（用 citation.trace_term）"],
        evidence_contract=ec,
        failure_modes=["corpus_unavailable"]))
    reg.add(ToolContractV2(
        name="concept.drift",
        description="概念漂移計量：術語按朝代分桶的頻次分佈。**頻次漂移"
                    "≠語義漂移**——本工具結果不能單獨支持語義演變主張。",
        input_schema={"type": "object", "properties": {
            "term": {"type": "string"}, "category": {"type": "string"},
            "max_scan": {"type": "integer", "default": 300}},
            "required": ["term"]},
        func=t_drift,
        use_when=["觀察術語跨朝代出現趨勢（分佈證據）"],
        do_not_use_when=["直接下語義演變結論（需段落級證據+人工判讀）"],
        evidence_contract=ec,
        failure_modes=["corpus_unavailable", "scan_capped"]))
