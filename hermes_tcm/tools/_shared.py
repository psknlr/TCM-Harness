"""工具實現共享助手：searcher/WorkRegistry 緩存 + SearchCoverage 構建。"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from hermes_shanghan.classics.tools import _searcher
from hermes_shanghan.corpus import library as _libmod

from ..corpus.registry import WorkRegistry
from ..evidence.coverage import SearchCoverage, coverage_id_for

_REGISTRY_CACHE: Dict[Tuple[str, float], WorkRegistry] = {}


def searcher():
    """classics.PassageSearcher（庫未就緒返回 None）。"""
    return _searcher()


def work_registry() -> Optional[WorkRegistry]:
    """按（庫根, 編目 mtime）緩存的 WorkRegistry——換庫自動失效。"""
    root = _libmod.library_root()
    cat = root / _libmod.CATALOG_NAME
    if not cat.exists():
        return None
    key = (str(root), cat.stat().st_mtime)
    if key not in _REGISTRY_CACHE:
        _REGISTRY_CACHE.clear()
        _REGISTRY_CACHE[key] = WorkRegistry(_libmod.Library(root))
    return _REGISTRY_CACHE[key]


def unavailable(tool: str) -> Dict:
    return {"tool": tool, "available": False,
            "error": "corpus_unavailable",
            "hint": "全庫未就緒：請先運行 `python3 -m hermes_shanghan "
                    "library fetch`"}


def coverage_from_search(result: Dict, query_forms: List[str],
                         corpus_version: str = "",
                         search_modes: Optional[List[str]] = None,
                         time_ordered: bool = False) -> SearchCoverage:
    """從 PassageSearcher 檢索結果構建 SearchCoverage（P0-3：每次檢索
    必須產生覆蓋記錄，工具輸出不得不聲明語料範圍）。"""
    layers = result.get("retrieval_layers") or {}
    l0 = layers.get("L0_metadata") or {}
    l2 = layers.get("L2_verbatim_scan") or {}
    capped = bool(result.get("scan_capped"))
    modes = list(search_modes or ["exact", "variant_folded"])
    if time_ordered and "dynasty_ordered" not in modes:
        modes.append("dynasty_ordered")
    filters = l0.get("filters") or {}
    s = searcher()
    n_candidates = l0.get("n_units_after",
                          len(getattr(s.lib, "units", [])) if s else 0)
    n_scanned = l2.get("n_units_scanned", 0)
    return SearchCoverage(
        coverage_id=coverage_id_for(query_forms,
                                    scope_note=str(sorted(filters.items()))),
        corpus_versions=[corpus_version] if corpus_version else [],
        included_categories=[filters["category"]]
        if filters.get("category") else [],
        dynasty_range=[filters["dynasty"]] if filters.get("dynasty") else [],
        candidate_works=n_candidates,
        works_scanned=n_scanned,
        passages_scanned=0,     # 段級計數未由底層返回，不編造
        query_forms=list(query_forms),
        search_modes=modes,
        scan_capped=capped,
        exhaustive_within_scope=not capped,
        stop_reason="scan_capped" if capped else "complete",
        known_gaps=(["零命中僅覆蓋前 max_scan 個候選"] if capped else []))
