"""P0/P1 加固回歸（審查修復）：認證/授權、審批不可偽造、租戶隔離、
scope 強制、回源核驗、節點上下文、MCP、超時、前置分診、能力標籤。"""
import tempfile
import time
import unittest
from pathlib import Path

from hermes_tcm.core.auth import (AuthError, AuthRegistry, AuthzError,
                                  AuthenticatedPrincipal)
from hermes_tcm.core.principals import Principal
from hermes_tcm.evidence.ledger import TypedEvidenceLedger
from hermes_tcm.tools.broker import CapabilityBroker
from hermes_tcm.tools.registry import get_tcm_registry

try:
    from tests.test_tcm_fixture import TCMFixtureCase
except ImportError:
    from test_tcm_fixture import TCMFixtureCase


# ---------------------------------------------------------------------------
# P0-1 認證/授權
# ---------------------------------------------------------------------------
class TestAuth(unittest.TestCase):
    def _reg(self):
        return AuthRegistry([{
            "token": "t1", "subject": "u1", "tenant_id": "tenA",
            "max_role": "researcher",
            "allowed_purposes": ["historical_research", "teaching"]}])

    def test_role_downgrade_ok_escalation_blocked(self):
        ap = self._reg().authenticate("Bearer t1")
        self.assertEqual(ap.resolve(requested_role="student").role,
                         "student")
        with self.assertRaises(AuthzError):
            ap.resolve(requested_role="system_admin")
        with self.assertRaises(AuthzError):
            ap.resolve(requested_role="clinician")

    def test_purpose_must_be_allowed(self):
        ap = self._reg().authenticate("Bearer t1")
        self.assertEqual(ap.resolve(requested_purpose="teaching")
                         .purpose_of_use, "teaching")
        with self.assertRaises(AuthzError):
            ap.resolve(requested_purpose="clinical_reference")

    def test_bad_token_401(self):
        with self.assertRaises(AuthError):
            self._reg().authenticate("Bearer nope")
        with self.assertRaises(AuthError):
            self._reg().authenticate("")

    def test_anonymous_mode_public_ceiling(self):
        anon = AuthRegistry([]).authenticate("")
        self.assertTrue(anon.anonymous)
        self.assertEqual(anon.max_role, "public")
        with self.assertRaises(AuthzError):
            anon.resolve(requested_role="researcher")

    def test_from_env_invalid_json(self):
        import os
        os.environ["HERMES_TCM_TOKENS"] = "{not json"
        try:
            with self.assertRaises(ValueError):
                AuthRegistry.from_env()
        finally:
            os.environ.pop("HERMES_TCM_TOKENS", None)


