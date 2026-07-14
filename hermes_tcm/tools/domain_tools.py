"""formula.* / herb.* / case.* / domain.*：領域工具（Protocol §9.2）。

當前唯一領域插件是 shanghan（Domain Pack 第一名）：方劑/藥物/醫案
工具委托 hermes_shanghan 的規則庫工具，結果如實標注 domain=shanghan
——本草通用層、跨書方劑譜系屬後續 Domain Pack 擴展。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .contracts import EvidenceContract, ToolContractV2


def _legacy_registry():
    from hermes_shanghan.agent.tools import get_registry
    return get_registry()


def _delegate(legacy_name: str, new_name: str, arguments: Dict) -> Dict:
    out = _legacy_registry().call(legacy_name, arguments)
    if isinstance(out, dict):
        out = dict(out)
        out["tool"] = new_name
        out["domain"] = "shanghan"
        out.setdefault("note_domain",
                       "當前僅 shanghan Domain Pack 就緒；跨書通用層屬"
                       "後續擴展，不冒充全庫結論")
    return out


def t_formula_resolve(formula: str) -> Dict:
    return _delegate("shanghan_formula_rule", "formula.resolve",
                     {"formula": formula})


def t_formula_compare_composition(formulas: List[str]) -> Dict:
    return _delegate("shanghan_differential", "formula.compare_composition",
                     {"formulas": formulas})


def t_formula_compare_dosage(formula: str = "") -> Dict:
    args = {"formula": formula} if formula else {}
    return _delegate("shanghan_dose", "formula.compare_dosage", args)


def t_formula_trace_lineage(formula: str = "") -> Dict:
    """方劑源流：劑量演化邊（家族視圖）+ 全庫載錄時間線。"""
    out = _delegate("shanghan_dose", "formula.trace_lineage",
                    {"formula": formula} if formula else {})
    if formula and not out.get("error"):
        from .citation_tools import t_trace_term
        trace = t_trace_term(term=formula, max_scan=200, top=8)
        if not trace.get("error"):
            out["library_attestations"] = {
                "n": trace.get("n_attestations", 0),
                "earliest_in_library": trace.get("earliest_in_library"),
                "coverage": trace.get("coverage")}
            if trace.get("passage_evidence"):
                out["passage_evidence"] = trace["passage_evidence"]
    return out


def t_herb_resolve(herb: str) -> Dict:
    return _delegate("shanghan_herb_profile", "herb.resolve", {"herb": herb})


def t_herb_trace_name(herb: str, max_scan: int = 200) -> Dict:
    from .citation_tools import t_trace_term
    out = t_trace_term(term=herb, max_scan=max_scan, top=12)
    if isinstance(out, dict):
        out["tool"] = "herb.trace_name"
    return out


def t_case_search(formula: str = "", keyword: str = "",
                  top_k: int = 3) -> Dict:
    args: Dict = {"top_k": top_k}
    if formula:
        args["formula"] = formula
    if keyword:
        args["keyword"] = keyword
    return _delegate("shanghan_case_search", "case.search", args)


def register(reg) -> None:
    domain_ec = EvidenceContract(returns_primary_text=True,
                                 evidence_role="primary_text_returned",
                                 minimum_locator=["work_id"])
    reg.add(ToolContractV2(
        name="formula.resolve",
        description="方劑解析：核心證/兼證/脈象/組成/禁忌與支持條文"
                    "（domain=shanghan）。",
        input_schema={"type": "object", "properties": {
            "formula": {"type": "string"}}, "required": ["formula"]},
        func=t_formula_resolve,
        use_when=["按方名取方證規則與原文證據"],
        evidence_contract=domain_ec,
        failure_modes=["formula_not_found", "ambiguous_formula_name"]))
    reg.add(ToolContractV2(
        name="formula.compare_composition",
        description="2-3 個方劑多軸對比與關鍵鑒別點（domain=shanghan）。",
        input_schema={"type": "object", "properties": {
            "formulas": {"type": "array", "items": {"type": "string"}}},
            "required": ["formulas"]},
        func=t_formula_compare_composition,
        use_when=["方劑組成/主治比較"],
        evidence_contract=domain_ec,
        failure_modes=["formula_not_found"]))
    reg.add(ToolContractV2(
        name="formula.compare_dosage",
        description="方劑劑量計量層：銖當量藥量比/三家折算/家族劑量演化"
                    "（domain=shanghan）。",
        input_schema={"type": "object", "properties": {
            "formula": {"type": "string"}}, "required": []},
        func=t_formula_compare_dosage,
        use_when=["劑量比較/演化研究"],
        evidence_contract=domain_ec,
        failure_modes=["formula_not_found"]))
    reg.add(ToolContractV2(
        name="formula.trace_lineage",
        description="方劑源流：家族劑量演化邊 + 全庫時間有序載錄。",
        input_schema={"type": "object", "properties": {
            "formula": {"type": "string"}}, "required": ["formula"]},
        func=t_formula_trace_lineage,
        use_when=["方劑源流/譜系研究"],
        evidence_contract=domain_ec,
        failure_modes=["formula_not_found", "corpus_unavailable"]))
    reg.add(ToolContractV2(
        name="herb.resolve",
        description="藥物檔案：藥證/配伍共現/頻次（A 層派生，"
                    "domain=shanghan）。",
        input_schema={"type": "object", "properties": {
            "herb": {"type": "string"}}, "required": ["herb"]},
        func=t_herb_resolve,
        use_when=["按藥名取用藥檔案"],
        evidence_contract=domain_ec,
        failure_modes=["herb_not_found"]))
    reg.add(ToolContractV2(
        name="herb.trace_name",
        description="藥名演變：全庫時間有序載錄（藥名即術語級溯源）。",
        input_schema={"type": "object", "properties": {
            "herb": {"type": "string"},
            "max_scan": {"type": "integer", "default": 200}},
            "required": ["herb"]},
        func=t_herb_trace_name,
        use_when=["藥名歷史載錄/異名演變研究"],
        evidence_contract=domain_ec,
        failure_modes=["corpus_unavailable"]))
    reg.add(ToolContractV2(
        name="case.search",
        description="醫案檢索：按方劑/關鍵詞（domain=shanghan 醫案集）。",
        input_schema={"type": "object", "properties": {
            "formula": {"type": "string"}, "keyword": {"type": "string"},
            "top_k": {"type": "integer", "default": 3}},
            "required": []},
        func=t_case_search,
        use_when=["查找某方/某證的醫案用例"],
        evidence_contract=domain_ec,
        failure_modes=["no_cases_available"]))
