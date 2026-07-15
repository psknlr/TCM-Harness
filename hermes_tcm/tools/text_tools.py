"""text.*：段落級檢索與閱讀（Protocol §9.2）。

委托 classics 檢索內核；每次檢索附 SearchCoverage（P0-3），
段落證據以 legacy P 層記錄隨結果攜帶，由 Broker 轉換為
EvidenceRecord V2 後入賬。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..platform import classics_tools
from .contracts import EvidenceContract, ToolContractV2
from ._shared import coverage_from_search, searcher, unavailable


def _read(**kwargs) -> Dict:
    return classics_tools().t_read_passage(**kwargs)


def _search(**kwargs) -> Dict:
    return classics_tools().t_search_passages(**kwargs)


def t_search(query: str = "", any_terms: Optional[List[str]] = None,
             not_terms: Optional[List[str]] = None, near: int = 0,
             category: str = "", dynasty: str = "", author: str = "",
             work: str = "", limit: int = 8, per_book: int = 3,
             max_scan: int = 200, order: str = "relevance") -> Dict:
    out = _search(query=query, any_terms=any_terms, not_terms=not_terms,
                  near=near, category=category, dynasty=dynasty,
                  author=author, work=work, limit=limit, per_book=per_book,
                  max_scan=max_scan, order=order)
    if out.get("error") or not out.get("available", True):
        return {**out, "tool": "text.search_passages"}
    out["tool"] = "text.search_passages"
    cov = coverage_from_search(out, [query] + list(any_terms or []),
                               time_ordered=(order == "dynasty"))
    out["coverage"] = cov.to_dict()
    return out


def t_read(passage_id: str = "", work: str = "", section: str = "",
           max_chars: int = 4000) -> Dict:
    out = _read(passage_id=passage_id, work=work, section=section,
                max_chars=max_chars)
    if out.get("error") or not out.get("available", True):
        return {**out, "tool": "text.read_passage"}
    out["tool"] = "text.read_passage"
    return out


def t_read_context(passage_id: str, window: int = 1) -> Dict:
    """讀取某段落及其前後相鄰段（同卷冊文件內，按 seq 相鄰）。"""
    s = searcher()
    if s is None:
        return unavailable("text.read_context")
    p = s.index.get(passage_id)
    if p is None:
        return {"error": f"未找到段落 {passage_id}"}
    unit = s.lib._by_id[p.work_id]
    from ..platform import passage_evidence
    window = max(0, min(int(window or 1), 3))
    siblings = [x for x in s.index.unit_passages(unit)
                if x.file == p.file and abs(x.seq - p.seq) <= window]
    siblings.sort(key=lambda x: x.seq)
    evs = [passage_evidence(x, unit, 0, len(x.flat_text),
                            retrieval_query=f"context:{passage_id}")
           for x in siblings]
    return {"tool": "text.read_context", "available": True,
            "center": p.locator(),
            "passages": [{"locator": x.locator(),
                          "text": x.flat_text[:2000],
                          "is_center": x.passage_id == passage_id}
                         for x in siblings],
            "passage_evidence": evs}


def t_search_semantic(query: str, category: str = "", dynasty: str = "",
                      work: str = "", limit: int = 8,
                      max_scan: int = 200) -> Dict:
    from ..retrieval.semantic import search_semantic
    return search_semantic(query=query, category=category, dynasty=dynasty,
                           work=work, limit=limit, max_scan=max_scan)


def t_read_section(work: str, section: str = "", max_chars: int = 6000) -> Dict:
    return {**t_read(work=work, section=section, max_chars=max_chars),
            "tool": "text.read_section"}


def t_get_page_alignment(passage_id: str) -> Dict:
    """段落的頁面對齊信息（IIIF locator + 規範化映射）。

    誠實邊界：當前庫是純轉錄文本，無影印頁掃描件——頁碼/葉碼/canvas/
    xywh 如實留空，不編造。字符級座標與 1:1 規範化映射真實可用。"""
    s = searcher()
    if s is None:
        return unavailable("text.get_page_alignment")
    p = s.index.get(passage_id)
    if p is None:
        return {"error": f"未找到段落 {passage_id}"}
    unit = s.lib._by_id[p.work_id]
    from ..corpus.iiif import PassageLocator
    from ..corpus.normalization import three_layer_view
    view = three_layer_view(p.flat_text)
    locator = PassageLocator(section=p.section, char_start=0,
                             char_end=len(p.flat_text))
    return {"tool": "text.get_page_alignment", "available": True,
            "passage_id": passage_id,
            "work": {k: unit[k] for k in ("id", "title", "author",
                                          "dynasty", "category")},
            "locator": locator.to_dict(),
            "alignment_status": "transcription_only",
            "image_alignment": {"iiif_canvas": "", "xywh": "",
                                "folio": "", "page": None,
                                "note": "影印頁對齊需底本掃描件——未對齊"
                                        "字段如實留空，不編造頁碼"},
            "normalization": {
                "map_id": view["normalization_map_id"],
                "version": view["normalization_version"],
                "raw_sha256": view["raw_sha256"],
                "note": "1:1 折疊映射：規範化座標與轉錄座標恆等"}}


def register(reg) -> None:
    text_ec = EvidenceContract(
        returns_primary_text=True,
        evidence_role="primary_text_returned",
        minimum_locator=["work_id", "passage_id", "char_start", "char_end"],
        requires_coverage_record=True)
    # 閱讀類工具按 id 直取，無檢索範圍可言——不要求覆蓋記錄
    # （否則每次成功閱讀都誤發 coverage_missing 護欄事件）
    read_ec = EvidenceContract(
        returns_primary_text=True,
        evidence_role="primary_text_returned",
        minimum_locator=["work_id", "passage_id", "char_start", "char_end"],
        requires_coverage_record=False)
    reg.add(ToolContractV2(
        name="text.search_passages",
        description="全庫段落級布爾檢索（AND/OR/NOT/鄰近窗口），返回段落"
                    "證據（verbatim+座標+quote_hash 可重驗）+ SearchCoverage"
                    "覆蓋記錄。零命中不等於全庫不存在——以 coverage 為準。",
        input_schema={"type": "object", "properties": {
            "query": {"type": "string", "description": "AND 檢索項（空白分詞）"},
            "any_terms": {"type": "array", "items": {"type": "string"}},
            "not_terms": {"type": "array", "items": {"type": "string"}},
            "near": {"type": "integer", "default": 0},
            "category": {"type": "string"}, "dynasty": {"type": "string"},
            "author": {"type": "string"}, "work": {"type": "string"},
            "limit": {"type": "integer", "default": 8},
            "per_book": {"type": "integer", "default": 3},
            "max_scan": {"type": "integer", "default": 200},
            "order": {"type": "string", "description": "relevance|dynasty"}},
            "required": []},
        func=t_search,
        use_when=["按術語/文句在全庫定位段落證據", "為主張取證"],
        do_not_use_when=["需要首見/傳播結論（用 citation.trace_quote）",
                         "只需要書目信息（用 catalog.*）"],
        evidence_contract=text_ec,
        failure_modes=["corpus_unavailable", "scan_capped"]))
    reg.add(ToolContractV2(
        name="text.search_semantic",
        description="近似語義檢索（形式擴展+bigram 召回+RRF+逐字蘊含"
                    "核驗；非向量庫，確定性可重放）。只有 verbatim 蘊含"
                    "核驗通過的片段是證據；lexical_support 命中僅是召回"
                    "信號，引用前須 text.read_passage 取證。",
        input_schema={"type": "object", "properties": {
            "query": {"type": "string", "minLength": 2},
            "category": {"type": "string"}, "dynasty": {"type": "string"},
            "work": {"type": "string"},
            "limit": {"type": "integer", "default": 8},
            "max_scan": {"type": "integer", "default": 200}},
            "required": ["query"]},
        func=t_search_semantic,
        use_when=["精確檢索零命中後的近失召回", "措辭不確定的探索式檢索"],
        do_not_use_when=["查詢形式明確（先用 text.search_passages）"],
        evidence_contract=text_ec,
        failure_modes=["corpus_unavailable", "scan_capped"]))
    reg.add(ToolContractV2(
        name="text.read_passage",
        description="按 passage_id（或 著作+章節）讀整段正文 + 段落證據。",
        input_schema={"type": "object", "properties": {
            "passage_id": {"type": "string"},
            "work": {"type": "string"}, "section": {"type": "string"},
            "max_chars": {"type": "integer", "default": 4000}},
            "required": []},
        func=t_read,
        use_when=["檢索命中後按需讀全文（just-in-time，不把全書塞進上下文）"],
        evidence_contract=read_ec,
        failure_modes=["corpus_unavailable", "passage_not_found"]))
    reg.add(ToolContractV2(
        name="text.read_context",
        description="讀某段落及前後相鄰段（同卷冊內），用於核驗上下文語義。",
        input_schema={"type": "object", "properties": {
            "passage_id": {"type": "string"},
            "window": {"type": "integer", "default": 1, "maximum": 3}},
            "required": ["passage_id"]},
        func=t_read_context,
        use_when=["檢索命中的語境不完整，需要前後文判斷"],
        evidence_contract=read_ec,
        failure_modes=["corpus_unavailable", "passage_not_found"]))
    reg.add(ToolContractV2(
        name="text.read_section",
        description="按著作+章節標題讀取整節。",
        input_schema={"type": "object", "properties": {
            "work": {"type": "string"}, "section": {"type": "string"},
            "max_chars": {"type": "integer", "default": 6000}},
            "required": ["work"]},
        func=t_read_section,
        use_when=["需要整節連續閱讀（如某卷某病篇）"],
        evidence_contract=read_ec,
        failure_modes=["corpus_unavailable", "section_not_found"]))
    meta_ec = EvidenceContract(returns_primary_text=False,
                               evidence_role="metadata_only",
                               minimum_locator=["work_id", "passage_id"])
    reg.add(ToolContractV2(
        name="text.get_page_alignment",
        description="段落的頁面對齊信息：字符座標 + 規範化映射 + IIIF "
                    "locator。純轉錄庫的影像字段如實留空（不編造頁碼）。",
        input_schema={"type": "object", "properties": {
            "passage_id": {"type": "string"}},
            "required": ["passage_id"]},
        func=t_get_page_alignment,
        use_when=["需要段落的定位/座標/規範化映射元數據"],
        do_not_use_when=["需要段落正文（用 text.read_passage）"],
        evidence_contract=meta_ec,
        failure_modes=["corpus_unavailable", "passage_not_found",
                       "no_image_alignment"]))
