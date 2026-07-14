"""graph：圖擴展召回——規劃層（不冒充）。

引文邊/條文關係圖的**圖投影查詢**已就緒（tools/graph_tools.py）；
以圖結構做檢索**擴展召回**（多跳鄰域補召）屬規劃層。
"""
from __future__ import annotations

from typing import Dict


def expand_graph(seed_ids, hops: int = 1, **kwargs) -> Dict:
    return {"error": "not_implemented",
            "status": "planned",
            "note": "圖擴展召回屬規劃層；圖投影查詢見 graph.* 工具"}
