"""Legacy 工具兼容適配器（Protocol §17，P0-10）。

原有 `shanghan_*` / `classics_*` API 不刪除、不改語義——本模塊維護
legacy 名稱 → 新命名空間工具的映射，Broker 在調用入口做名稱適配。
反向適配（新工具委托 legacy 實現）在各 *_tools.py 中完成。

    shanghan_search           → domain.shanghan.search（規則庫檢索）
    classics_search_passages  → text.search_passages
    classics_trace_citation   → citation.trace_quote
    ...

沒有映射的 legacy 工具（如 shanghan_match_formula 等診療輔助類）
繼續由 hermes_shanghan.agent.tools.get_registry() 服務——它們是
shanghan Domain Pack 的領域工具，不強行塞進通用命名空間。
"""
from __future__ import annotations

from typing import Dict, Optional

# legacy name → {tool: 新工具名, default_arguments: 參數預設,
#                argument_map: legacy 參數名 → 新參數名}
LEGACY_TOOL_MAP: Dict[str, Dict] = {
    # —— classics 全庫族 → 通用命名空間 ——
    "classics_search_passages": {"tool": "text.search_passages"},
    "classics_read_passage": {"tool": "text.read_passage"},
    "classics_compare_witnesses": {"tool": "collation.align_witnesses"},
    "classics_trace_citation": {"tool": "citation.trace_quote"},
    "classics_resolve_term": {"tool": "concept.resolve_term"},
    "classics_concept_drift": {"tool": "concept.drift"},
    "classics_export_evidence_packet": {"tool": "evidence.build_packet"},
    # —— shanghan 領域族（部分投影到通用面） ——
    "shanghan_formula_rule": {"tool": "formula.resolve"},
    "shanghan_differential": {"tool": "formula.compare_composition"},
    "shanghan_dose": {"tool": "formula.compare_dosage"},
    "shanghan_herb_profile": {"tool": "herb.resolve"},
    "shanghan_case_search": {"tool": "case.search"},
}


def resolve_legacy_tool(name: str) -> Optional[Dict]:
    """legacy 名稱 → 適配項；未映射返回 None（調用方 fail-closed）。"""
    return LEGACY_TOOL_MAP.get(name)
