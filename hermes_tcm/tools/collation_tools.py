"""collation.*：傳本對照與校勘（Protocol §9.2）。"""
from __future__ import annotations

from typing import Dict, List

from ..corpus.tei import ApparatusEntry, Reading, export_tei_document
from ..core.identity import witness_urn
from ..platform import classics_tools
from .contracts import EvidenceContract, ToolContractV2
from ._shared import searcher, unavailable, work_registry


def _compare(**kwargs) -> Dict:
    return classics_tools().t_compare_witnesses(**kwargs)


def t_align_witnesses(work: str, query: str = "", limit: int = 6) -> Dict:
    out = _compare(work=work, query=query, limit=limit)
    if out.get("error") or not out.get("available", True):
        return {**out, "tool": "collation.align_witnesses"}
    out["tool"] = "collation.align_witnesses"
    # 身份鏈升級：附 witness URN（同名異書消歧狀態一并透出）
    reg = work_registry()
    if reg is not None:
        for w in out.get("witnesses", []):
            rec = reg.witness_for_unit(w.get("id", ""))
            if rec:
                w["witness_id"] = rec.witness_id
                w["work_urn"] = rec.work_id
                w["source_type"] = rec.source_type
    return out


def t_list_variants(work: str, query: str, limit: int = 6) -> Dict:
    """探針詞在各傳本的異文清單（成對差異）。"""
    out = t_align_witnesses(work=work, query=query, limit=limit)
    if out.get("error") or not out.get("available", True):
        return {**out, "tool": "collation.list_variants"}
    probes = out.get("probe_hits") or []
    variants: List[Dict] = []
    for i in range(len(probes)):
        for j in range(i + 1, len(probes)):
            a, b = probes[i], probes[j]
            if a.get("excerpt") != b.get("excerpt"):
                variants.append({
                    "witness_a": a.get("work_id"),
                    "witness_b": b.get("work_id"),
                    "reading_a": a.get("excerpt", ""),
                    "reading_b": b.get("excerpt", ""),
                    "passage_a": a.get("passage_id"),
                    "passage_b": b.get("passage_id")})
    return {"tool": "collation.list_variants", "available": True,
            "work": work, "probe_query": query,
            "n_variant_pairs": len(variants), "variants": variants,
            "passage_evidence": out.get("passage_evidence", []),
            "note": "字符級異文由探針段對照派生；系統性 apparatus 需"
                    "collation.export_tei_apparatus"}


def t_compare_passages(passage_ids: List[str]) -> Dict:
    """任意段落兩兩對照（difflib 相似度 + 差異片段）。"""
    import difflib
    s = searcher()
    if s is None:
        return unavailable("collation.compare_passages")
    if not passage_ids or len(passage_ids) < 2:
        return {"error": "至少提供 2 個 passage_id"}
    ps = []
    for pid in passage_ids[:6]:
        p = s.index.get(pid)
        if p is None:
            return {"error": f"未找到段落 {pid}"}
        ps.append(p)
    pairs = []
    for i in range(len(ps)):
        for j in range(i + 1, len(ps)):
            a, b = ps[i], ps[j]
            sm = difflib.SequenceMatcher(None, a.flat_text[:2000],
                                         b.flat_text[:2000])
            diffs = [{"op": op, "a": a.flat_text[i1:i2][:40],
                      "b": b.flat_text[j1:j2][:40]}
                     for op, i1, i2, j1, j2 in sm.get_opcodes()
                     if op != "equal"][:10]
            pairs.append({"a": a.passage_id, "b": b.passage_id,
                          "similarity": round(sm.ratio(), 3),
                          "diffs": diffs})
    from ..platform import passage_evidence
    evs = [passage_evidence(p, s.lib._by_id[p.work_id], 0,
                            min(len(p.flat_text), 120),
                            retrieval_query="compare")
           for p in ps]
    return {"tool": "collation.compare_passages", "available": True,
            "n_passages": len(ps), "pairs": pairs,
            "passage_evidence": evs}


