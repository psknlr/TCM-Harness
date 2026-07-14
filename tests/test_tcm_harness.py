"""Typed DAG Harness（P0-6）：15 節點研究圖 / durable 執行 / 審批 / replay。"""
import tempfile
import unittest
from pathlib import Path

from hermes_tcm.core.principals import Principal
from hermes_tcm.harness.approvals import approval_allowed
from hermes_tcm.harness.checkpoint import (LeaseHeldError, RunStore,
                                           StaleStateError)
from hermes_tcm.harness.controller import (ResearchRunController,
                                           classify_task, extract_topic)
from hermes_tcm.harness.graph import RESEARCH_GRAPH, validate_graph
from hermes_tcm.harness.replay import replay_evidence, replay_policy

try:
    from tests.test_tcm_fixture import TCMFixtureCase
except ImportError:
    from test_tcm_fixture import TCMFixtureCase

EXPECTED_NODES = [n.node_id for n in RESEARCH_GRAPH]


class TestGraphStatics(unittest.TestCase):
    def test_graph_valid(self):
        self.assertEqual(validate_graph(), [])

    def test_protocol_node_sequence(self):
        """Protocol §10 的 15 節點序全部在。"""
        for node_id in ("intake", "task_classify", "scope_contract",
                        "plan_compile", "catalog_resolution",
                        "retrieval_fanout",
                        "identity_and_attribution_check",
                        "counterevidence_search", "claim_compile",
                        "claim_verify", "synthesis", "citation_bind",
                        "safety_and_policy", "human_review", "release"):
            self.assertIn(node_id, EXPECTED_NODES)
        self.assertEqual(len(EXPECTED_NODES), 15)

    def test_node_contracts_complete(self):
        for n in RESEARCH_GRAPH:
            self.assertTrue(n.release_condition, n.node_id)
            d = n.to_dict()
            for key in ("input_schema", "output_schema", "dependencies",
                        "tool_scope", "timeout_ms", "retry_policy",
                        "fallback_policy", "cancellation_boundary"):
                self.assertIn(key, d)

    def test_task_classification(self):
        self.assertEqual(classify_task("「奔豚」最早見於哪部書"),
                         "earliest_attestation")
        self.assertEqual(classify_task("傷寒論各傳本異文比較"),
                         "witness_comparison")
        self.assertEqual(classify_task("桂枝湯的方劑源流"),
                         "formula_lineage")
        self.assertEqual(classify_task("查一下奔豚"), "general_search")

    def test_topic_extraction(self):
        self.assertEqual(extract_topic("「奔豚」一詞最早見於哪部書？"),
                         "奔豚")


class TestRunStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = RunStore(Path(self._tmp.name) / "runs.db")

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_cas_rejects_stale_write(self):
        self.store.create_run("run_x", {"query": "q"})
        self.store.save_state("run_x", "running", {"a": 1}, 0)
        with self.assertRaises(StaleStateError):
            self.store.save_state("run_x", "running", {"a": 2}, 0)

    def test_lease_conflict(self):
        self.store.create_run("run_x", {})
        self.store.acquire_lease("run_x", "n1", "holder_a")
        with self.assertRaises(LeaseHeldError):
            self.store.acquire_lease("run_x", "n1", "holder_b")
        self.store.release_lease("run_x", "n1", "holder_a")
        self.store.acquire_lease("run_x", "n1", "holder_b")   # 釋放後可取

    def test_event_sourcing_append_only(self):
        self.store.create_run("run_x", {})
        self.store.append_event("run_x", "e1", {"k": 1})
        self.store.append_event("run_x", "e2", {"k": 2})
        events = self.store.events("run_x")
        self.assertEqual([e["event_type"] for e in events], ["e1", "e2"])

    def test_idempotent_attempt_replay(self):
        self.store.create_run("run_x", {})
        self.store.record_attempt("run_x", "n1", 1, "ok",
                                  output={"v": 42}, idempotency_key="k1")
        self.assertEqual(
            self.store.completed_attempt("run_x", "n1", "k1"), {"v": 42})
        self.assertIsNone(
            self.store.completed_attempt("run_x", "n1", "other"))


