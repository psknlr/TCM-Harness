"""反證義務（Protocol §8 / §11.1 Counterevidence Critic 的確定性內核）。

按 claim_type 生成主動反證搜索義務：查什麼、用什麼工具、
什麼結果會推翻主張。義務清單是數據，由 counterevidence_search
節點逐項執行並回填 SearchCoverage。
"""
from __future__ import annotations

from typing import Dict, List

from .records import ClaimRecord


def counter_search_obligations(claim: ClaimRecord,
                               query_forms: List[str]) -> List[Dict]:
    """claim → 反證搜索義務清單。query_forms 是主張涉及的檢索詞
    （含異體變形）。"""
    obligations: List[Dict] = []
    if claim.claim_type == "earliest_attestation":
        for q in query_forms:
            # 截半探針僅對 ≥8 字引文有意義（與 citation 內核同一口徑：
            # 短術語的半探針是噪聲，反證由異體變形時間線承擔）
            if len(q) >= 8:
                obligations.append({
                    "kind": "earlier_partial_match",
                    "tool": "citation.counter_search",
                    "query": q,
                    "note": "截半探針找更早部分匹配候選——存在更早部分"
                            "匹配時不得發布「首見」"})
        obligations.append({
            "kind": "variant_form_search",
            "tool": "citation.trace_term",
            "query": "|".join(query_forms),
            "note": "異體/別名變形逐一時間有序檢索"})
    elif claim.claim_type == "broad_consensus":
        obligations.append({
            "kind": "dissenting_source",
            "tool": "text.search_passages",
            "query": " ".join(query_forms[:2]),
            "note": "搜索持異議著作——「普遍認為」必須主動找反例"})
    elif claim.claim_type in ("semantic_drift", "formula_lineage",
                              "quotation_relay"):
        obligations.append({
            "kind": "alternative_lineage",
            "tool": "citation.trace_quote",
            "query": query_forms[0] if query_forms else "",
            "note": "檢查是否存在並行傳承/獨立來源（推翻單線譜系）"})
    elif claim.claim_type == "negative_result":
        obligations.append({
            "kind": "recall_probe",
            "tool": "text.search_passages",
            "query": query_forms[0] if query_forms else "",
            "note": "用部分匹配/異體折疊復查——負結論的反證就是任何命中"})
    return obligations
