"""lexical：確定性詞彙重排（token 重疊 + 覆蓋密度）。

對 exact 檢索的命中做查詢無關長度懲罰的重疊評分——不是語義相似度，
如實命名為 lexical。
"""
from __future__ import annotations

from typing import Dict, List

from ..platform import fold_variants


def _bigrams(text: str) -> set:
    t = fold_variants("".join((text or "").split()))
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else set(t)


def rerank_lexical(query: str, hits: List[Dict],
                   text_key: str = "excerpt") -> List[Dict]:
    """按字符 bigram 重疊率重排（穩定排序：同分保持原次序）。"""
    q = _bigrams(query)
    if not q:
        return list(hits)
    scored = []
    for i, h in enumerate(hits):
        overlap = len(q & _bigrams(h.get(text_key, ""))) / len(q)
        scored.append((-overlap, i, h))
    scored.sort(key=lambda x: (x[0], x[1]))
    out = []
    for neg, _, h in scored:
        h = dict(h)
        h["lexical_score"] = round(-neg, 4)
        out.append(h)
    return out
