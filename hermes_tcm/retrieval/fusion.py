"""fusion：多路檢索結果融合（Reciprocal Rank Fusion，確定性）。"""
from __future__ import annotations

from typing import Dict, List, Sequence


def fuse_rrf(ranked_lists: Sequence[List[Dict]], key: str = "passage_id",
             k: int = 60, limit: int = 20) -> List[Dict]:
    """RRF：score(d) = Σ 1/(k + rank_i(d))。確定性、無參數學習；
    同分按 key 字典序（跨進程穩定）。"""
    scores: Dict[str, float] = {}
    best: Dict[str, Dict] = {}
    for lst in ranked_lists:
        for rank, item in enumerate(lst):
            ident = item.get(key)
            if not ident:
                continue
            scores[ident] = scores.get(ident, 0.0) + 1.0 / (k + rank + 1)
            if ident not in best:
                best[ident] = item
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    out = []
    for ident, score in ordered[:max(1, limit)]:
        item = dict(best[ident])
        item["rrf_score"] = round(score, 6)
        out.append(item)
    return out
