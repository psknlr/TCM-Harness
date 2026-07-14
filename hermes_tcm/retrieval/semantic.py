"""semantic：語義召回——規劃層（不冒充）。

必須避免的錯誤之一是把向量庫當完成品；語義召回引入時必須帶
可解釋的蘊含核驗（L2 entailment），在此之前顯式 not_implemented。
"""
from __future__ import annotations

from typing import Dict


def search_semantic(query: str, **kwargs) -> Dict:
    return {"error": "not_implemented",
            "status": "planned",
            "note": "語義召回屬規劃層：引入時須配套蘊含核驗與評測，"
                    "不以確定性檢索降級混充"}