# ---------------------------------------------------------------------------
# P0-2 審批不可偽造
# ---------------------------------------------------------------------------
class TestApprovalIntegrity(unittest.TestCase):
    def _req(self, **kw):
        from hermes_tcm.harness.approvals import build_approval_request
        defaults = dict(run_id="run_1", trigger="identity_needs_review",
                        action_digest="dig1", tenant_id="tenA")
        defaults.update(kw)
        return build_approval_request(**defaults)

    def test_citation_failure_never_approvable(self):
        from hermes_tcm.harness.approvals import verify_approval
        req = self._req(trigger="citation_failure")
        ok, why = verify_approval(req, "citation_failure",
                                  current_action_digest="dig1")
        self.assertFalse(ok)
        self.assertIn("補證據", why)

    def test_stale_action_digest_refused(self):
        from hermes_tcm.harness.approvals import verify_approval
        req = self._req()
        ok, why = verify_approval(req, "identity_needs_review",
                                  current_action_digest="CHANGED")
        self.assertFalse(ok)
        self.assertIn("action_digest", why)

    def test_expired_refused(self):
        from hermes_tcm.harness.approvals import verify_approval
        req = self._req(ttl_s=1, now_ts=time.time() - 100)
        ok, why = verify_approval(req, "identity_needs_review",
                                  current_action_digest="dig1")
        self.assertFalse(ok)
        self.assertIn("過期", why)

    def test_single_use(self):
        from hermes_tcm.harness.approvals import verify_approval
        req = self._req()
        req["status"] = "approved"      # 已消費
        ok, why = verify_approval(req, "identity_needs_review",
                                  current_action_digest="dig1")
        self.assertFalse(ok)

    def test_reviewer_role_enforced(self):
        from hermes_tcm.harness.approvals import verify_approval
        req = self._req()
        # researcher 不具備審核資格
        researcher = Principal(subject="r", role="researcher",
                               tenant_id="tenA")
        ok, why = verify_approval(req, "identity_needs_review",
                                  reviewer=researcher,
                                  current_action_digest="dig1")
        self.assertFalse(ok)
        # editor 具備資格
        editor = Principal(subject="e", role="editor", tenant_id="tenA")
        ok2, _ = verify_approval(req, "identity_needs_review",
                                 reviewer=editor,
                                 current_action_digest="dig1")
        self.assertTrue(ok2)

    def test_cross_tenant_reviewer_refused(self):
        from hermes_tcm.harness.approvals import verify_approval
        req = self._req()
        editor_b = Principal(subject="e", role="editor", tenant_id="tenB")
        ok, why = verify_approval(req, "identity_needs_review",
                                  reviewer=editor_b,
                                  current_action_digest="dig1")
        self.assertFalse(ok)
        self.assertIn("租戶", why)


# ---------------------------------------------------------------------------
# P0-3 租戶隔離
# ---------------------------------------------------------------------------
class TestTenantIsolation(unittest.TestCase):
    def test_cross_tenant_and_owner(self):
        from hermes_tcm.harness.checkpoint import RunAccessDenied, RunStore
        with tempfile.TemporaryDirectory() as td:
            store = RunStore(Path(td) / "r.db")
            store.create_run("run_x", {"query": "q"},
                             owner_subject="u1", tenant_id="tenA")
            with self.assertRaises(RunAccessDenied):
                store.authorize("run_x",
                                Principal(subject="u2", role="researcher",
                                          tenant_id="tenB"))
            with self.assertRaises(RunAccessDenied):
                # 同租戶不同屬主也拒（非 admin）
                store.authorize("run_x",
                                Principal(subject="u2", role="researcher",
                                          tenant_id="tenA"))
            # 屬主可訪問
            self.assertIsNotNone(store.authorize(
                "run_x", Principal(subject="u1", role="researcher",
                                   tenant_id="tenA")))
            # system_admin 同租戶可訪問
            self.assertIsNotNone(store.authorize(
                "run_x", Principal(subject="adm", role="system_admin",
                                   tenant_id="tenA")))
            store.close()

    def test_migration_adds_columns(self):
        # 舊庫（無 owner/tenant 列）打開後補列不報錯
        import sqlite3
        from hermes_tcm.harness.checkpoint import RunStore
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "old.db"
            con = sqlite3.connect(str(p))
            con.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, "
                        "status TEXT NOT NULL, state_version INTEGER "
                        "DEFAULT 0, spec_json TEXT NOT NULL, state_json "
                        "TEXT DEFAULT '{}', created_at TEXT, updated_at TEXT)")
            con.commit(); con.close()
            store = RunStore(p)      # 遷移不應拋錯
            self.assertIsNone(store.run_acl("nope"))
            store.close()


