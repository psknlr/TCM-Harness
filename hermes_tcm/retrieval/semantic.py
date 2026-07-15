"""semantic：確定性近似語義召回 + 逐字蘊含核驗（P1 實裝）。

不是向量庫：純標準庫、確定性、可重放的召回棧——

    查詢形式擴展（原式 + 異體折疊 + 領域實體規範名）
    → 字符 bigram 分解 OR 召回（近失段落也能進候選池）
    → 多路 RRF 融合 + bigram 覆蓋率重排（lexical rerank）
    → 逐字蘊含核驗（entailment gate）：
        verbatim         某一查詢形式在段落中逐字出現（1:1 折疊座標）
        lexical_support  查詢 bigram 覆蓋率 ≥ 閾值（僅召回信號）
    → SearchCoverage 覆蓋記錄

證據不變量（審計方案的核心約束，照單全收）：**召回命中只是信號，
不是證據**。只有通過 verbatim 蘊含核驗的片段才構造 passage_evidence
（verbatim+座標+quote_hash 可重驗，經 Broker 入 V2 台賬）；
lexical_support 命中如實標注 evidence_role=recall_signal，不入台賬，
引用它們的主張過不了 Claim Verifier。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..platform import fold_variants, passage_evidence
from .fusion import fuse_rrf
from .lexical import rerank_lexical

# lexical_support 的 bigram 覆蓋率下界（低於此值視為噪聲直接丟棄；
# 1/3 允許「四字查詢命中其中一個雙字核心詞」這類真實近失信號）
MIN_BIGRAM_OVERLAP = 0.30
MAX_QUERY_FORMS = 4
MAX_OR_TERMS = 8


def _flat(text: str) -> str:
    return "".join((text or "").split())


def _bigrams(text: str) -> List[str]:
    t = _flat(text)
    if len(t) < 2:
        return [t] if t else []
    return list(dict.fromkeys(t[i:i + 2] for i in range(len(t) - 1)))


def _query_forms(query: str) -> List[str]:
    forms = [query, fold_variants(query)]
    try:
        from ..domains.registry import link_domain_entities
        forms.extend(e["name"] for e in link_domain_entities(query))
    except Exception:
        pass
    return [f for f in dict.fromkeys(_flat(f) for f in forms)
            if f][:MAX_QUERY_FORMS]


def search_semantic(query: str, category: str = "", dynasty: str = "",
                    work: str = "", limit: int = 8,
                    max_scan: int = 200) -> Dict:
    """近似語義檢索。返回 hits（帶 entailment 標注）+ 僅含 verbatim
    核驗通過片段的 passage_evidence + 覆蓋記錄。"""
    from ..tools._shared import coverage_from_search, searcher
    s = searcher()
    if s is None:
        return {"error": "corpus_unavailable", "tool_hint": "semantic"}
    query = (query or "").strip()
    if len(_flat(query)) < 2:
        return {"error": "查詢至少 2 字"}
    forms = _query_forms(query)
    grams = _bigrams(query)[:MAX_OR_TERMS]

    # 1. 多路召回：每個查詢形式一路精確 AND；一路 bigram OR（近失召回）
    ranked_lists: List[List[Dict]] = []
    last = None
    capped = False
    for f in forms:
        r = s.search(query=f, category=category, dynasty=dynasty, work=work,
                     limit=max(limit, 8), per_book=3, max_scan=max_scan,
                     order="relevance")
        if "error" in r:
            continue
        last = r
        capped = capped or bool(r.get("scan_capped"))
        ranked_lists.append(r["hits"])
    if grams:
        r = s.search(query="", any_terms=grams, category=category,
                     dynasty=dynasty, work=work, limit=max(limit * 3, 16),
                     per_book=3, max_scan=max_scan, order="relevance")
        if "error" not in r:
            last = last or r
            capped = capped or bool(r.get("scan_capped"))
            ranked_lists.append(r["hits"])
    if last is None:
        return {"error": "no_recall_path：全部召回路徑被拒絕"}

    # 2. 融合 + 詞彙重排（確定性）
    fused = fuse_rrf(ranked_lists, key="passage_id", limit=limit * 4)
    fused = rerank_lexical(query, fused, text_key="excerpt")

    # 3. 逐字蘊含核驗（evidence gate）：折疊是 1:1 映射（normalization
    # 不變量），折疊文本座標即原文座標，可直接構造可重驗證據
    qset = set(grams)
    hits: List[Dict] = []
    evidence: List[Dict] = []
    for h in fused:
        p = s.index.get(h.get("passage_id", ""))
        if p is None:
            continue
        folded_text = fold_variants(p.flat_text)
        entail = ""
        matched_form = ""
        for f in forms:
            pos = folded_text.find(f)
            if pos != -1:
                entail = "verbatim"
                matched_form = f
                start, end = pos, pos + len(f)
                break
        if not entail:
            overlap = 0.0
            if qset:
                pgrams = set(_bigrams(p.flat_text)) \
                    | set(_bigrams(folded_text))
                overlap = len(qset & pgrams) / len(qset)
            if overlap < MIN_BIGRAM_OVERLAP:
                continue        # 噪聲：既無逐字蘊含也無足夠詞彙覆蓋
            entail = "lexical_support"
        row = {**h, "entailment": entail}
        if entail == "verbatim":
            row["matched_form"] = matched_form
            row["evidence_role"] = "primary_text_returned"
            unit = s.lib._by_id.get(p.work_id)
            if unit is not None:
                evidence.append(passage_evidence(
                    p, unit, start, end,
                    retrieval_query=f"semantic:{query}"))
        else:
            row["evidence_role"] = "recall_signal"
            row["note"] = ("召回信號非證據：正文未逐字蘊含查詢形式，"
                           "引用前須經 text.read_passage 取證")
        hits.append(row)
        if len(hits) >= limit:
            break

    out = {"tool": "text.search_semantic", "available": True,
           "query": query, "query_forms": forms,
           "recall_terms": grams,
           "n_hits": len(hits),
           "n_verbatim": sum(1 for h in hits
                             if h["entailment"] == "verbatim"),
           "n_recall_signals": sum(1 for h in hits
                                   if h["entailment"] == "lexical_support"),
           "hits": hits,
           "passage_evidence": evidence,
           "scan_capped": capped,
           "retrieval_layers": last.get("retrieval_layers", {}),
           "honesty": "近似語義召回（bigram/異體/實體擴展，非向量庫）；"
                      "只有 verbatim 蘊含核驗通過的片段是證據，"
                      "lexical_support 命中僅是召回信號"}
    cov = coverage_from_search(
        out, forms,
        search_modes=["exact", "variant_folded", "ngram_or_recall",
                      "verbatim_entailment_gate"])
    out["coverage"] = cov.to_dict()
    return out
