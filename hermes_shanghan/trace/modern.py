"""現代學術引用接口（論文/教材/專著引用的導入、回源與功能分類）。

現代論文與教材受版權與獲取方式限制，**不隨庫分發、不憑空生成**——
這是誠實性約束：語料自帶的最晚傳播層為民國（1937《經方實驗錄》）。
研究者可把自己整理的現代引用記錄放入
``data/shanghan/trace/modern_citations.jsonl``（一行一條）：

    {"source_title": "…", "year": 2019, "venue": "期刊/教材/專著/學位論文",
     "quote_text": "引用的古籍原文或轉述", "context": "引用處上下文（可選）"}

導入時每條記錄過與古籍層相同的逐字回源匹配器（明引/節引/改寫判定），
並按提示詞規則做引用功能分類（原文依據/理論闡釋/方證說明/教材轉述/
爭議討論/背景引用），使現代切片與歷代切片在同一網絡中可比。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .. import config
from ..schemas import read_jsonl
from ..textutil import fold_variants

MODERN_FILE = "modern_citations.jsonl"

# 引用功能分類的提示詞規則（按優先級判定，首中即止）
FUNCTION_RULES = [
    ("爭議討論", ["商榷", "質疑", "辨析", "存疑", "爭議", "误读", "誤讀"]),
    ("教材轉述", ["教材", "規劃教材", "讲义", "講義", "教科書", "教科书"]),
    ("方證說明", ["方證", "方证", "主治", "適應證", "适应证", "湯證", "汤证"]),
    ("理論闡釋", ["病機", "病机", "理論", "理论", "營衛", "营卫", "樞機", "枢机",
                  "氣化", "气化", "六經", "六经"]),
    ("原文依據", []),   # 逐字回源成功且無上述提示詞 → 原文依據
]


def classify_function(record: Dict, grounded: bool) -> str:
    text = (record.get("context", "") or "") + (record.get("venue", "") or "")
    for label, cues in FUNCTION_RULES[:-1]:
        if any(c in text for c in cues):
            return label
    return "原文依據" if grounded else "背景引用"


def load_modern_trace(matcher=None) -> Dict:
    """讀取並回源現代引用記錄；文件不存在時如實返回不可用狀態。"""
    from .builder import trace_dir
    path = trace_dir() / MODERN_FILE
    if not path.exists():
        return {"available": False,
                "note": "未導入現代文獻引用記錄（不隨庫分發、不憑空生成）。"
                        "可將整理好的引用置於 " + str(path.name) +
                        "；語料自帶的最晚傳播層為民國（1937《經方實驗錄》）。",
                "records": []}
    records = read_jsonl(path)
    if matcher is None:
        from .builder import get_matcher
        matcher = get_matcher()
    out: List[Dict] = []
    func_counts: Dict[str, int] = {}
    for i, r in enumerate(records, 1):
        quote = fold_variants(r.get("quote_text", "") or "")
        matches = matcher.match_text(quote, limit=3) if quote else []
        grounded = bool(matches and (matches[0].get("longest_run", 0) >= 8
                                     or matches[0].get("coverage", 0) >= 0.5))
        func = classify_function(r, grounded)
        func_counts[func] = func_counts.get(func, 0) + 1
        out.append({
            "modern_citation_id": f"MC_{i:04d}",
            "source_title": r.get("source_title", ""),
            "year": r.get("year", 0),
            "venue": r.get("venue", ""),
            "grounded": grounded,
            "clause_matches": matches,
            "citation_function": func,
        })
    return {"available": True, "n_records": len(out),
            "function_distribution": {k: func_counts[k] for k in sorted(func_counts)},
            "records": out}


def modern_echo_for(clause_ids: List[str], trace: Optional[Dict] = None) -> Dict:
    """給定條文集合，返回現代引用回聲（供溯源鏈使用）。"""
    trace = trace or load_modern_trace()
    if not trace.get("available"):
        return {"available": False, "note": trace.get("note", ""), "citations": []}
    wanted = set(clause_ids)
    hits = []
    for rec in trace.get("records", []):
        matched = [m for m in rec.get("clause_matches", [])
                   if m.get("clause_id") in wanted]
        if matched:
            hits.append({"source_title": rec["source_title"], "year": rec["year"],
                         "venue": rec["venue"],
                         "citation_function": rec["citation_function"],
                         "clause_ids": [m["clause_id"] for m in matched]})
    hits.sort(key=lambda h: (h.get("year") or 0, h.get("source_title", "")))
    return {"available": True, "n_citations": len(hits), "citations": hits}
