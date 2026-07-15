"""DomainRouter / EntityLinker（P0 修復：任務分類只有「研究問題類型」
一個維度）。

此前「桂枝湯的核心方證是什麼」被歸為 general_search，只走全庫
text.search_passages，不會調用已就緒 shanghan Domain Pack 的
formula.resolve——通用智能體與領域包沒有真正聯通。

本模塊把路由拆成兩個正交維度：

    Task Type × Domain Pack

    用戶問題 → 實體識別（Domain Pack 詞表，確定性）
             → 領域檢測（只路由到 status=ready 的包）
             → 任務細分（general_search → formula_pattern / …）
             → 檢索策略（domain_first_then_library：專屬工具優先，
               全庫旁證補充）

未就緒領域一律退回全庫檢索——不冒充領域能力。全部規則確定性、
離線可重放（planner=deterministic 不變量）。
"""
from __future__ import annotations

from typing import Dict, List

# 只在 general_search 兜底後細分的領域任務類型
DOMAIN_TASK_TYPES = ("formula_pattern", "herb_profile", "case_study")

# 醫案任務線索（比方證更特異，先判）
_CASE_CUES = ("醫案", "医案", "案例", "治驗", "治验", "驗案", "验案")

# 藥物檔案線索（無藥名詞表隨庫——按線索詞判定，工具側如實報錯兜底）
_HERB_CUES = ("藥證", "药证", "用藥", "用药", "配伍", "藥性", "药性",
              "歸經", "归经")


def link_entities(query: str) -> List[Dict]:
    """全部 ready Domain Pack 的確定性實體鏈接。"""
    from ..domains.registry import link_domain_entities
    return link_domain_entities(query or "")


def route(query: str, base_task_type: str = "general_search") -> Dict:
    """Task Type × Domain 路由決策（確定性）。

    返回 {task_type, domains, entities, retrieval_strategy}；
    base_task_type 非 general_search 時不改任務類型（顯式分類與
    _TASK_RULES 命中優先），但實體/領域信號照常返回。"""
    q = query or ""
    entities = link_entities(q)
    formula = next((e for e in entities if e.get("type") == "formula"), None)
    task_type = base_task_type
    if base_task_type == "general_search":
        if any(c in q for c in _CASE_CUES):
            task_type = "case_study"
        elif formula is not None:
            task_type = "formula_pattern"
        elif any(c in q for c in _HERB_CUES):
            task_type = "herb_profile"
    domains = sorted({e["domain"] for e in entities if e.get("domain")})
    if task_type in DOMAIN_TASK_TYPES and not domains:
        # 線索詞觸發的領域任務落在唯一就緒的領域包（工具側如實兜底）
        from ..domains.registry import ready_domain_packs
        domains = sorted(p.domain_id for p in ready_domain_packs()
                         if p.evidence_normalizer)
    strategy = ("domain_first_then_library"
                if domains and task_type in DOMAIN_TASK_TYPES
                else "library")
    return {"task_type": task_type, "domains": domains,
            "entities": entities, "retrieval_strategy": strategy}


def refine_general_task(query: str) -> str:
    """classify_task 的 general_search 兜底細分（規則命中不經此路）。"""
    return route(query, "general_search")["task_type"]