def t_export_tei_apparatus(work: str, query: str, limit: int = 6) -> Dict:
    """探針對照 → TEI P5 critical apparatus（app/lem/rdg）。"""
    out = t_align_witnesses(work=work, query=query, limit=limit)
    if out.get("error") or not out.get("available", True):
        return {**out, "tool": "collation.export_tei_apparatus"}
    probes = out.get("probe_hits") or []
    if not probes:
        return {"error": f"探針詞「{query}」在《{work}》各傳本無命中，"
                         "無法生成 apparatus"}
    readings = [Reading(witness_id=witness_urn(h["work_id"]),
                        text=h.get("excerpt", ""),
                        is_lemma=(idx == 0))
                for idx, h in enumerate(probes)]
    entry = ApparatusEntry(app_id=f"app_{probes[0].get('passage_id', 'x')}",
                           readings=readings,
                           location=f"probe:{query}")
    witnesses = [{"witness_id": witness_urn(h["work_id"]),
                  "title": h.get("title", h.get("work_id", ""))}
                 for h in probes]
    xml = export_tei_document(f"{work}（apparatus）", witnesses, [entry])
    return {"tool": "collation.export_tei_apparatus", "available": True,
            "work": work, "probe_query": query,
            "tei_xml": xml,
            "passage_evidence": out.get("passage_evidence", []),
            "note": "lemma 取第一個命中傳本（探針序），正式校勘的底本"
                    "選擇屬專家審批範圍"}


def register(reg) -> None:
    ec = EvidenceContract(returns_primary_text=True,
                          evidence_role="primary_text_returned",
                          minimum_locator=["work_id", "passage_id"])
    reg.add(ToolContractV2(
        name="collation.align_witnesses",
        description="同一著作各傳本對照：傳本清單（含 witness URN/"
                    "source_type）+ 探針詞命中段對照與兩兩相似度。",
        input_schema={"type": "object", "properties": {
            "work": {"type": "string"}, "query": {"type": "string"},
            "limit": {"type": "integer", "default": 6}},
            "required": ["work"]},
        func=t_align_witnesses,
        use_when=["傳本比較/校勘任務的第一步"],
        do_not_use_when=["只需單一傳本正文（用 text.read_passage）"],
        evidence_contract=ec,
        failure_modes=["corpus_unavailable", "work_not_found"]))
    reg.add(ToolContractV2(
        name="collation.list_variants",
        description="探針詞在各傳本的異文成對清單。",
        input_schema={"type": "object", "properties": {
            "work": {"type": "string"}, "query": {"type": "string"},
            "limit": {"type": "integer", "default": 6}},
            "required": ["work", "query"]},
        func=t_list_variants,
        use_when=["需要異文差異明細而非整段對照"],
        evidence_contract=ec,
        failure_modes=["corpus_unavailable", "work_not_found"]))
    reg.add(ToolContractV2(
        name="collation.compare_passages",
        description="任意 2-6 個段落兩兩對照：相似度 + 差異片段。",
        input_schema={"type": "object", "properties": {
            "passage_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["passage_ids"]},
        func=t_compare_passages,
        use_when=["跨書段落比較（如轉引核驗）"],
        evidence_contract=ec,
        failure_modes=["corpus_unavailable", "passage_not_found"]))
    reg.add(ToolContractV2(
        name="collation.export_tei_apparatus",
        description="傳本對照 → TEI P5 critical apparatus（app/lem/rdg）"
                    "XML 導出。",
        input_schema={"type": "object", "properties": {
            "work": {"type": "string"}, "query": {"type": "string"},
            "limit": {"type": "integer", "default": 6}},
            "required": ["work", "query"]},
        func=t_export_tei_apparatus,
        use_when=["把校勘結果導出為 TEI 標準格式"],
        evidence_contract=ec,
        failure_modes=["corpus_unavailable", "no_probe_hits"]))
