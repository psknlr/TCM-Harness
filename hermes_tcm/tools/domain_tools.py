"""formula.* / herb.* / case.* / domain.*：領域工具（Protocol §9.2）。

當前唯一領域插件是 shanghan（Domain Pack 第一名）：方劑/藥物/醫案
工具委托 hermes_shanghan 的規則庫工具，結果如實標注 domain=shanghan
——本草通用層、跨書方劑譜系屬後續 Domain Pack 擴展。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .contracts import EvidenceContract, ToolContractV2


def _call_legacy(name: str, arguments: Dict) -> Dict:
    # 領域工具委托一律經 shanghan Domain Pack 接縫（P0-3 依賴倒置：
    # 內核不直接 import legacy 註冊表）
    from ..domains.shanghan import call_legacy_tool
    return call_legacy_tool(name, arguments)


def _delegate(legacy_name: str, new_name: str, arguments: Dict) -> Dict:
    out = _call_legacy(legacy_name, arguments)
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


def t_herb_compare_properties(herbs: List[str]) -> Dict:
    """2-4 味藥的用藥檔案對比：頻次/配伍共現/共同配伍夥伴。

    誠實邊界：原始事實取自 A 層（組成/條文），配伍共現屬確定性派生
    統計；藥性功效（四氣五味/歸經）屬本草層未隨庫，不編造。"""
    herbs = [h for h in (herbs or []) if h and h.strip()][:4]
    if len(herbs) < 2:
        return {"error": "至少提供 2 味藥"}
    profiles: List[Dict] = []
    for h in herbs:
        out = _call_legacy("shanghan_herb_profile", {"herb": h})
        if out.get("error"):
            return {"tool": "herb.compare_properties",
                    "error": f"藥物檔案不可得：{h}——{out['error']}"}
        profiles.append(out)
    partner_sets = []
    for p in profiles:
        partners = {x.get("partner") or x.get("herb") or ""
                    for x in (p.get("co_occurrence")
                              or p.get("top_partners") or [])
                    if isinstance(x, dict)} - {""}
        partner_sets.append(partners)
    shared = set.intersection(*partner_sets) if all(partner_sets) else set()
    return {"tool": "herb.compare_properties", "available": True,
            "domain": "shanghan",
            "herbs": herbs,
            "profiles": [{k: p.get(k) for k in
                          ("herb", "n_formulas", "n_clauses",
                           "co_occurrence", "top_partners", "dose_range")
                          if k in p} for p in profiles],
            "shared_partners": sorted(shared)[:12],
            "note": "配伍共現為 A 層派生統計；四氣五味/歸經等藥性屬本草層"
                    "未隨庫，不編造——本草 Domain Pack 就緒後補齊"}


def t_case_extract_treatment_episode(formula: str = "", keyword: str = "",
                                     top_k: int = 3) -> Dict:
    """醫案 → 結構化診療片段：呈現（症/脈）→ 治法錨點 → 方劑。

    結局（outcome）文本尚未結構化（規劃層，如實標注）。"""
    out = t_case_search(formula=formula, keyword=keyword, top_k=top_k)
    if out.get("error"):
        return {**out, "tool": "case.extract_treatment_episode"}
    episodes = []
    for c in out.get("cases", []):
        episodes.append({
            "case_title": c.get("title", ""),
            "presentation": {"symptoms": c.get("symptoms", []),
                             "pulse": c.get("pulse", [])},
            "treatment": {"formula": c.get("formula", ""),
                          "canonical_support": c.get("canonical_support",
                                                     [])},
            "outcome": {"status": "not_structured",
                        "note": "結局文本結構化屬規劃層——不從案文猜測"}})
    return {"tool": "case.extract_treatment_episode", "available": True,
            "domain": "shanghan",
            "source": out.get("source", ""),
            "n_episodes": len(episodes), "episodes": episodes,
            "note": "片段為確定性抽取（詞表/規則），治法-方劑錨定 A 層條文；"
                    "結局關聯屬規劃層"}


def t_case_compare_outcomes(formulas: List[str], top_k: int = 3) -> Dict:
    """按方劑分組的醫案呈現譜對比。

    誠實邊界：醫案結局尚未結構化，本工具對比的是**呈現譜**
    （症狀/脈象分佈）而非療效結局——不冒充療效比較。"""
    formulas = [f for f in (formulas or []) if f and f.strip()][:4]
    if len(formulas) < 2:
        return {"error": "至少提供 2 個方劑"}
    groups = []
    for f in formulas:
        eps = t_case_extract_treatment_episode(formula=f, top_k=top_k)
        if eps.get("error"):
            groups.append({"formula": f, "n_cases": 0,
                           "error": eps["error"]})
            continue
        symptoms: Dict[str, int] = {}
        for e in eps.get("episodes", []):
            for s in e["presentation"]["symptoms"]:
                symptoms[s] = symptoms.get(s, 0) + 1
        groups.append({"formula": f,
                       "n_cases": eps.get("n_episodes", 0),
                       "top_symptoms": sorted(symptoms.items(),
                                              key=lambda kv: (-kv[1],
                                                              kv[0]))[:8]})
    return {"tool": "case.compare_outcomes", "available": True,
            "domain": "shanghan",
            "groups": groups,
            "comparison_semantics": "presentation_profile",
            "note": "對比對象是醫案呈現譜（症/脈分佈）；療效結局結構化"
                    "屬規劃層，不冒充結局比較"}


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
    reg.add(ToolContractV2(
        name="herb.compare_properties",
        description="2-4 味藥的用藥檔案對比：頻次/配伍共現/共同夥伴"
                    "（A 層派生統計；藥性四氣五味屬本草層未隨庫，不編造）。",
        input_schema={"type": "object", "properties": {
            "herbs": {"type": "array", "items": {"type": "string"},
                      "maxItems": 4}},
            "required": ["herbs"]},
        func=t_herb_compare_properties,
        use_when=["比較多味藥的用藥譜/配伍傾向"],
        do_not_use_when=["需要藥性功效結論（本草 Domain Pack 未就緒）"],
        evidence_contract=domain_ec,
        failure_modes=["herb_not_found"]))
    reg.add(ToolContractV2(
        name="case.extract_treatment_episode",
        description="醫案 → 結構化診療片段（呈現→治法錨點→方劑）；"
                    "結局結構化屬規劃層，如實標注。",
        input_schema={"type": "object", "properties": {
            "formula": {"type": "string"}, "keyword": {"type": "string"},
            "top_k": {"type": "integer", "default": 3}},
            "required": []},
        func=t_case_extract_treatment_episode,
        use_when=["把醫案轉為結構化診療片段供跨案分析"],
        evidence_contract=domain_ec,
        failure_modes=["no_cases_available"]))
    reg.add(ToolContractV2(
        name="case.compare_outcomes",
        description="按方劑分組的醫案**呈現譜**對比（症/脈分佈）——"
                    "療效結局結構化屬規劃層，不冒充結局比較。",
        input_schema={"type": "object", "properties": {
            "formulas": {"type": "array", "items": {"type": "string"},
                         "maxItems": 4},
            "top_k": {"type": "integer", "default": 3}},
            "required": ["formulas"]},
        func=t_case_compare_outcomes,
        use_when=["比較不同方劑對應醫案群的呈現特徵"],
        do_not_use_when=["需要療效/結局結論（未結構化）"],
        evidence_contract=domain_ec,
        failure_modes=["no_cases_available"]))
