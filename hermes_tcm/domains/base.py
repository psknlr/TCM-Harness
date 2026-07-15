"""DomainPack 接口（P0-3 依賴倒置的領域側契約）。

依賴方向：

    hermes_tcm 內核
          ↑ 只依賴本接口
    DomainPackInterface
          ↑ 實現
    shanghan_pack / jingui_pack / neijing_pack / …

內核不 import 領域實現的內部模塊；領域包經 registry 的 import 路徑
接縫（evidence_normalizer / entity_linker / implementation）向內核
提供能力。每個方法都有保守默認實現——未提供的能力如實返回空/未知，
不偽裝。

接口面（審計建議的 DomainPack Protocol 全集）：

    metadata / health / register_tools / detect_intent /
    extract_entities / build_plan / normalize_evidence /
    claim_policies / specialists / evaluation_suites / call_legacy_tool
"""
from __future__ import annotations

from typing import Dict, List


class DomainPackInterface:
    """領域包可執行接口。子類覆蓋自己具備的能力；默認實現一律
    fail-closed（空結果/未知狀態），不冒充。"""

    domain_id: str = ""

    # —— 聲明面 ——
    def metadata(self) -> Dict:
        from .registry import get_domain_pack
        pack = get_domain_pack(self.domain_id)
        return pack.to_dict() if pack else {"domain_id": self.domain_id}

    def health(self) -> Dict:
        """領域健康檢查：{healthy, status, checks:[{check, ok, note}]}。"""
        return {"domain_id": self.domain_id, "healthy": False,
                "status": "unknown",
                "checks": [{"check": "implemented", "ok": False,
                            "note": "領域包未提供健康檢查"}]}

    # —— 能力面 ——
    def register_tools(self, registry) -> None:
        """向 V2 工具註冊表註冊本領域工具（默認：無工具）。"""

    def detect_intent(self, query: str) -> Dict:
        """{domain_id, score∈[0,1], cues}——確定性領域信號，不用 LLM。"""
        return {"domain_id": self.domain_id, "score": 0.0, "cues": []}

    def extract_entities(self, query: str) -> List[Dict]:
        return []

    def build_plan(self, task_type: str,
                   entities: List[Dict] = ()) -> List[Dict]:
        """task_type → 檢索計劃步驟（[{step, tool}]；空=無領域計劃）。"""
        return []

    def normalize_evidence(self, tool_name: str, result: Dict,
                           corpus_version: str = "") -> List:
        return []

    def claim_policies(self) -> List[str]:
        """本領域附加的結論策略 id 清單（空=只用通用策略）。"""
        return []

    def specialists(self) -> List[str]:
        """本領域可用的合議專家角色。"""
        return []

    def evaluation_suites(self) -> List[str]:
        """本領域評測套件（測試文件路徑）。"""
        return []

    def call_legacy_tool(self, name: str, arguments: Dict) -> Dict:
        """legacy 工具委托入口（無 legacy 面的領域如實拒絕）。"""
        return {"error": f"領域 {self.domain_id} 無 legacy 工具面"}