# ---------------------------------------------------------------------------
# P0-4 scope 強制
# ---------------------------------------------------------------------------
class TestScopeEnforcement(TCMFixtureCase):
    def _broker(self, scope_dict):
        from hermes_tcm.harness.scope import compile_scope
        led = TypedEvidenceLedger("cv")
        b = CapabilityBroker(get_tcm_registry(), led, corpus_version="cv")
        b.scope = compile_scope(scope_dict, "cv")
        return b, led

    def test_category_scope_filters_hits_and_ledger(self):
        b, led = self._broker({"categories": ["方書"]})
        out = b.call("text.search_passages", {"query": "奔豚",
                                              "order": "dynasty"})
        self.assertTrue(all("方書" in h["category"] for h in out["hits"]))
        self.assertEqual(out["coverage"]["scope_hash"], b.scope.scope_hash)
        for r in led.all_records():
            self.assertTrue(r.category == "" or "方書" in r.category)

    def test_scope_changes_earliest(self):
        # 綜合 scope → 攻擊之書(清)成為 earliest（證明過濾真的改變結果）
        b, _ = self._broker({"categories": ["綜合"]})
        out = b.call("citation.trace_quote", {"quote": "奔豚"})
        ea = out.get("earliest_in_library")
        self.assertIsNotNone(ea)
        self.assertIn("綜合", ea["category"])

    def test_unrestricted_scope_no_filter(self):
        b, led = self._broker({})
        self.assertTrue(b.scope.is_unrestricted)
        out = b.call("text.search_passages", {"query": "奔豚"})
        self.assertNotIn("scope_filtered_out", out)


# ---------------------------------------------------------------------------
# P0-5 回源核驗
# ---------------------------------------------------------------------------
class TestSourceReverification(TCMFixtureCase):
    def test_high_risk_needs_reverify_without_index(self):
        from hermes_tcm.claims.records import ClaimRecord, claim_id_for
        from hermes_tcm.claims.verifier import ClaimVerifier
        from hermes_tcm.evidence.ledger import mint_broker_token
        from hermes_tcm.evidence.records import EvidenceRecord, quote_hash
        led = TypedEvidenceLedger("cv")
        tok = mint_broker_token()
        rec = EvidenceRecord(
            evidence_id="ev_1", corpus_version="cv",
            work_id="urn:tcm:work:a", witness_id="urn:tcm:witness:b",
            verbatim="奔豚上衝", quote_hash=quote_hash("奔豚上衝"),
            verification_level="V2", tool_call_id="tc", span_id="sp",
            registered_by="capability_broker", dynasty="東漢")
        led.register("n1", rec, tok)
        claim = ClaimRecord(
            claim_id=claim_id_for("x", "earliest_attestation"),
            claim_text="x", claim_type="earliest_attestation",
            supporting_evidence=["ev_1"], counter_search_performed=True)
        # 策略條件全部滿足（工具+覆蓋），僅缺回源核驗——無 passage_index
        # → source_reverified False → needs_review（不是策略 fail）
        from hermes_tcm.evidence.coverage import SearchCoverage
        cov = SearchCoverage(coverage_id="cov_1", corpus_versions=["cv"],
                             exhaustive_within_scope=True,
                             search_modes=["exact", "dynasty_ordered"])
        ClaimVerifier(led).verify(
            claim, coverage=cov,
            tools_used=["citation.trace_quote", "citation.counter_search"])
        self.assertEqual(claim.verification["source_reverified"], False)
        self.assertEqual(claim.verification["quotation"], "review")
        self.assertEqual(claim.status, "needs_review")

    def test_full_run_reverifies_against_library(self):
        # 全 run 用 fixture 真庫 → 首見主張 source_reverified True → pass
        from hermes_tcm.harness.checkpoint import RunStore
        from hermes_tcm.harness.controller import ResearchRunController
        with tempfile.TemporaryDirectory() as td:
            store = RunStore(Path(td) / "r.db")
            ctrl = ResearchRunController(store)
            row = ctrl.start("「奔豚」一詞最早見於哪部書？",
                             Principal(subject="r", role="researcher"))
            env = row["state"]["envelope"]
            self.assertEqual(env["release"]["decision"], "pass")
            verified = [c for c in env["claims"]
                        if c["status"] == "verified"]
            self.assertTrue(verified)
            store.close()

    def test_version_mismatch_fails(self):
        from hermes_tcm.evidence.packets import verify_packet
        from hermes_tcm.evidence.records import EvidenceRecord, quote_hash
        rec = EvidenceRecord(
            evidence_id="ev_x", corpus_version="OTHER",
            verbatim="甲", quote_hash=quote_hash("甲"),
            verification_level="V1")
        v = verify_packet([rec], expected_corpus_version="cv")
        self.assertFalse(v["ok"])
        self.assertEqual(v["version_mismatch"], 1)


