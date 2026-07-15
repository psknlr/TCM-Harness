"""Python SDK（Protocol §17 integrations/sdk；§三 Agent Harness 使用面）。

進程內客戶端：研究 run、工具調用、資源讀取、審批——供腳本/notebook/
上層服務嵌入，與 CLI/HTTP/MCP 同一語義。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from ..core.principals import Principal
from ..envelope import AnswerEnvelope
from ..evidence.ledger import TypedEvidenceLedger
from ..harness.checkpoint import RunStore
from ..harness.controller import ResearchRunController
from ..integrations.mcp import ResourceResolver
from ..tools.broker import CapabilityBroker
from ..tools.registry import get_tcm_registry


class TCMClient:
    """進程內 SDK 客戶端。

    store_path：SQLite run 存儲位置（默認 data/tcm_runs/runs.db，
    不入庫）。所有回答走 AnswerEnvelope，不返回裸文本。"""

    def __init__(self, store_path: Optional[Path] = None,
                 principal: Optional[Principal] = None):
        if store_path is None:
            from hermes_shanghan import config
            store_path = config.DATA_DIR / "tcm_runs" / "runs.db"
        self.store = RunStore(Path(store_path))
        self.controller = ResearchRunController(self.store)
        self.principal = principal or Principal(subject="sdk",
                                                role="researcher")
        self.registry = get_tcm_registry()

    def close(self) -> None:
        self.store.close()

    # ------------------------------------------------------------------
    def research(self, query: str, **spec_kwargs) -> Dict:
        """研究 run → AnswerEnvelope dict（含 release 決策與 run 狀態）。"""
        row = self.controller.start(query, principal=self.principal,
                                    **spec_kwargs)
        env = row["state"].get("envelope") or AnswerEnvelope(
            answer="", answer_type="clarification_needed",
            limitations=["run 未到 release 節點"],
            run={"run_id": row["run_id"]},
            release={"decision": "review_required"}).to_dict()
        return {"run_id": row["run_id"], "status": row["status"],
                "envelope": env}

    def resume(self, run_id: str, approve: str = "", reject: str = "",
               approver: str = "", reason: str = "") -> Dict:
        row = self.controller.resume(run_id, approve=approve,
                                     reject=reject, approver=approver,
                                     reason=reason)
        return {"run_id": run_id, "status": row["status"],
                "envelope": row["state"].get("envelope", {})}

    def approvals(self, run_id: str) -> List[Dict]:
        return self.store.approvals(run_id)

    # ------------------------------------------------------------------
    def call_tool(self, name: str, arguments: Optional[Dict] = None,
                  approved_operations: Optional[List[str]] = None) -> Dict:
        """單工具調用（獨立 Broker/台賬；返回結果 + 本次登記的證據）。"""
        ledger = TypedEvidenceLedger("")
        broker = CapabilityBroker(
            self.registry.for_role(self.principal.role), ledger,
            principal=self.principal,
            approved_operations=approved_operations or [])
        result = broker.call(name, arguments or {})
        return {"result": result,
                "evidence": [r.to_dict() for r in ledger.all_records()],
                "guardrail_events": broker.guardrail_events}

    def discover_tools(self, query: str = "", namespace: str = "",
                       limit: int = 8) -> List[Dict]:
        return self.registry.discover(query=query, namespace=namespace,
                                      limit=limit)

    def read_resource(self, uri: str) -> Dict:
        return ResourceResolver(run_store=self.store,
                                principal=self.principal).read(uri)
