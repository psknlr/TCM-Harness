"""exact：精確/布爾/異體折疊檢索（委托 classics 三層內核）。"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..tools._shared import searcher


def search_exact(query: str, any_terms: Optional[List[str]] = None,
                 not_terms: Optional[List[str]] = None,
                 category: str = "", dynasty: str = "", work: str = "",
                 limit: int = 12, max_scan: int = 200,
                 order: str = "relevance") -> Dict:
    s = searcher()
    if s is None:
        return {"error": "corpus_unavailable"}
    return s.search(query=query, any_terms=any_terms or [],
                    not_terms=not_terms or [], category=category,
                    dynasty=dynasty, work=work, limit=limit,
                    max_scan=max_scan, order=order)
