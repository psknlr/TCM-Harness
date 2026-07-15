"""citation.*：引文溯源/首見/反證/轉引（Protocol §9.2）。

citation.trace_quote 的契約自帶誠實邊界：查找**定義語料範圍內**的
時間有序載錄，不用於證明歷史上絕對首次出現。
"""
from __future__ import annotations

from typing import Dict, List

from ..platform import classics_tools, fold_variants
from .contracts import EvidenceContract, ToolContractV2
from ._shared import coverage_from_search, searcher, unavailable


def _trace(**kwargs) -> Dict:
    return classics_tools().t_trace_citation(**kwargs)


def _with_coverage(out: Dict, query_forms: List[str]) -> Dict:
    cov = coverage_from_search(out, query_forms, time_ordered=True)
    counter = out.get("counter_search") or {}
    cov.earlier_partial_candidates = len(
        counter.get("earlier_partial_candidates") or [])
    out["coverage"] = cov.to_dict()
    return out


def t_trace_quote(quote: str, max_scan: int = 300, top: int = 12) -> Dict:
    out = _trace(quote=quote, max_scan=max_scan, top=top)
    if out.get("error") or not out.get("available", True):
        return {**out, "tool": "citation.trace_quote"}
    out["tool"] = "citation.trace_quote"
    return _with_coverage(out, [quote, fold_variants(quote)])


def t_trace_term(term: str, variants: List[str] = None,
                 max_scan: int = 300, top: int = 12) -> Dict:
    """術語（含指定異名）逐一時間有序載錄，合併為單一時間線。"""
    s = searcher()
    if s is None:
        return unavailable("citation.trace_term")
    forms = [term] + [v for v in (variants or []) if v and v != term]
    all_hits: List[Dict] = []
    capped = False
    per_form: List[Dict] = []
    last = None
    for f in forms[:6]:
        r = s.search(query=f, limit=top, per_book=2, max_scan=max_scan,
                     order="dynasty")
        if "error" in r:
            per_form.append({"form": f, "error": r["error"]})
            continue
        last = r
        capped = capped or bool(r.get("scan_capped"))
        for h in r["hits"]:
            all_hits.append({**h, "query_form": f})
        per_form.append({"form": f, "n_hits": r["n_hits"]})
    if last is None:
        # 全部檢索形式都被拒絕：不得產出覆蓋記錄（否則零掃描的
        # exhaustive 覆蓋會為假負結論背書）
        return {"tool": "citation.trace_term", "term": term,
                "per_form": per_form,
                "error": "no_query_form_searchable：所有檢索形式均被拒絕"}
    seen = set()
    merged = []
    for h in sorted(all_hits, key=lambda h: (h["dynasty_rank"],
                                             h["work_id"], h["seq"])):
        if h["passage_id"] in seen:
            continue
        seen.add(h["passage_id"])
        merged.append(h)
    out = {"tool": "citation.trace_term",
           "available": True,
           "term": term, "forms_searched": forms,
           "per_form": per_form,
           "n_attestations": len(merged),
           "attestations_time_ordered": merged[:top],
           "earliest_in_library": merged[0] if merged else None,
           "scan_capped": capped,
           "honesty": "在庫首現≠歷史首現；異名清單以調用方提供為準，"
                      "未提供的別名不會被檢索"}
    if last is not None:
        classics_tools()._attach_evidence(out, s, merged[:top], term)
        out["retrieval_layers"] = last.get("retrieval_layers", {})
    return _with_coverage(out, forms)


