"""ToolNamespaceRegistry：可發現、不平鋪的工具註冊表（Protocol §9.1）。

工具集過度膨脹會增加模型選擇錯誤和上下文負擔——本註冊表默認只暴露
命名空間清單與工具名，完整定義經 `discover()`（tool search）按需取出。

新工具委托給既有實現（classics/* 與 shanghan ToolRegistry），不重複
造輪子；委托結果經 Broker 統一轉換為 EvidenceRecord V2 + SearchCoverage。
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from .contracts import EvidenceContract, ToolContractV2

TOOLS_V2_VERSION = "2.0.0"

_NAMESPACE_SUMMARY = {
    "catalog": "書目與文獻身份：解析著作/傳本/作者/別名/分類",
    "text": "段落級檢索與閱讀（返回 P 層段落證據 + 覆蓋記錄）",
    "collation": "傳本對照與校勘（異文/apparatus 導出）",
    "citation": "引文溯源：時間有序載錄/反證搜索/轉引檢測",
    "concept": "術語與概念：異體解析/概念漂移計量",
    "formula": "方劑：解析/源流/組成與劑量比較",
    "herb": "藥物：名實解析/藥性檔案",
    "case": "醫案：檢索/診療片段抽取",
    "evidence": "證據包：構建/重驗",
    "claim": "主張：編譯/核驗/反證義務",
    "research": "研究導出：bundle/markdown/jsonld/tei/bibtex",
    "domain": "領域插件投影（domain.shanghan.* 等）",
    "admin": "管理操作（寫入類，逐級審批）",
}


class ToolNamespaceRegistry:
    def __init__(self):
        self._tools: Dict[str, ToolContractV2] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def add(self, contract: ToolContractV2) -> None:
        with self._lock:
            if contract.name in self._tools:
                raise ValueError(f"工具重複註冊：{contract.name}")
            self._tools[contract.name] = contract

    def get(self, name: str) -> Optional[ToolContractV2]:
        return self._tools.get(name)

    def names(self) -> List[str]:
        return sorted(self._tools)

    def namespaces(self) -> Dict[str, Dict]:
        """頂層可發現面：命名空間 → 摘要 + 工具名清單（不含 schema）。"""
        out: Dict[str, Dict] = {}
        for name in self.names():
            ns = name.split(".", 1)[0]
            entry = out.setdefault(
                ns, {"summary": _NAMESPACE_SUMMARY.get(ns, ""), "tools": []})
            entry["tools"].append(name)
        return out

    def discover(self, query: str = "", namespace: str = "",
                 limit: int = 8) -> List[Dict]:
        """按需取出完整工具定義（tool search：只把真正使用的工具定義
        放入上下文）。query 對 name/description/use_when 做包含匹配。"""
        hits: List[ToolContractV2] = []
        q = (query or "").strip().lower()
        for name in self.names():
            c = self._tools[name]
            if namespace and c.namespace != namespace:
                continue
            if q:
                haystack = " ".join(
                    [c.name, c.description] + c.use_when).lower()
                if q not in haystack and not any(
                        tok in haystack for tok in q.split()):
                    continue
            hits.append(c)
        return [c.spec() for c in hits[:max(1, limit)]]

    def for_role(self, role: str) -> "ToolNamespaceRegistry":
        """最小權限視圖：只保留該角色可用的工具。"""
        sub = ToolNamespaceRegistry()
        for c in self._tools.values():
            if not c.roles or role in c.roles:
                sub._tools[c.name] = c
        return sub

    # ------------------------------------------------------------------
    def specs(self) -> List[Dict]:
        return [self._tools[n].spec() for n in self.names()]

    def export(self) -> Dict[str, Any]:
        return {
            "tools_version": TOOLS_V2_VERSION,
            "namespaces": self.namespaces(),
            "contracts": self.specs(),
            "openai_tools": [self._tools[n].openai_spec()
                             for n in self.names()],
            "anthropic_tools": [self._tools[n].anthropic_spec()
                                for n in self.names()],
            "mcp_tools": [self._tools[n].mcp_spec() for n in self.names()],
        }


# ---------------------------------------------------------------------------
# 全量註冊（進程級單例）
# ---------------------------------------------------------------------------
_REGISTRY: Optional[ToolNamespaceRegistry] = None
_REGISTRY_LOCK = threading.Lock()


def get_tcm_registry() -> ToolNamespaceRegistry:
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            reg = ToolNamespaceRegistry()
            from . import (catalog_tools, citation_tools, collation_tools,
                           concept_tools, evidence_tools, research_tools,
                           text_tools, domain_tools)
            catalog_tools.register(reg)
            text_tools.register(reg)
            collation_tools.register(reg)
            citation_tools.register(reg)
            concept_tools.register(reg)
            evidence_tools.register(reg)
            research_tools.register(reg)
            domain_tools.register(reg)
            _REGISTRY = reg
        return _REGISTRY


def reset_tcm_registry() -> None:
    """測試用：換庫後重建註冊單例。"""
    global _REGISTRY
    with _REGISTRY_LOCK:
        _REGISTRY = None
