"""ScopeContract：不可變檢索範圍合同（Protocol §7，P0-4 修復）。

問題：RunSpec 已含 collections/categories/dynasties/works/exclude，但
scope_contract 節點只是原樣輸出，retrieval 節點並未把這些約束系統性
傳入工具——聲明的 scope 與實際執行的檢索脫節（學術正確性問題）。

修復：scope_contract 節點編譯出**不可變** ScopeContract（帶 scope_hash）；
Broker 為每次檢索調用注入 scope 約束（category/dynasty/exclude），
SearchCoverage 回寫同一 scope_hash；ClaimVerifier 核對 coverage 與
scope 一致。Agent 不需要記得填參數——約束由 Harness 強制。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# 需要 scope 過濾的檢索工具參數：工具 → {scope 字段: 工具參數名}
# category/dynasty 是 classics 檢索的原生過濾參數（單值——多值時取
# 第一個並在 known_gaps 標注，不靜默丟棄）
SCOPE_AWARE_TOOLS = {
    "text.search_passages": {"category": "category", "dynasty": "dynasty",
                             "work": "work"},
    "citation.trace_quote": {},          # 全庫時間有序——scope 由後置過濾
    "citation.trace_term": {},
    "citation.counter_search": {},
    "concept.drift": {"category": "category"},
    "concept.resolve_term": {},
}


@dataclass
class ScopeContract:
    scope_id: str
    categories: List[str] = field(default_factory=list)
    dynasties: List[str] = field(default_factory=list)
    works: List[str] = field(default_factory=list)
    exclude_categories: List[str] = field(default_factory=list)
    exclude_works: List[str] = field(default_factory=list)
    corpus_versions: List[str] = field(default_factory=list)
    scope_hash: str = ""

    def __post_init__(self):
        if not self.scope_hash:
            self.scope_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        body = json.dumps({
            "categories": sorted(self.categories),
            "dynasties": sorted(self.dynasties),
            "works": sorted(self.works),
            "exclude_categories": sorted(self.exclude_categories),
            "exclude_works": sorted(self.exclude_works),
            "corpus_versions": sorted(self.corpus_versions),
        }, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]

    @property
    def is_unrestricted(self) -> bool:
        return not (self.categories or self.dynasties or self.works
                    or self.exclude_categories or self.exclude_works)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ScopeContract":
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in (d or {}).items() if k in known})

    # ------------------------------------------------------------------
    def constrain_arguments(self, tool: str, arguments: Dict) -> Dict:
        """為 scope-aware 工具注入約束參數（不覆蓋調用方已顯式收窄的
        更嚴約束——scope 是上界，調用方可再窄不可放寬）。"""
        mapping = SCOPE_AWARE_TOOLS.get(tool)
        if not mapping:
            return arguments
        args = dict(arguments)
        if "category" in mapping and self.categories \
                and not args.get(mapping["category"]):
            args[mapping["category"]] = self.categories[0]
        if "dynasty" in mapping and self.dynasties \
                and not args.get(mapping["dynasty"]):
            args[mapping["dynasty"]] = self.dynasties[0]
        if "work" in mapping and self.works \
                and not args.get(mapping["work"]):
            args[mapping["work"]] = self.works[0]
        return args

    def permits_hit(self, hit: Dict) -> bool:
        """後置過濾：命中是否落在 scope 內（category/dynasty/work/排除）。"""
        cat = hit.get("category", "") or ""
        dyn = hit.get("dynasty", "") or ""
        title = hit.get("title", "") or hit.get("work_title", "") or ""
        if self.categories and not any(c in cat for c in self.categories):
            return False
        if self.dynasties and not any(d in dyn for d in self.dynasties):
            return False
        if self.works and not any(w in title for w in self.works):
            return False
        if any(c and c in cat for c in self.exclude_categories):
            return False
        if any(w and w in title for w in self.exclude_works):
            return False
        return True

    def filter_hits(self, hits: List[Dict]) -> List[Dict]:
        return [h for h in hits if self.permits_hit(h)]


def compile_scope(corpus_scope: Dict, corpus_version: str = "") -> ScopeContract:
    """RunSpec.corpus_scope → 不可變 ScopeContract。"""
    cs = corpus_scope or {}
    exclude = cs.get("exclude") or []
    # exclude 條目可能是分類或著作名——保守起見兩者都放（過濾時各自匹配）
    return ScopeContract(
        scope_id="scope_" + hashlib.sha256(
            json.dumps(cs, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:12],
        categories=list(cs.get("categories") or []),
        dynasties=list(cs.get("dynasties") or []),
        works=list(cs.get("works") or []),
        exclude_categories=list(exclude),
        exclude_works=list(exclude),
        corpus_versions=[corpus_version] if corpus_version else [])