def t_counter_search(quote: str, max_scan: int = 300) -> Dict:
    """獨立反證搜索：截半探針找更早部分匹配候選（首見主張的義務工序）。"""
    s = searcher()
    if s is None:
        return unavailable("citation.counter_search")
    flat_q = "".join((quote or "").split())
    if len(flat_q) < 4:
        return {"error": "反證探針至少 4 字"}
    half = max(2, len(flat_q) // 2)
    probes = list(dict.fromkeys((flat_q[:half], flat_q[-half:])))
    candidates: List[Dict] = []
    capped = False
    last = None
    for probe in probes:
        r = s.search(query=probe, limit=8, per_book=1, max_scan=max_scan,
                     order="dynasty")
        if "error" in r:
            continue
        last = r
        capped = capped or bool(r.get("scan_capped"))
        for h in r["hits"]:
            candidates.append({**h, "probe": probe, "match_kind": "partial"})
    out = {"tool": "citation.counter_search", "available": True,
           "quote": quote, "probes": probes,
           "n_candidates": len(candidates),
           "earlier_partial_candidates": candidates[:12],
           "scan_capped": capped,
           "note": "部分匹配候選需人工核驗；存在更早候選時「首見」主張"
                   "被策略引擎攔截"}
    if last is not None:
        out["retrieval_layers"] = last.get("retrieval_layers", {})
        classics_tools()._attach_evidence(out, s, candidates[:8], quote)
    cov = coverage_from_search(out, probes, time_ordered=True,
                               search_modes=["partial", "variant_folded"])
    cov.earlier_partial_candidates = len(candidates)
    out["coverage"] = cov.to_dict()
    return out


def t_detect_relay(quote: str, max_scan: int = 300) -> Dict:
    """轉引檢測：同一文句的時間有序載錄鏈——後出載錄與最早載錄逐字
    重合度高即為轉引候選（是否直接引用/隱引屬人工判定）。"""
    import difflib
    out = t_trace_quote(quote=quote, max_scan=max_scan, top=20)
    if out.get("error") or not out.get("available", True):
        return {**out, "tool": "citation.detect_relay"}
    hits = out.get("attestations_time_ordered") or []
    if len(hits) < 2:
        return {"tool": "citation.detect_relay", "available": True,
                "quote": quote, "n_relay_candidates": 0,
                "relay_chain": [],
                "coverage": out.get("coverage"),
                "note": "載錄不足 2 處，無轉引鏈可言"}
    first = hits[0]
    chain: List[Dict] = []
    for h in hits[1:]:
        ratio = difflib.SequenceMatcher(
            None, first.get("excerpt", ""), h.get("excerpt", "")).ratio()
        chain.append({"from_work": first.get("title"),
                      "to_work": h.get("title"),
                      "to_dynasty": h.get("dynasty"),
                      "to_passage": h.get("passage_id"),
                      "verbatim_similarity": round(ratio, 3),
                      "relay_candidate": ratio >= 0.85})
    return {"tool": "citation.detect_relay", "available": True,
            "quote": quote,
            "earliest": {"work": first.get("title"),
                         "dynasty": first.get("dynasty"),
                         "passage_id": first.get("passage_id")},
            "n_relay_candidates": sum(1 for c in chain
                                      if c["relay_candidate"]),
            "relay_chain": chain,
            "passage_evidence": out.get("passage_evidence", []),
            "coverage": out.get("coverage"),
            "note": "逐字重合度≥0.85 標 relay_candidate；直接引用/隱引/"
                    "共同底本的區分屬人工判定"}


def t_build_citation_network(quote: str, max_scan: int = 300) -> Dict:
    """引文網絡：以載錄鏈構圖（節點=著作，邊=時間先後+相似度）。"""
    relay = t_detect_relay(quote=quote, max_scan=max_scan)
    if relay.get("error") or not relay.get("available", True):
        return {**relay, "tool": "citation.build_citation_network"}
    chain = relay.get("relay_chain") or []
    nodes = sorted({c["from_work"] for c in chain}
                   | {c["to_work"] for c in chain})
    edges = [{"source": c["from_work"], "target": c["to_work"],
              "similarity": c["verbatim_similarity"],
              "relay_candidate": c["relay_candidate"]} for c in chain]
    return {"tool": "citation.build_citation_network", "available": True,
            "quote": quote, "n_nodes": len(nodes), "n_edges": len(edges),
            "nodes": nodes, "edges": edges,
            "coverage": relay.get("coverage"),
            "passage_evidence": relay.get("passage_evidence", [])}


def register(reg) -> None:
    ec = EvidenceContract(
        returns_primary_text=True,
        evidence_role="primary_text_returned",
        minimum_locator=["work_id", "passage_id", "char_start", "char_end"],
        requires_coverage_record=True)
    reg.add(ToolContractV2(
        name="citation.trace_quote",
        description="查找某段原文在定義語料範圍內的時間有序載錄，附反證"
                    "搜索。用於首見、傳播和轉引研究。**不用於**證明歷史上"
                    "絕對首次出現。",
        input_schema={"type": "object", "properties": {
            "quote": {"type": "string", "description": "≥2 字引文"},
            "max_scan": {"type": "integer", "default": 300},
            "top": {"type": "integer", "default": 12}},
            "required": ["quote"]},
        func=t_trace_quote,
        use_when=["用戶詢問首見、最早記載、傳播路徑"],
        do_not_use_when=["僅需解釋術語含義（用 concept.resolve_term）",
                         "未定義語料範圍"],
        evidence_contract=ec,
        failure_modes=["corpus_unavailable", "scan_capped",
                       "ambiguous_work_identity"]))
    reg.add(ToolContractV2(
        name="citation.trace_term",
        description="術語（含異名清單）合併時間線載錄——術語級首見研究。",
        input_schema={"type": "object", "properties": {
            "term": {"type": "string"},
            "variants": {"type": "array", "items": {"type": "string"},
                         "description": "異名/異寫清單（如 賁豚 之於 奔豚）"},
            "max_scan": {"type": "integer", "default": 300},
            "top": {"type": "integer", "default": 12}},
            "required": ["term"]},
        func=t_trace_term,
        use_when=["術語譜系/首見研究（需覆蓋異名變體）"],
        do_not_use_when=["整句引文溯源（用 citation.trace_quote）"],
        evidence_contract=ec,
        failure_modes=["corpus_unavailable", "scan_capped"]))
    reg.add(ToolContractV2(
        name="citation.counter_search",
        description="獨立反證搜索：截半探針找更早部分匹配候選。首見主張"
                    "的**義務工序**——存在更早候選時首見結論被攔截。",
        input_schema={"type": "object", "properties": {
            "quote": {"type": "string"},
            "max_scan": {"type": "integer", "default": 300}},
            "required": ["quote"]},
        func=t_counter_search,
        use_when=["發布任何首見/最早類主張之前"],
        evidence_contract=ec,
        failure_modes=["corpus_unavailable", "scan_capped"]))
    reg.add(ToolContractV2(
        name="citation.detect_relay",
        description="轉引檢測：同一文句載錄鏈的逐字重合度分析。",
        input_schema={"type": "object", "properties": {
            "quote": {"type": "string"},
            "max_scan": {"type": "integer", "default": 300}},
            "required": ["quote"]},
        func=t_detect_relay,
        use_when=["判斷後出著作是否轉引前出著作"],
        evidence_contract=ec,
        failure_modes=["corpus_unavailable", "insufficient_attestations"]))
    reg.add(ToolContractV2(
        name="citation.build_citation_network",
        description="引文網絡構圖：節點=著作，邊=時間先後+逐字相似度。",
        input_schema={"type": "object", "properties": {
            "quote": {"type": "string"},
            "max_scan": {"type": "integer", "default": 300}},
            "required": ["quote"]},
        func=t_build_citation_network,
        use_when=["可視化/導出某文句的傳播網絡"],
        evidence_contract=ec,
        failure_modes=["corpus_unavailable"]))