# ---------------------------------------------------------------------------
# P0-6 節點上下文
# ---------------------------------------------------------------------------
class TestNodeContext(TCMFixtureCase):
    def test_node_tool_scope_denies_out_of_scope(self):
        from hermes_tcm.harness.graph import NodeContract
        led = TypedEvidenceLedger("cv")
        b = CapabilityBroker(get_tcm_registry(), led, corpus_version="cv")
        node = NodeContract(node_id="counterevidence_search",
                            node_type="counter", tool_scope=["citation"])
        nb = b.for_node(node)
        # citation 允許
        self.assertNotIn("node_tool_scope_denied",
                         nb.call("citation.trace_quote",
                                 {"quote": "奔豚"}).get("error", ""))
        # catalog 不在節點範圍 → 拒
        out = nb.call("catalog.list_categories", {})
        self.assertIn("node_tool_scope_denied", out["error"])

    def test_node_budget_enforced(self):
        from hermes_tcm.harness.graph import NodeContract
        led = TypedEvidenceLedger("cv")
        b = CapabilityBroker(get_tcm_registry(), led, corpus_version="cv")
        node = NodeContract(node_id="x", node_type="retrieve",
                            tool_scope=["text"], budget_tool_calls=1)
        nb = b.for_node(node)
        nb.call("text.search_passages", {"query": "奔豚"})
        out = nb.call("text.search_passages", {"query": "中風"})
        self.assertIn("NODE_BUDGET_EXHAUSTED", out["error"])

    def test_deadline_blocks_new_calls(self):
        from hermes_tcm.harness.graph import NodeContract
        led = TypedEvidenceLedger("cv")
        b = CapabilityBroker(get_tcm_registry(), led, corpus_version="cv")
        node = NodeContract(node_id="x", node_type="retrieve",
                            tool_scope=["text"])
        nb = b.for_node(node, deadline=time.time() - 1)   # 已過截止
        out = nb.call("text.search_passages", {"query": "奔豚"})
        self.assertIn("NODE_DEADLINE_EXCEEDED", out["error"])


# ---------------------------------------------------------------------------
# P0-7 MCP Server
# ---------------------------------------------------------------------------
class TestMCPServer(TCMFixtureCase):
    def _server(self):
        from hermes_tcm.integrations.mcp_server import MCPServer
        s = MCPServer()
        s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-06-18"}})
        s.handle({"jsonrpc": "2.0",
                  "method": "notifications/initialized"})
        return s

    def test_lifecycle_and_negotiation(self):
        from hermes_tcm.integrations.mcp_server import MCPServer
        s = MCPServer()
        r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(r["result"]["serverInfo"]["name"], "hermes-tcm")
        self.assertIn("Never state historical first occurrence",
                      r["result"]["instructions"])
        # pre-init tools/list → error
        r2 = s.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertIn("error", r2)

    def test_tools_list_call_resources(self):
        s = self._server()
        tl = s.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        names = {t["name"] for t in tl["result"]["tools"]}
        self.assertIn("citation__trace_quote", names)
        call = s.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                         "params": {"name": "text__search_passages",
                                    "arguments": {"query": "奔豚"}}})
        self.assertFalse(call["result"]["isError"])
        rr = s.handle({"jsonrpc": "2.0", "id": 5, "method": "resources/read",
                       "params": {"uri": "tcm://policies/current"}})
        self.assertIn("contents", rr["result"])

    def test_notification_and_unknown(self):
        s = self._server()
        self.assertIsNone(s.handle({"jsonrpc": "2.0",
                                    "method": "notifications/cancelled",
                                    "params": {"requestId": 4}}))
        r = s.handle({"jsonrpc": "2.0", "id": 9, "method": "bogus"})
        self.assertEqual(r["error"]["code"], -32601)