class TestFullRun(TCMFixtureCase):
    """全圖端到端（fixture 全庫）。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmpdb = tempfile.TemporaryDirectory()
        cls.store = RunStore(Path(cls._tmpdb.name) / "runs.db")
        cls.ctrl = ResearchRunController(cls.store)

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        cls._tmpdb.cleanup()
        super().tearDownClass()

    def test_earliest_attestation_happy_path(self):
        row = self.ctrl.start("「奔豚」一詞最早見於哪部書？",
                              Principal(subject="r1", role="researcher"))
        state = row["state"]
        # 全部 15 節點跑完
        for node_id in EXPECTED_NODES:
            self.assertEqual(state["nodes"][node_id]["status"], "ok",
                             node_id)
        env = state["envelope"]
        self.assertEqual(row["status"], "completed")
        self.assertEqual(env["release"]["decision"], "pass")
        # 主張已驗證且綁定證據
        verified = [c for c in env["claims"] if c["status"] == "verified"]
        self.assertTrue(verified)
        self.assertTrue(verified[0]["evidence_ids"])
        # 首見結論帶強制限定語 + 最早著作正確（東漢《漢方遺編》）
        self.assertIn("在當前語料庫範圍內", env["answer"])
        self.assertIn("漢方遺編", env["answer"])
        # 信封聲明範圍與誠實邊界
        self.assertTrue(env["scope"]["coverage_id"])
        self.assertTrue(any("歷史絕對首現" in x
                            for x in env["limitations"]))
        # 環境指紋凍結
        self.assertTrue(env["run"]["policy_version"])

    def test_run_state_durably_persisted(self):
        row = self.ctrl.start("查一下中風",
                              Principal(subject="r2", role="researcher"))
        run_id = row["run_id"]
        reloaded = self.store.load(run_id)
        self.assertEqual(reloaded["status"], row["status"])
        self.assertIn("ledger", reloaded["state"])
        events = self.store.events(run_id)
        self.assertEqual(events[0]["event_type"], "run_prepared")
        self.assertEqual(events[-1]["event_type"], "run_finished")

    def test_cancel_requested_stops_at_node_boundary(self):
        spec = self.ctrl.prepare("「奔豚」最早見於哪部書",
                                 Principal(subject="r3",
                                           role="researcher"))
        self.assertTrue(self.ctrl.request_cancel(spec.run_id))
        row = self.ctrl.execute(spec.run_id)
        self.assertEqual(row["status"], "cancelled")

    def test_citation_failure_not_approvable(self):
        ok, why = approval_allowed("citation_failure")
        self.assertFalse(ok)
        self.assertIn("補證據", why)

    def test_adjudication_triggers_approvable(self):
        for trig in ("earlier_partial_candidate", "identity_needs_review",
                     "uncertain_work_date"):
            self.assertTrue(approval_allowed(trig)[0], trig)

    def test_witness_comparison_pauses_on_homonym(self):
        """同名異書 → identity_needs_review 審批隊列 → paused。"""
        row = self.ctrl.start("同名醫鑑各傳本異文比較",
                              Principal(subject="r4", role="researcher"))
        self.assertEqual(row["status"], "paused")
        approvals = self.store.approvals(row["run_id"])
        self.assertTrue(any(a["trigger"] == "identity_needs_review"
                            for a in approvals))

    def test_resume_approve_reruns_downstream(self):
        row = self.ctrl.start("同名醫鑑各傳本異文比較",
                              Principal(subject="r5", role="researcher"))
        self.assertEqual(row["status"], "paused")
        row2 = self.ctrl.resume(row["run_id"],
                                approve="identity_needs_review",
                                approver="tester", reason="測試裁決")
        # 批准後下游重跑，run 走到終態
        self.assertIn(row2["status"], ("completed", "paused", "blocked"))
        events = [e["event_type"] for e in
                  self.store.events(row["run_id"])]
        self.assertIn("human_review_approved", str(
            self.store.load(row["run_id"])["state"]["guardrail_events"]))

    def test_resume_reject_terminal(self):
        row = self.ctrl.start("同名醫鑑各傳本異文比較",
                              Principal(subject="r6", role="researcher"))
        row2 = self.ctrl.resume(row["run_id"],
                                reject="identity_needs_review",
                                approver="tester", reason="不通過")
        self.assertEqual(row2["status"], "rejected")

    def test_replay_evidence_mode(self):
        row = self.ctrl.start("「奔豚」一詞最早見於哪部書？",
                              Principal(subject="r7", role="researcher"))
        rep = replay_evidence(self.store, row["run_id"])
        self.assertTrue(rep["verification"]["ok"])
        self.assertGreater(rep["n_records"], 0)

    def test_replay_policy_mode(self):
        row = self.ctrl.start("「奔豚」一詞最早見於哪部書？",
                              Principal(subject="r8", role="researcher"))
        rep = replay_policy(self.store, row["run_id"])
        self.assertIn("policy_fingerprint", rep)

    def test_budget_snapshot_persisted(self):
        row = self.ctrl.start("查一下中風",
                              Principal(subject="r9", role="researcher"))
        b = row["state"]["budget"]
        self.assertGreater(b["used_tool_calls"], 0)
        self.assertLessEqual(b["used_tool_calls"], b["max_tool_calls"])


if __name__ == "__main__":
    unittest.main()
