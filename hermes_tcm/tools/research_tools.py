"""research.*：研究導出（Protocol §9.2）。

導出對象是 AnswerEnvelope / EvidencePacket 結構——版本化研究導出包，
所有引用帶穩定 URN 與庫指紋。
"""
from __future__ import annotations

import json
from typing import Dict, List

from .contracts import EvidenceContract, ToolContractV2


def t_create_bundle(title: str, claims: List[Dict] = None,
                    evidence: List[Dict] = None,
                    coverage: Dict = None) -> Dict:
    """研究束：claims + evidence + coverage 的自包含導出對象。"""
    import hashlib
    claims = list(claims or [])
    evidence = list(evidence or [])
    body = json.dumps({"claims": claims, "evidence": evidence},
                      ensure_ascii=False, sort_keys=True)
    bundle_id = "bnd_" + hashlib.sha256(
        f"{title}\0{body}".encode("utf-8")).hexdigest()[:12]
    return {"tool": "research.create_bundle", "available": True,
            "bundle": {"bundle_id": bundle_id, "title": title,
                       "claims": claims, "evidence": evidence,
                       "coverage": coverage or {},
                       "n_claims": len(claims),
                       "n_evidence": len(evidence)}}


def t_export_markdown(bundle: Dict) -> Dict:
    b = bundle or {}
    lines = [f"# {b.get('title', '（無題）')}", "",
             f"- bundle：{b.get('bundle_id', '')}", "", "## 主張", ""]
    for c in b.get("claims", []):
        status = c.get("status", "draft")
        lines.append(f"- **[{status}]** {c.get('claim_text', '')}"
                     f"（證據：{'、'.join(c.get('supporting_evidence', []))}）")
        for q in c.get("forced_qualifiers", []):
            lines.append(f"  - 限定：{q}")
    lines += ["", "## 證據", ""]
    for e in b.get("evidence", []):
        lines.append(f"- `{e.get('evidence_id', '')}` "
                     f"《{e.get('work_title', '')}》{e.get('section', '')}："
                     f"「{(e.get('verbatim') or '')[:60]}」")
    cov = b.get("coverage") or {}
    if cov:
        lines += ["", "## 檢索範圍", "",
                  f"- coverage：{cov.get('coverage_id', '')}",
                  f"- 掃描：{cov.get('works_scanned', 0)}/"
                  f"{cov.get('candidate_works', 0)} 部候選",
                  f"- 封頂：{cov.get('scan_capped', False)}"]
    return {"tool": "research.export_markdown", "available": True,
            "markdown": "\n".join(lines)}


def t_export_jsonld(bundle: Dict) -> Dict:
    b = bundle or {}
    graph = []
    for c in b.get("claims", []):
        graph.append({"@id": c.get("claim_id", ""),
                      "@type": "tcm:Claim",
                      "tcm:text": c.get("claim_text", ""),
                      "tcm:status": c.get("status", ""),
                      "tcm:supportedBy": [{"@id": e} for e in
                                          c.get("supporting_evidence", [])]})
    for e in b.get("evidence", []):
        graph.append({"@id": e.get("evidence_id", ""),
                      "@type": "tcm:Evidence",
                      "tcm:work": e.get("work_id", ""),
                      "tcm:witness": e.get("witness_id", ""),
                      "tcm:passage": e.get("passage_id", ""),
                      "tcm:verbatim": e.get("verbatim", ""),
                      "tcm:quoteHash": e.get("quote_hash", "")})
    return {"tool": "research.export_jsonld", "available": True,
            "jsonld": {"@context": {"tcm": "urn:tcm:vocab:"},
                       "@graph": graph}}


