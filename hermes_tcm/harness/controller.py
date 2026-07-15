"""ResearchRunController：typed DAG 的 durable 執行器（Protocol §10）。

用戶任務 → 定義檢索範圍 → 制定研究計劃 → 調用確定性工具取證
→ 形成結構化主張 → 搜索支持證據和反證 → 獨立驗證 → 綜合表達
→ 引用綁定 → 風險和權限審查 → 發布或進入人工審核

實現原則：

* 每個節點是確定性函數（planner=deterministic 時全程可重放）；
* 工具一律經 CapabilityBroker（span/台賬/預算/覆蓋登記）；
* 每節點後 checkpoint（SQLite CAS）；resume 跳過已完成節點；
* 節點邊界檢查取消旗標；
* human_review 產生審批請求 → run 轉 paused；
* approve 不是改狀態：重新執行 claim_verify 之後的下游節點。
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Dict, List, Optional

from ..claims.compiler import ClaimCompiler
from ..claims.counterevidence import counter_search_obligations
from ..claims.policy_dsl import ConclusionPolicyEngine
from ..claims.records import ClaimRecord
from ..claims.verifier import ClaimVerifier
from ..core.principals import Principal
from ..envelope import AnswerEnvelope, evidence_entry
from ..evidence.coverage import SearchCoverage, negative_statement
from ..evidence.ledger import TypedEvidenceLedger
from ..security.untrusted import scan_injection
from ..tools.broker import CapabilityBroker
from ..tools.registry import get_tcm_registry
from .approvals import approval_allowed, build_approval_request
from .budget import RunBudgetV2
from .checkpoint import RunStore
from .graph import RESEARCH_GRAPH, NodeContract, validate_graph
from .release import evaluate_release
from .run_spec import RunSpecV2, environment_fingerprint, new_run_id

TERMINAL = ("completed", "failed", "blocked", "rejected", "cancelled")

# 任務分類規則（確定性；未來 high_reasoning planner 可替換，
# 但工具/預算/證據範圍仍受本 Harness 控制）
_TASK_RULES = [
    ("earliest_attestation",
     ("最早", "最先", "首見", "首见", "首載", "首载", "首現", "首现",
      "首倡", "源出", "出自哪")),
    ("witness_comparison",
     ("傳本", "传本", "版本比較", "版本比较", "異文", "异文", "校勘",
      "對照", "对照")),
    ("formula_lineage", ("方劑源流", "方剂源流", "源流", "加減演化")),
    ("broad_consensus",
     ("普遍", "多數", "多数", "諸家", "诸家", "歷代", "历代", "共識",
      "共识")),
    ("term_genealogy", ("譜系", "谱系", "演變", "演变", "沿革", "漂移")),
]


def classify_task(query: str) -> str:
    q = query or ""
    for task_type, cues in _TASK_RULES:
        if any(c in q for c in cues):
            return task_type
    return "general_search"


def extract_topic(query: str) -> str:
    """檢索主題抽取：引號優先，其次去疑問詞的首個實詞串（確定性）。"""
    import re
    m = re.search(r"[「『\"']([^」』\"']{2,24})[」』\"']", query or "")
    if m:
        return m.group(1)
    cleaned = re.sub(r"(最早|最先|首見|首见|首載|首载|出現|出现|記載|记载|"
                     r"提出|見於|见于|哪部|哪本|什麼|什么|何時|何时|如何|"
                     r"是否|嗎|吗|呢|？|\?|的|在|一詞|一词|各?傳本|各?传本|"
                     r"異文|异文|比較|比较|校勘|源流|譜系|谱系|演變|演变|"
                     r"查一下|查查|請問|请问)", " ", query or "")
    tokens = [t for t in cleaned.split() if len(t) >= 2]
    return tokens[0] if tokens else (query or "").strip()[:12]


def _digest(obj: Any) -> str:
    try:
        blob = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                          default=str)
    except Exception:
        blob = str(obj)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class ResearchRunController:
    def __init__(self, store: RunStore, registry=None,
                 policy_engine: Optional[ConclusionPolicyEngine] = None):
        self.store = store
        self.registry = registry or get_tcm_registry()
        self.engine = policy_engine or ConclusionPolicyEngine()
        self.graph: List[NodeContract] = RESEARCH_GRAPH
        problems = validate_graph(self.graph)
        if problems:
            raise ValueError(f"研究圖靜態校驗失敗：{problems}")
        self.holder = f"controller-{uuid.uuid4().hex[:8]}"

    # ------------------------------------------------------------------
    def prepare(self, query: str, principal: Optional[Principal] = None,
                run_id: str = "", **spec_kwargs) -> RunSpecV2:
        """建立 queued run 並同步落盤（無幽靈 run）。"""
        principal = principal or Principal(subject="anonymous",
                                           role="researcher")
        # 顯式 task_type 優先（replay 沿用記錄值），否則確定性分類
        task_type = spec_kwargs.pop("task_type", "") or classify_task(query)
        spec = RunSpecV2(run_id=run_id or new_run_id(query), query=query,
                         principal=principal,
                         task_type=task_type,
                         environment_fingerprint=environment_fingerprint(),
                         **spec_kwargs)
        # 屬主/租戶落庫（P0-3 資源隔離的依據）
        self.store.create_run(spec.run_id, spec.to_dict(),
                              owner_subject=principal.subject,
                              tenant_id=principal.tenant_id)
        self.store.append_event(spec.run_id, "run_prepared",
                                {"task_type": spec.task_type})
        return spec

    def start(self, query: str, principal: Optional[Principal] = None,
              **spec_kwargs) -> Dict:
        spec = self.prepare(query, principal, **spec_kwargs)
        return self.execute(spec.run_id)

    # ------------------------------------------------------------------
    def execute(self, run_id: str) -> Dict:
        row = self.store.load(run_id)
        if row is None:
            raise ValueError(f"未知 run：{run_id}")
        if row["status"] in TERMINAL:
            return row
        spec = RunSpecV2.from_dict(row["spec"])
        state: Dict[str, Any] = row["state"] or {}
        version = row["state_version"]
        state.setdefault("nodes", {})
        state.setdefault("guardrail_events", [])
        state.setdefault("approved_items", [])

        # 台賬/預算重建（屬於 run，不屬於進程）
        from ..evidence.ledger import mint_broker_token
        corpus_version = spec.environment_fingerprint.get("corpus", "")
        ledger = TypedEvidenceLedger(corpus_version)
        if state.get("ledger"):
            ledger = TypedEvidenceLedger.from_dict(
                state["ledger"], mint_broker_token("capability_broker"))
        budget = RunBudgetV2(spec.budget)
        budget.restore(
            used_tool_calls=state.get("budget", {}).get("used_tool_calls", 0))
        broker = CapabilityBroker(
            self.registry.for_role(spec.principal.role), ledger,
            principal=spec.principal, budget=budget,
            corpus_version=corpus_version)
        # scope 重建（resume：scope 屬於 run，跨進程延續）——P0-4
        from .scope import ScopeContract, compile_scope
        if state.get("scope"):
            broker.scope = ScopeContract.from_dict(state["scope"])
        else:
            broker.scope = compile_scope(spec.corpus_scope.to_dict(),
                                         corpus_version)
            state["scope"] = broker.scope.to_dict()
        # 覆蓋記錄重建
        for cov_d in (state.get("coverages") or {}).values():
            try:
                sc = SearchCoverage.from_dict(cov_d)
                broker.coverages[sc.coverage_id] = sc
            except (TypeError, ValueError):
                pass
        claims = [ClaimRecord.from_dict(c)
                  for c in state.get("claims", [])]

        ctx = _RunContext(spec=spec, state=state, ledger=ledger,
                          broker=broker, budget=budget, claims=claims,
                          engine=self.engine)

        status = "running"
        version = self.store.save_state(run_id, status, state, version)
        self.store.append_event(run_id, "run_started", {})

        for node in self.graph:
            node_state = state["nodes"].get(node.node_id, {})
            if node_state.get("status") in ("ok", "skipped_by_triage") \
                    and not node.always_rerun:
                continue
            deps_ok = all(
                state["nodes"].get(d, {}).get("status")
                in ("ok", "degraded", "skipped_by_triage")
                for d in node.dependencies)
            if not deps_ok:
                state["nodes"][node.node_id] = {"status": "skipped"}
                version = self.store.save_state(run_id, status, state,
                                                version)
                continue
            if state.get("cancel_requested"):
                status = "cancelled"
                self.store.append_event(run_id, "run_cancelled", {})
                break
            self.store.acquire_lease(run_id, node.node_id, self.holder)
            try:
                outcome = self._run_node(ctx, node)
            finally:
                self.store.release_lease(run_id, node.node_id, self.holder)
            state["nodes"][node.node_id] = outcome["node_state"]
            if outcome.get("output") is not None:
                state.setdefault("node_outputs", {})[node.node_id] = \
                    outcome["output"]
            self.store.record_attempt(
                run_id, node.node_id,
                outcome["node_state"].get("attempts", 1),
                outcome["node_state"]["status"],
                output=outcome.get("output"),
                error=outcome["node_state"].get("error", ""),
                idempotency_key=outcome.get("idempotency_key", ""))
            # 持久化台賬/主張/覆蓋/預算鏡像
            state["ledger"] = ledger.to_dict()
            state["claims"] = [c.to_dict() for c in ctx.claims]
            state["coverages"] = {cid: c.to_dict()
                                  for cid, c in broker.coverages.items()}
            state["budget"] = budget.snapshot()
            state["guardrail_events"] = (state.get("guardrail_events", [])
                                         + broker.guardrail_events)
            broker.guardrail_events = []
            for entry in broker.tool_calls:
                self.store.record_tool_call(run_id, entry)
            broker.tool_calls = []
            for rec in ledger.all_records():
                self.store.record_evidence(run_id, "execute", rec.to_dict())
            for c in ctx.claims:
                self.store.record_claim(run_id, c.to_dict())
            for cov in broker.coverages.values():
                self.store.record_coverage(run_id, cov.to_dict())

            # P1-8：intake 前置分診說停就停——標記下游（除 release）
            # 跳過，直達 release 產出拒答信封，不消耗任何檢索工具
            if node.node_id == "intake":
                dec = (outcome.get("output", {}) or {}).get(
                    "triage_decision") or {}
                if dec and not dec.get("continue_execution", True):
                    ctx.state["refused"] = True
                    ctx.state["refusal_message"] = dec.get("message", "")
                    ctx.state["refused_intents"] = dec.get("intents", [])
                    for n in self.graph:
                        if n.node_id not in ("intake", "release"):
                            state["nodes"][n.node_id] = {
                                "status": "skipped_by_triage"}

            new_status = outcome.get("run_status")
            if new_status:
                status = new_status
            version = self.store.save_state(run_id, status, state, version)
            self.store.append_event(run_id, "node_finished",
                                    {"node": node.node_id,
                                     "status": outcome["node_state"]
                                     ["status"]})
            if status in ("paused",) + TERMINAL:
                break
        else:
            if status == "running":
                status = "completed"
        # 終寫：無論以何種方式離開循環，最終狀態必須落盤
        version = self.store.save_state(run_id, status, state, version)
        self.store.append_event(run_id, "run_finished", {"status": status})
        return self.store.load(run_id)

    # ------------------------------------------------------------------
    def request_cancel(self, run_id: str) -> bool:
        row = self.store.load(run_id)
        if row is None or row["status"] in TERMINAL:
            return False
        state = row["state"]
        state["cancel_requested"] = True
        self.store.save_state(run_id, row["status"], state,
                              row["state_version"])
        return True

    def resume(self, run_id: str, approve: str = "", reject: str = "",
               approver: str = "", reason: str = "",
               reviewer=None) -> Dict:
        """approve/reject 按 trigger 逐項；批准後重跑 claim_verify 下游。

        reviewer：服務端認證的審核人 Principal（P0-2）。提供時強制核驗
        審核人角色/租戶；審批對象一致性/有效期/單次使用一律核驗。"""
        from .approvals import verify_approval
        row = self.store.load(run_id)
        if row is None:
            raise ValueError(f"未知 run：{run_id}")
        if row["status"] in TERMINAL:
            # 終態不可復活/改寫：completed/blocked/rejected/cancelled 的
            # approve/reject 一律 no-op 返回持久化狀態
            return row
        state = row["state"]
        version = row["state_version"]
        if row["status"] != "paused" and not (approve or reject):
            return self.execute(run_id)
        # 審批請求索引（單次使用/有效期/digest 核驗的依據）
        requests = {a["trigger"]: a for a in self.store.approvals(run_id)}
        cur_digest = _digest(state.get("final_answer"))
        if reject:
            req = requests.get(reject)
            if req is not None:
                req["status"] = "rejected"
                req["reviewer"] = getattr(reviewer, "subject", approver)
                self.store.record_approval(run_id, req)
            state.setdefault("guardrail_events", []).append(
                {"event": "human_review_rejected", "trigger": reject,
                 "approver": getattr(reviewer, "subject", approver),
                 "reason": reason})
            self.store.save_state(run_id, "rejected", state, version)
            return self.store.load(run_id)
        if approve:
            req = requests.get(approve)
            ok, why = verify_approval(req, approve, reviewer=reviewer,
                                      current_action_digest=cur_digest)
            if not ok:
                state.setdefault("guardrail_events", []).append(
                    {"event": "approval_refused", "trigger": approve,
                     "approver": getattr(reviewer, "subject", approver),
                     "reason": why})
                self.store.save_state(run_id, row["status"], state, version)
                return self.store.load(run_id)
            # 單次使用：消費該審批請求（防重放）
            if req is not None:
                req["status"] = "approved"
                req["reviewer"] = getattr(reviewer, "subject", approver)
                req["approved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                self.store.record_approval(run_id, req)
            approved = sorted(set(state.get("approved_items", []))
                              | {approve})
            state["approved_items"] = approved
            state.setdefault("guardrail_events", []).append(
                {"event": "human_review_approved", "trigger": approve,
                 "approver": getattr(reviewer, "subject", approver),
                 "reviewer_role": getattr(reviewer, "role", ""),
                 "reason": reason})
            # 批准 ≠ 改狀態：重置 claim_verify 及下游，重新過閘
            for node_id in ("claim_verify", "synthesis", "citation_bind",
                            "safety_and_policy", "human_review", "release"):
                state.get("nodes", {}).pop(node_id, None)
            version = self.store.save_state(run_id, "queued", state,
                                            version)
        return self.execute(run_id)

    # ------------------------------------------------------------------
    def _run_node(self, ctx: "_RunContext", node: NodeContract) -> Dict:
        handler = getattr(self, f"_n_{node.node_id}", None)
        if handler is None:
            raise ValueError(f"未實現節點 {node.node_id}")
        attempts = 0
        last_error = ""
        # P0-6：工具調用型節點在受限 Broker 上下文執行——只能調本節點
        # tool_scope 命名空間、受節點預算與截止約束。ctx.broker 在節點
        # 執行期間切換為受限視圖（共享台賬/覆蓋/run 預算/scope）。
        run_broker = ctx.broker
        use_node_broker = bool(node.tool_scope) or node.budget_tool_calls
        deadline = (time.time() + node.timeout_ms / 1000.0
                    if node.timeout_ms else None)
        while attempts <= node.retry_policy:
            attempts += 1
            t0 = time.time()
            if use_node_broker:
                ctx.broker = run_broker.for_node(node, deadline=deadline)
            try:
                output = handler(ctx)
                missing = [f for f in node.output_schema
                           if f not in (output or {})]
                if missing:
                    raise ValueError(f"節點輸出契約違例：缺少 {missing}")
                ctx.outputs[node.node_id] = output
                return {"node_state": {"status": "ok", "attempts": attempts,
                                       "duration_ms": int(
                                           (time.time() - t0) * 1000),
                                       "output_digest": _digest(output)},
                        "output": output,
                        "run_status": output.get("_run_status"),
                        "idempotency_key": _digest(
                            {k: ctx.outputs.get("task_classify", {}).get(k)
                             for k in node.idempotency_key_fields})}
            except Exception as exc:   # noqa: BLE001 — 節點級隔離
                last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            finally:
                ctx.broker = run_broker      # 恢復 run 級 broker
        if node.fallback_policy == "degrade":
            output = {"degraded": True, "error": last_error}
            for f in node.output_schema:
                output.setdefault(f, [] if f.endswith("s") else "")
            ctx.outputs[node.node_id] = output
            return {"node_state": {"status": "degraded",
                                   "attempts": attempts,
                                   "error": last_error},
                    "output": output}
        if node.fallback_policy == "skip":
            return {"node_state": {"status": "skipped",
                                   "attempts": attempts,
                                   "error": last_error}}
        return {"node_state": {"status": "failed", "attempts": attempts,
                               "error": last_error},
                "run_status": "failed"}

    # ------------------------------------------------------------------
    # 節點實現
    # ------------------------------------------------------------------
    def _n_intake(self, ctx: "_RunContext") -> Dict:
        signals = scan_injection(ctx.spec.query)
        if signals:
            ctx.state["guardrail_events"].append(
                {"event": "injection_signals_in_query",
                 "signals": signals,
                 "note": "輸入標記為 UNTRUSTED；信號僅審計，不改變數據地位"})
        decision = {"outcome": "safe", "continue_execution": True,
                    "message": ""}
        # P1-8：紅旗分診 + 意圖守衛在**任何領域工具執行前**完成——
        # 患者教育目的/public 角色的臨床型請求先攔截，不消耗檢索工具，
        # 不留到 release 才擋。復用 hermes_shanghan 成熟 triage/守衛。
        purpose = ctx.spec.principal.purpose_of_use
        role = ctx.spec.principal.role
        if purpose == "patient_education" or role == "public":
            try:
                from hermes_shanghan import safety
                flag = safety.red_flag_triage(ctx.spec.query)
                if flag:
                    decision = {"outcome": "emergency_redirect",
                                "continue_execution": False,
                                "message": "檢測到急症紅旗信號，請立即就醫。"
                                           "本接口不提供診斷或處方。",
                                "intents": flag.get("red_flags", [])}
                else:
                    guard = safety.patient_intent_guard(ctx.spec.query)
                    if guard:
                        decision = {
                            "outcome": "refused_intent",
                            "continue_execution": False,
                            "message": "該請求涉及診斷/處方/劑量，"
                                       "患者教育接口不提供；可改問古籍"
                                       "原文與術語含義。",
                            "intents": guard.get("refused_intents", [])}
            except Exception:
                pass
        if not decision["continue_execution"]:
            ctx.state["guardrail_events"].append(
                {"event": "intake_triage_refused",
                 "outcome": decision["outcome"],
                 "intents": decision.get("intents", [])})
        return {"sanitized_query": ctx.spec.query.strip(),
                "injection_signals": signals,
                "triage_decision": decision}

    def _n_task_classify(self, ctx: "_RunContext") -> Dict:
        q = ctx.outputs["intake"]["sanitized_query"]
        task_type = ctx.spec.task_type or classify_task(q)
        topic = extract_topic(q)
        from hermes_shanghan.textutil import fold_variants
        forms = list(dict.fromkeys([topic, fold_variants(topic)]))
        return {"task_type": task_type, "topic": topic,
                "query_forms": [f for f in forms if f]}

    def _n_scope_contract(self, ctx: "_RunContext") -> Dict:
        # scope 已在 execute() 頂部編譯進 broker.scope 並落 state["scope"]
        # （resume 沿用）；此節點輸出不可變合同供下游核對
        scope = ctx.broker.scope
        return {"corpus_scope": ctx.spec.corpus_scope.to_dict(),
                "scope_contract": scope.to_dict() if scope else {},
                "scope_hash": scope.scope_hash if scope else "",
                "corpus_version":
                    ctx.spec.environment_fingerprint.get("corpus", "")}

    def _n_plan_compile(self, ctx: "_RunContext") -> Dict:
        task_type = ctx.outputs["task_classify"]["task_type"]
        from ..skills import skill_for_task
        skill = skill_for_task(task_type)
        plans = {
            "earliest_attestation": [
                {"step": "resolve", "tool": "catalog.resolve_work"},
                {"step": "trace", "tool": "citation.trace_quote"},
                {"step": "counter", "tool": "citation.counter_search"}],
            "witness_comparison": [
                {"step": "resolve", "tool": "catalog.list_witnesses"},
                {"step": "align", "tool": "collation.align_witnesses"}],
            "term_genealogy": [
                {"step": "resolve_term", "tool": "concept.resolve_term"},
                {"step": "trace", "tool": "citation.trace_term"},
                {"step": "drift", "tool": "concept.drift"}],
            "formula_lineage": [
                {"step": "lineage", "tool": "formula.trace_lineage"}],
            "broad_consensus": [
                {"step": "search", "tool": "text.search_passages"},
                {"step": "counter", "tool": "text.search_passages"}],
        }
        steps = plans.get(task_type,
                          [{"step": "search",
                            "tool": "text.search_passages"}])
        return {"plan_steps": steps,
                "skill_used": skill["name"] if skill else ""}

    def _n_catalog_resolution(self, ctx: "_RunContext") -> Dict:
        topic = ctx.outputs["task_classify"]["topic"]
        task_type = ctx.outputs["task_classify"]["task_type"]
        resolved: List[Dict] = []
        flags: List[Dict] = []
        if task_type == "witness_comparison":
            out = ctx.broker.call("catalog.resolve_work", {"title": topic},
                                  node_id="catalog_resolution")
            if not out.get("error"):
                res = out.get("resolution", {})
                resolved.append(res)
                if res.get("needs_human_adjudication"):
                    flags.append({"flag": "identity_needs_review",
                                  "query": topic,
                                  "candidates": res.get("candidates", [])})
        return {"resolved_works": resolved, "identity_flags": flags}

    def _n_retrieval_fanout(self, ctx: "_RunContext") -> Dict:
        tc = ctx.outputs["task_classify"]
        task_type, topic = tc["task_type"], tc["topic"]
        forms = tc["query_forms"]
        calls: List[Dict] = []
        if task_type == "earliest_attestation":
            calls.append(("citation.trace_quote", {"quote": topic}))
        elif task_type == "term_genealogy":
            calls.append(("citation.trace_term",
                          {"term": topic, "variants": forms[1:]}))
            calls.append(("concept.drift", {"term": topic}))
        elif task_type == "witness_comparison":
            calls.append(("collation.align_witnesses",
                          {"work": topic, "query": topic}))
        elif task_type == "formula_lineage":
            calls.append(("formula.trace_lineage", {"formula": topic}))
        else:
            calls.append(("text.search_passages",
                          {"query": topic, "order": "dynasty"}))
        results = []
        for name, args in calls:
            out = ctx.broker.call(name, args, node_id="retrieval_fanout")
            results.append({"tool": name, "ok": "error" not in out,
                            "error": out.get("error"),
                            "available": out.get("available", True)})
        ctx.state["retrieval_results"] = results
        return {"evidence_ids": ctx.ledger.primary_text_ids(),
                "coverage_ids": sorted(ctx.broker.coverages)}

    def _n_identity_and_attribution_check(self, ctx: "_RunContext") -> Dict:
        report: List[Dict] = []
        for rec in ctx.ledger.all_records():
            complete = bool(rec.work_id and rec.witness_id
                            and rec.passage_id)
            low_conf = (rec.identity_confidence or 0) < 0.7
            if not complete or low_conf:
                report.append({"evidence_id": rec.evidence_id,
                               "identity_complete": complete,
                               "identity_confidence":
                                   rec.identity_confidence})
        flags = ctx.outputs.get("catalog_resolution", {}) \
            .get("identity_flags", [])
        return {"identity_report": {"n_checked": len(ctx.ledger),
                                    "n_flagged": len(report),
                                    "flagged": report[:10],
                                    "identity_flags": flags}}

    def _n_counterevidence_search(self, ctx: "_RunContext") -> Dict:
        tc = ctx.outputs["task_classify"]
        task_type = tc["task_type"]
        if ctx.spec.counterevidence_policy == "off":
            return {"counter_obligations": [], "counter_results": [],
                    "counter_search_performed": False}
        claim_type = {"earliest_attestation": "earliest_attestation",
                      "broad_consensus": "broad_consensus",
                      "term_genealogy": "earliest_attestation"} \
            .get(task_type)
        if claim_type is None and \
                ctx.spec.counterevidence_policy == "risk_based":
            return {"counter_obligations": [], "counter_results": [],
                    "counter_search_performed": False}
        probe = ClaimRecord(claim_id="clm_probe", claim_text=tc["topic"],
                            claim_type=claim_type or "attestation")
        obligations = counter_search_obligations(probe, tc["query_forms"])
        results: List[Dict] = []
        for ob in obligations:
            tool = ob["tool"]
            args = ({"quote": ob["query"]}
                    if tool.startswith("citation.counter")
                    else {"term": ob["query"].split("|")[0]}
                    if tool == "citation.trace_term"
                    else {"quote": ob["query"]}
                    if tool == "citation.trace_quote"
                    else {"query": ob["query"]})
            out = ctx.broker.call(tool, args,
                                  node_id="counterevidence_search")
            results.append({"obligation": ob["kind"], "tool": tool,
                            "ok": "error" not in out,
                            "n_candidates": out.get("n_candidates", 0),
                            "error": out.get("error")})
        # 全部義務成功執行才算完成——corpus 不可用時如實 False
        # （反證沒做就是沒做，不能記成做了）
        performed = bool(obligations) and all(r["ok"] for r in results)
        return {"counter_obligations": obligations,
                "counter_results": results,
                "counter_search_performed": performed}

    def _n_claim_compile(self, ctx: "_RunContext") -> Dict:
        tc = ctx.outputs["task_classify"]
        counter = ctx.outputs.get("counterevidence_search", {})
        from ..evidence.packets import build_packet
        # 主檢索覆蓋優先（retrieval_fanout 登記的），反證覆蓋不混用
        coverage = None
        primary_cov_ids = ctx.outputs.get("retrieval_fanout", {}) \
            .get("coverage_ids", [])
        for cid in primary_cov_ids:
            if cid in ctx.broker.coverages:
                coverage = ctx.broker.coverages[cid]
                break
        if coverage is None and ctx.broker.coverages:
            coverage = sorted(ctx.broker.coverages.values(),
                              key=lambda c: c.coverage_id)[0]
        packet = build_packet(tc["topic"], ctx.ledger.all_records(),
                              coverage=coverage,
                              corpus_version=ctx.ledger.corpus_version)
        compiler = ClaimCompiler()
        task_for_compile = tc["task_type"]
        if task_for_compile in ("term_genealogy",):
            task_for_compile = "earliest_attestation"
        if not packet.records and coverage is not None:
            task_for_compile = "negative_result"
        elif not packet.records and coverage is None:
            # 庫未就緒：無覆蓋無證據——負結論也不可發布
            task_for_compile = "negative_result"
        new_claims = compiler.compile(
            task_for_compile, packet, topic=tc["topic"],
            counter_search_performed=counter.get("counter_search_performed",
                                                 False))
        ctx.claims.clear()
        ctx.claims.extend(new_claims)
        ctx.packet = packet
        return {"claim_ids": [c.claim_id for c in ctx.claims]}

    def _n_claim_verify(self, ctx: "_RunContext") -> Dict:
        coverage = None
        for c in ctx.claims:
            if c.scope_id and c.scope_id in ctx.broker.coverages:
                coverage = ctx.broker.coverages[c.scope_id]
                break
        if coverage is None and ctx.broker.coverages:
            coverage = sorted(ctx.broker.coverages.values(),
                              key=lambda c: c.coverage_id)[0]
        # 權威工具清單：事件存儲 tool_calls 表（成功調用）∪ 本輪 broker
        # 審計尾（尚未落庫的調用）
        tools_used = sorted(set(self._tools_used(ctx.spec.run_id))
                            | {e["tool"] for e in ctx.broker.audit_tail(200)
                               if e.get("ok")})
        # P0-5：版本鎖定的 PassageIndex 供高風險主張回源核驗；庫未就緒
        # 時為 None（source_reverified 如實為 False，高風險主張降級 review）
        passage_index = None
        try:
            from ..tools._shared import searcher
            s = searcher()
            passage_index = s.index if s is not None else None
        except Exception:
            passage_index = None
        verifier = ClaimVerifier(ctx.ledger, self.engine,
                                 passage_index=passage_index)
        summary = verifier.verify_all(
            ctx.claims, coverage=coverage,
            tools_used=tools_used,
            role=ctx.spec.principal.role,
            coverage_lookup=dict(ctx.broker.coverages))
        return {"verification_summary": summary}

    def _tools_used(self, run_id: str) -> List[str]:
        with self.store._lock:
            rows = self.store._conn.execute(
                "SELECT DISTINCT tool FROM tool_calls WHERE run_id=?"
                " AND ok=1", (run_id,)).fetchall()
        return [r[0] for r in rows]

    def _n_synthesis(self, ctx: "_RunContext") -> Dict:
        """綜合表達：只基於 verified / needs_review 主張，不新增事實。"""
        lines: List[str] = []
        for c in ctx.claims:
            if c.status == "failed":
                continue
            qualifier = "".join(f"（{q}）" for q in c.forced_qualifiers)
            marker = "" if c.status == "verified" else "【待人工審核】"
            lines.append(f"{marker}{c.claim_text}{qualifier}")
        if not lines:
            cov = None
            if ctx.broker.coverages:
                cov = sorted(ctx.broker.coverages.values(),
                             key=lambda c: c.coverage_id)[0]
            if cov is not None:
                stmt = negative_statement(cov)
                lines.append(stmt.get("statement") or
                             "（覆蓋範圍不足，無可發布結論）")
            else:
                lines.append("（本輪未取得任何可核驗證據，無可發布結論）")
        return {"draft_answer": "。".join(lines) + "。"}

    def _n_citation_bind(self, ctx: "_RunContext") -> Dict:
        from ..claims.binder import bind_citations
        draft = ctx.outputs["synthesis"]["draft_answer"]
        bound, citations = bind_citations(draft, ctx.claims, ctx.ledger)
        # 綁定即定稿：final_answer 在此固定，使 human_review 的
        # action_digest 與 resume 時的核驗對象一致（審批對象不漂移）
        ctx.state["final_answer"] = bound
        return {"bound_answer": bound, "citations": citations}

    def _n_safety_and_policy(self, ctx: "_RunContext") -> Dict:
        from .release import clinical_actions
        answer = ctx.outputs["citation_bind"]["bound_answer"]
        actions = clinical_actions(answer)
        return {"safety_report": {"clinical_actions": actions,
                                  "role": ctx.spec.principal.role,
                                  "purpose":
                                      ctx.spec.principal.purpose_of_use}}

    def _n_human_review(self, ctx: "_RunContext") -> Dict:
        # 拒答路徑（always_rerun 使本節點即便被標記跳過仍會執行）：
        # 分診已攔截，無主張可審——直接空隊列
        if ctx.state.get("refused"):
            return {"review_queue": [], "pending": []}
        queue: List[Dict] = []
        answer = ctx.outputs["citation_bind"]["bound_answer"]
        action_digest = _digest(answer)
        evidence_digest = _digest([r.evidence_id
                                   for r in ctx.ledger.all_records()])
        policy_version = self.engine.version
        tenant = ctx.spec.principal.tenant_id

        def _add(key: str) -> None:
            # 已批准的 trigger 不再重建請求（否則會覆蓋已消費的請求，
            # 打開重放缺口）——批准後下游重跑時保留 approved 態
            existing = {a["trigger"]: a
                        for a in self.store.approvals(ctx.spec.run_id)}
            prev = existing.get(key)
            if prev is not None and prev.get("status") == "approved" \
                    and prev.get("action_digest") == action_digest:
                queue.append(prev)
                return
            req = build_approval_request(
                ctx.spec.run_id, key, action_digest=action_digest,
                evidence_digest=evidence_digest,
                policy_version=policy_version, tenant_id=tenant)
            if req["approval_id"] not in {q["approval_id"] for q in queue}:
                queue.append(req)
                self.store.record_approval(ctx.spec.run_id, req)

        for c in ctx.claims:
            if c.status != "needs_review":
                continue
            triggers = c.verification.get("policy", {}) \
                .get("review_required", []) or ["semantic_support_review"]
            for trig in triggers:
                key = trig if approval_allowed(trig)[0] or \
                    trig == "citation_failure" else \
                    "semantic_support_review"
                _add(key)
        for flag in ctx.outputs.get("identity_and_attribution_check", {}) \
                .get("identity_report", {}).get("identity_flags", []):
            _add("identity_needs_review")
        approved = set(ctx.state.get("approved_items", []))
        pending = [q for q in queue if q["trigger"] not in approved]
        out: Dict[str, Any] = {"review_queue": queue,
                               "pending": [q["trigger"] for q in pending]}
        if pending and ctx.spec.human_review_policy != "never":
            out["_run_status"] = "paused"
        return out

    def _n_release(self, ctx: "_RunContext") -> Dict:
        # P1-8 拒答路徑：intake 分診攔截 → 直接產出拒答信封（拒答是安全
        # 結論，pass）；未跑檢索/綁定節點，answer 取分診訊息
        if ctx.state.get("refused"):
            answer = ctx.state.get("refusal_message", "該請求已被拒絕。")
            verdict = evaluate_release(ctx.spec, [], answer,
                                       refused=True)
            envelope = AnswerEnvelope(
                answer=answer, answer_type="refusal",
                limitations=["前置分診攔截：未執行任何檢索"],
                run={"run_id": ctx.spec.run_id,
                     "corpus_version":
                         ctx.spec.environment_fingerprint.get("corpus", "")},
                release=verdict)
            ctx.state["envelope"] = envelope.to_dict()
            ctx.state["final_answer"] = answer
            return {"envelope": envelope.to_dict(),
                    "decision": verdict["decision"],
                    "_run_status": "completed"}
        answer = ctx.outputs["citation_bind"]["bound_answer"]
        problems = ctx.ledger.verify_integrity()
        verdict = evaluate_release(
            ctx.spec, ctx.claims, answer,
            ledger_problems=problems,
            approved=frozenset(ctx.state.get("approved_items", [])))
        coverage_dicts = [c.to_dict() for c in
                          ctx.broker.coverages.values()]
        scope = {}
        if coverage_dicts:
            cov = coverage_dicts[0]
            neg = negative_statement(SearchCoverage.from_dict(cov))
            scope = {"coverage_id": cov["coverage_id"],
                     "scope_statement": neg.get("statement", ""),
                     "works_scanned": cov.get("works_scanned"),
                     "scan_capped": cov.get("scan_capped")}
        limitations = ["「在庫首現」不代表歷史絕對首現"]
        if not coverage_dicts:
            limitations.append("本輪無覆蓋記錄：語料庫未就緒或未執行檢索")
        envelope = AnswerEnvelope(
            answer=answer,
            answer_type=("negative_result"
                         if all(c.claim_type == "negative_result"
                                for c in ctx.claims) and ctx.claims
                         else "research_synthesis"),
            claims=[{"claim_id": c.claim_id, "text": c.claim_text,
                     "status": c.status,
                     "evidence_ids": c.supporting_evidence}
                    for c in ctx.claims],
            evidence=[evidence_entry(r.to_dict())
                      for r in ctx.ledger.all_records()
                      if r.is_primary_text_returned],
            scope=scope,
            uncertainty=[q["reason"] for q in
                         ctx.outputs.get("human_review", {})
                         .get("review_queue", [])],
            limitations=limitations,
            run={"run_id": ctx.spec.run_id,
                 "corpus_version":
                     ctx.spec.environment_fingerprint.get("corpus", ""),
                 "tool_spec_version":
                     ctx.spec.environment_fingerprint.get("tools", ""),
                 "policy_version":
                     ctx.spec.environment_fingerprint.get("policies", ""),
                 "model_versions":
                     [ctx.spec.environment_fingerprint.get("models", "")]},
            release=verdict)
        decision = verdict["decision"]
        run_status = {"pass": "completed",
                      "pass_with_warning": "completed",
                      "pass_after_human_review": "completed",
                      "review_required": "paused",
                      "blocked": "blocked",
                      "failed_closed": "failed"}[decision]
        ctx.state["envelope"] = envelope.to_dict()
        ctx.state["final_answer"] = answer
        return {"envelope": envelope.to_dict(), "decision": decision,
                "_run_status": run_status}


class _RunContext:
    def __init__(self, spec, state, ledger, broker, budget, claims, engine):
        self.spec = spec
        self.state = state
        self.ledger = ledger
        self.broker = broker
        self.budget = budget
        self.claims: List[ClaimRecord] = claims
        self.engine = engine
        # resume：已完成節點的輸出從持久化狀態重建
        self.outputs: Dict[str, Dict] = dict(state.get("node_outputs") or {})
        self.packet = None
