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
    def research(self, query: str, execution_mode: str = "single",
                 **spec_kwargs) -> Dict:
        """研究 run → AnswerEnvelope dict（含 release 決策與 run 狀態）。

        execution_mode：single=typed DAG 單代理（默認）；
        council=隔離合議多專家（同一 RunStore/台賬類型/Release Gate）。"""
        if execution_mode == "council":
            return self._research_council(query, **spec_kwargs)
        row = self.controller.start(query, principal=self.principal,
                                    execution_mode=execution_mode,
                                    **spec_kwargs)
        env = row["state"].get("envelope") or AnswerEnvelope(
            answer="", answer_type="clarification_needed",
            limitations=["run 未到 release 節點"],
            run={"run_id": row["run_id"]},
            release={"decision": "review_required"}).to_dict()
        return {"run_id": row["run_id"], "status": row["status"],
                "envelope": env}

    def _research_council(self, query: str, **spec_kwargs) -> Dict:
        """隔離合議模式：ResearchOrchestrator 取證+合議，結果經同一
        Release Gate 裁定並落入同一 RunStore（run/證據/主張/工具調用
        全部持久化——多智能體編排不再遊離於主產品路徑之外）。"""
        from ..agents.orchestrator import ResearchOrchestrator
        from ..envelope import evidence_entry
        from ..harness.controller import extract_topic
        from ..harness.release import evaluate_release

        spec = self.controller.prepare(query, self.principal,
                                       execution_mode="council",
                                       **spec_kwargs)
        corpus_version = spec.environment_fingerprint.get("corpus", "")
        orch = ResearchOrchestrator(principal=self.principal,
                                    corpus_version=corpus_version)
        result, ledger, claims, broker = orch.run_with_context(
            extract_topic(query), spec.task_type)
        verdict = evaluate_release(
            spec, claims, result["answer"],
            ledger_problems=ledger.verify_integrity(),
            approved=frozenset())
        envelope = AnswerEnvelope(
            answer=result["answer"],
            answer_type="research_synthesis",
            claims=[{"claim_id": c.claim_id, "text": c.claim_text,
                     "status": c.status,
                     "evidence_ids": c.supporting_evidence}
                    for c in claims],
            evidence=[evidence_entry(r.to_dict())
                      for r in ledger.all_records()
                      if r.is_primary_text_returned],
            uncertainty=[f"{c.get('claim_type', '')}：{c.get('note', '')}"
                         for c in (result.get("conflicts") or [])],
            limitations=["council 模式：多專家隔離合議；審批續跑"
                         "（resume approve）尚未接入合議重跑，"
                         "review_required 需以 single 模式重查"],
            run={"run_id": spec.run_id,
                 "corpus_version": corpus_version,
                 "execution_mode": "council"},
            release=verdict)
        status = {"pass": "completed",
                  "pass_with_warning": "completed",
                  "pass_after_human_review": "completed",
                  "review_required": "paused",
                  "blocked": "blocked",
                  "failed_closed": "failed"}[verdict["decision"]]
        row = self.store.load(spec.run_id)
        state = {"envelope": envelope.to_dict(),
                 "final_answer": result["answer"],
                 "council": {k: result[k] for k in
                             ("specialists", "conflicts", "verification",
                              "synthesis_note", "budget", "n_evidence")},
                 "ledger": ledger.to_dict(),
                 "claims": [c.to_dict() for c in claims],
                 "guardrail_events": result.get("guardrail_events", [])}
        self.store.save_state(spec.run_id, status, state,
                              row["state_version"])
        for entry in broker.tool_calls:
            self.store.record_tool_call(spec.run_id, entry)
        for rec in ledger.all_records():
            self.store.record_evidence(spec.run_id, "council",
                                       rec.to_dict())
        for c in claims:
            self.store.record_claim(spec.run_id, c.to_dict())
        for cov in broker.coverages.values():
            self.store.record_coverage(spec.run_id, cov.to_dict())
        self.store.append_event(spec.run_id, "run_finished",
                                {"status": status,
                                 "execution_mode": "council"})
        return {"run_id": spec.run_id, "status": status,
                "envelope": envelope.to_dict()}

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
        return ResourceResolver(run_store=self.store).read(uri)