def t_export_tei(bundle: Dict) -> Dict:
    """研究束 → TEI 文檔：每條證據一個 quote 段（帶 witness 出處），
    主張為 interpGrp。純標準庫 XML 生成。"""
    from xml.sax.saxutils import escape, quoteattr
    b = bundle or {}
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<TEI xmlns="http://www.tei-c.org/ns/1.0">',
             "  <teiHeader><fileDesc><titleStmt>",
             f"    <title>{escape(b.get('title', '（無題）'))}</title>",
             "  </titleStmt><sourceDesc><p>Hermes-TCM research bundle "
             f"{escape(b.get('bundle_id', ''))}</p></sourceDesc>"
             "</fileDesc></teiHeader>",
             "  <text><body>",
             '    <div type="claims"><interpGrp>']
    for c in b.get("claims", []):
        parts.append(
            f"      <interp xml:id={quoteattr(c.get('claim_id', 'clm'))}"
            f" ana={quoteattr('#' + c.get('status', 'draft'))}>"
            f"{escape(c.get('claim_text') or c.get('text', ''))}</interp>")
    parts.append("    </interpGrp></div>")
    parts.append('    <div type="evidence">')
    for e in b.get("evidence", []):
        source = f"《{e.get('work_title', '')}》{e.get('section', '')}"
        parts.append(
            f"      <cit xml:id={quoteattr(e.get('evidence_id', 'ev'))}>"
            f"<quote>{escape(e.get('verbatim', ''))}</quote>"
            f"<bibl corresp={quoteattr(e.get('witness_id', ''))}>"
            f"{escape(source)}</bibl></cit>")
    parts += ["    </div>", "  </body></text>", "</TEI>"]
    return {"tool": "research.export_tei", "available": True,
            "tei_xml": "\n".join(parts)}


def t_export_bibtex(bundle: Dict) -> Dict:
    b = bundle or {}
    entries = []
    seen = set()
    for e in b.get("evidence", []):
        key = e.get("work_id") or e.get("work_title", "")
        if not key or key in seen:
            continue
        seen.add(key)
        cite_key = (e.get("work_id", "") or "work").split(":")[-1]
        entries.append(
            "@book{" + cite_key + ",\n"
            f"  title = {{{e.get('work_title', '')}}},\n"
            f"  author = {{{e.get('author', '')}}},\n"
            f"  note = {{朝代：{e.get('dynasty', '')}；"
            f"witness：{e.get('witness_id', '')}}}\n}}")
    return {"tool": "research.export_bibtex", "available": True,
            "bibtex": "\n\n".join(entries)}


def register(reg) -> None:
    meta_ec = EvidenceContract(returns_primary_text=False,
                               evidence_role="metadata_only")
    reg.add(ToolContractV2(
        name="research.create_bundle",
        description="把 claims+evidence+coverage 固化為自包含研究束。",
        input_schema={"type": "object", "properties": {
            "title": {"type": "string"},
            "claims": {"type": "array", "items": {"type": "object"}},
            "evidence": {"type": "array", "items": {"type": "object"}},
            "coverage": {"type": "object"}},
            "required": ["title"]},
        func=t_create_bundle,
        use_when=["研究結束後導出可審計成果包"],
        evidence_contract=meta_ec, failure_modes=[]))
    reg.add(ToolContractV2(
        name="research.export_markdown",
        description="研究束 → Markdown 報告。",
        input_schema={"type": "object", "properties": {
            "bundle": {"type": "object"}}, "required": ["bundle"]},
        func=t_export_markdown,
        use_when=["人類可讀導出"], evidence_contract=meta_ec,
        failure_modes=[]))
    reg.add(ToolContractV2(
        name="research.export_jsonld",
        description="研究束 → JSON-LD 圖（claims/evidence 連接）。",
        input_schema={"type": "object", "properties": {
            "bundle": {"type": "object"}}, "required": ["bundle"]},
        func=t_export_jsonld,
        use_when=["知識圖譜/語義網導出"], evidence_contract=meta_ec,
        failure_modes=[]))
    reg.add(ToolContractV2(
        name="research.export_tei",
        description="研究束 → TEI 文檔（claims=interpGrp，evidence=cit/"
                    "quote/bibl，帶 witness 出處）。",
        input_schema={"type": "object", "properties": {
            "bundle": {"type": "object"}}, "required": ["bundle"]},
        func=t_export_tei,
        use_when=["向 TEI 生態導出研究成果"], evidence_contract=meta_ec,
        failure_modes=[]))
    reg.add(ToolContractV2(
        name="research.export_bibtex",
        description="研究束涉及著作 → BibTeX 條目。",
        input_schema={"type": "object", "properties": {
            "bundle": {"type": "object"}}, "required": ["bundle"]},
        func=t_export_bibtex,
        use_when=["論文引用導出"], evidence_contract=meta_ec,
        failure_modes=[]))