# ---------------------------------------------------------------------------
# P0-8 超時熔斷
# ---------------------------------------------------------------------------
class TestTimeout(unittest.TestCase):
    def setUp(self):
        import threading
        # 可控釋放的工作線程：tearDown set 後立即退出，不遺留跨測試
        self._release = threading.Event()

    def tearDown(self):
        from hermes_tcm.tools import broker
        self._release.set()
        # 等待所有登記的遺留線程退出並清空全局列表（防污染後續測試）
        for t in list(broker._ZOMBIE_THREADS):
            t.join(timeout=2)
        broker._ZOMBIE_THREADS.clear()

    def test_zombie_circuit_breaker(self):
        from hermes_tcm.tools import broker
        broker._prune_zombies()
        broker._ZOMBIE_THREADS.clear()

        def hang(**k):
            self._release.wait(timeout=5)      # tearDown 立即釋放
            return {}
        for _ in range(broker.MAX_ZOMBIE_THREADS):
            with self.assertRaises(broker.BrokerTimeout):
                broker._run_with_timeout(hang, {}, 0.02, read_only=True)
        with self.assertRaises(broker.BrokerTimeout):
            broker._run_with_timeout(hang, {}, 0.02, read_only=True)

    def test_write_tool_runs_synchronously(self):
        from hermes_tcm.tools import broker
        # 非只讀=同步（無後台線程可孤立）
        out = broker._run_with_timeout(lambda **k: {"ok": 1}, {}, 0.01,
                                       read_only=False)
        self.assertEqual(out, {"ok": 1})


# ---------------------------------------------------------------------------
# P1-8 前置分診 / P1-9 能力標籤
# ---------------------------------------------------------------------------
class TestIntakeTriageAndCapability(TCMFixtureCase):
    def test_patient_education_clinical_refused_before_tools(self):
        from hermes_tcm.harness.checkpoint import RunStore
        from hermes_tcm.harness.controller import ResearchRunController
        with tempfile.TemporaryDirectory() as td:
            store = RunStore(Path(td) / "r.db")
            ctrl = ResearchRunController(store)
            p = Principal(subject="pt", role="public",
                          purpose_of_use="patient_education")
            row = ctrl.start("我发烧了桂枝汤剂量多少能治吗", p)
            st = row["state"]
            self.assertEqual(row["status"], "completed")
            self.assertEqual(st["envelope"]["answer_type"], "refusal")
            self.assertEqual(st["nodes"]["retrieval_fanout"]["status"],
                             "skipped_by_triage")
            n = store._conn.execute(
                "SELECT COUNT(*) FROM tool_calls WHERE run_id=?",
                (row["run_id"],)).fetchone()[0]
            self.assertEqual(n, 0)      # 未消耗任何檢索工具
            store.close()

    def test_capability_declared_on_dosage_tool(self):
        c = get_tcm_registry().get("formula.compare_dosage")
        self.assertIn("dosage_conversion", c.capabilities)
        self.assertIn("capabilities", c.spec())

    def test_dosage_denied_under_patient_education_via_capability(self):
        led = TypedEvidenceLedger("cv")
        b = CapabilityBroker(
            get_tcm_registry(), led,
            principal=Principal(subject="x", role="researcher",
                                purpose_of_use="patient_education"),
            corpus_version="cv")
        out = b.call("formula.compare_dosage", {"formula": "桂枝湯"})
        self.assertIn("purpose_denied", out["error"])


if __name__ == "__main__":
    unittest.main()
