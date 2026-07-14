"""ToolContract V2（Protocol §9.3）。

每個工具聲明：use_when / do_not_use_when / side_effect / approval /
timeout / cacheable / evidence_contract / failure_modes——契約是機器
可讀數據，隨 spec 導出，並由 Broker 在調用管道中逐項執行。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

SIDE_EFFECTS = ("read_only", "annotate", "write_metadata", "admin")
APPROVALS = ("none", "prompt", "single_approval", "dual_approval",
             "expert_approval")


@dataclass
class EvidenceContract:
    """工具的證據契約：返回什麼證據、最低定位字段、是否須帶覆蓋記錄。"""
    returns_primary_text: bool = False
    evidence_role: str = ""          # primary_text_returned | metadata_only
    minimum_locator: List[str] = field(default_factory=list)
    requires_coverage_record: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ToolContractV2:
    name: str                        # 帶命名空間，如 citation.trace_quote
    description: str
    input_schema: Dict[str, Any]
    func: Callable[..., Dict]
    use_when: List[str] = field(default_factory=list)
    do_not_use_when: List[str] = field(default_factory=list)
    side_effect: str = "read_only"
    approval: str = "none"
    timeout_ms: int = 30000
    cacheable: bool = True
    idempotent: bool = True
    evidence_contract: EvidenceContract = field(
        default_factory=EvidenceContract)
    failure_modes: List[str] = field(default_factory=list)
    output_contract: str = "compact_envelope"   # 小型摘要+handles，非全文
    roles: List[str] = field(default_factory=list)   # 空=全部角色可用
    version: str = "2.0.0"

    def __post_init__(self):
        if "." not in self.name:
            raise ValueError(f"工具名必須帶命名空間（如 text.search_passages）"
                             f"：{self.name!r}")
        if self.side_effect not in SIDE_EFFECTS:
            raise ValueError(f"非法 side_effect {self.side_effect!r}")
        if self.approval not in APPROVALS:
            raise ValueError(f"非法 approval {self.approval!r}")
        if self.side_effect != "read_only" and self.approval == "none":
            raise ValueError(f"{self.name}：非只讀工具必須聲明審批等級"
                             "（默認只讀，寫入需要審批）")

    @property
    def namespace(self) -> str:
        return self.name.split(".", 1)[0]

    def schema_hash(self) -> str:
        blob = json.dumps(self.input_schema, sort_keys=True,
                          ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def spec(self) -> Dict[str, Any]:
        """機器可讀契約（隨 tool_specs 導出；不含 func）。"""
        return {
            "name": self.name,
            "namespace": self.namespace,
            "version": self.version,
            "description": self.description,
            "use_when": self.use_when,
            "do_not_use_when": self.do_not_use_when,
            "side_effect": self.side_effect,
            "approval": self.approval,
            "timeout_ms": self.timeout_ms,
            "cacheable": self.cacheable,
            "idempotent": self.idempotent,
            "input_schema": self.input_schema,
            "evidence_contract": self.evidence_contract.to_dict(),
            "failure_modes": self.failure_modes,
            "output_contract": self.output_contract,
            "roles": self.roles,
            "schema_hash": self.schema_hash(),
        }

    def openai_spec(self) -> Dict[str, Any]:
        """OpenAI function-calling 導出（命名空間點號 → 雙下劃線）。"""
        return {"type": "function", "function": {
            "name": self.name.replace(".", "__"),
            "description": self.description,
            "parameters": self.input_schema}}

    def anthropic_spec(self) -> Dict[str, Any]:
        return {"name": self.name.replace(".", "__"),
                "description": self.description,
                "input_schema": self.input_schema}

    def mcp_spec(self) -> Dict[str, Any]:
        """MCP tools/list 導出（含 annotations：readOnlyHint 等）。"""
        return {"name": self.name.replace(".", "__"),
                "description": self.description,
                "inputSchema": self.input_schema,
                "annotations": {
                    "readOnlyHint": self.side_effect == "read_only",
                    "idempotentHint": self.idempotent,
                    "destructiveHint": False,
                    "openWorldHint": False}}
