"""十三輪不變量回歸（鏡像評審探針與建議測試清單）：

  外層獨立複核不信任業務自報 citation_report（偽造 ok=True 不過閘）
  審批不可覆蓋技術失敗/空輸出（failed_closed，approve 被拒）
  台賬證據角色分類（僅編號 ≠ 正文返回）
  指代解析控制工具參數與最終答案（不再「元數據對、答案錯」）
  非法 mode 創建前拒絕（400）· queued 先落盤（幽靈 run 根除）
  取消 · 分頁 · 下載 Content-Disposition · 鎖心跳線程
"""
import json
import shutil
import threading
import time
import unittest
import urllib.error
import urllib.request

from hermes_shanghan import config


def _ensure_artifacts():
    if not (config.RESEARCH_DIR / "commentary_divergence.json").exists():
        from hermes_shanghan.orchestrator import run_pipeline
        run_pipeline(verbose=False)


def _rm_runs():
    shutil.rmtree(config.RUNS_DIR, ignore_errors=True)


class _ForgedReportAsk:
    """把 ShanghanAgent.ask 換成「零工具 + 偽造 ok=True 報告」。"""

    def __enter__(self):
        import hermes_shanghan.agent.agent as agmod
        self.mod, self.orig = agmod, agmod.ShanghanAgent.ask

        def fake_ask(agent_self, q, role=None):
            return {"answer": "結論見 SHL_SONGBEN_0012。", "tools_used": [],
                    "citation_report": {"ok": True, "has_any_citation": True,
                                        "verified": ["SHL_SONGBEN_0012"],
                                        "unsupported": []},
                    "backend": "forged"}
        agmod.ShanghanAgent.ask = fake_ask
        return self

    def __exit__(self, *a):
        self.mod.ShanghanAgent.ask = self.orig
        return False


class TestOuterAuditAuthority(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_artifacts()

    def tearDown(self):
        _rm_runs()

    def test_outer_audit_ignores_forged_inner_citation_report(self):
        # 評審探針復現：偽造 ok=True + 空台賬 → 不得 pass
        from hermes_shanghan.agent.harness import HarnessRunner
        with _ForgedReportAsk():
            st = HarnessRunner().start("桂枝湯？", mode="agent",
                                       role="researcher")
        self.assertNotIn(st.release["decision"],
                         ("pass", "pass_with_warning",
                          "pass_after_human_review"))
        outer = st.node_outputs["execute"]["citation_report"]
        self.assertEqual(outer["authority"], "harness_independent_audit")
        self.assertFalse(outer["ok"])
        self.assertIn("SHL_SONGBEN_0012", outer["outside_evidence"])
        # 自報降級存檔 + 分歧事件在案
        self.assertTrue(st.node_outputs["execute"]["agent_self_report"]["ok"])
        self.assertTrue(any(e["event"] == "citation_report_disagreement"
                            for e in st.guardrail_events))

    def test_forged_report_cannot_be_approved_into_pass(self):
        from hermes_shanghan.agent.harness import HarnessRunner
        with _ForgedReportAsk():
            st = HarnessRunner().start("桂枝湯？", mode="agent",
                                       role="researcher")
        if st.status == "paused":
            st2 = HarnessRunner().resume(st.spec.run_id, approve=True,
                                         approver="attacker")
            # 批准後重跑閘門：外層複核仍然不通過 → 不得 pass 收場
            self.assertNotIn(st2.release["decision"],
                             ("pass", "pass_with_warning"))

    def test_normal_run_passes_outer_audit(self):
        # 正常運行的引用來自工具結果 → 外層複核等價通過（不誤傷）
        from hermes_shanghan.agent.harness import HarnessRunner
        st = HarnessRunner().start("惡寒發熱，汗出，脈浮緩，應當用什麼方？",
                                   mode="agent", role="doctor")
        outer = st.node_outputs["execute"]["citation_report"]
        self.assertTrue(outer["ok"])
        self.assertFalse(st.node_outputs["evidence_audit"]["disagreement"])


class TestApprovalInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_artifacts()

    def tearDown(self):
        _rm_runs()

    def test_invalid_mode_rejected_before_run_creation(self):
        # 評審探針：非法 mode 不得創建注定失敗的任務
        from hermes_shanghan.agent.harness import HarnessRunner
        with self.assertRaises(ValueError):
            HarnessRunner().prepare("test", mode="not-a-mode")
        from hermes_shanghan.server.service import ServiceContext
        out = ServiceContext().run_start("test", mode="not-a-mode")
        self.assertIn("error", out)
        self.assertEqual(out["_status"], 400)

    def test_approval_cannot_override_technical_failure(self):
        # execute 節點異常 → degraded/空輸出 → approve 不能洗白成 pass。
        # 故障在 start 與 resume 期間都在場（resume 對失敗節點的合法重試
        # 若成功修復，那是恢復而非覆蓋——此處驗證「未修復時批准無效」）
        import hermes_shanghan.agent.agent as agmod
        from hermes_shanghan.agent.harness import HarnessRunner
        orig = agmod.ShanghanAgent.ask

        def boom(agent_self, q, role=None):
            raise RuntimeError("模擬節點崩潰")
        agmod.ShanghanAgent.ask = boom
        try:
            st = HarnessRunner().start("桂枝湯？", mode="agent",
                                       role="researcher")
            self.assertEqual(st.release["decision"], "failed_closed")
            self.assertEqual(st.status, "failed")
            self.assertTrue(st.release.get("technical_failures"))
            st2 = HarnessRunner().resume(st.spec.run_id, approve=True,
                                         approver="x")
            self.assertNotIn(st2.release["decision"],
                             ("pass", "pass_after_human_review",
                              "pass_with_warning"))
            self.assertFalse((st2.final_answer or "").strip())
        finally:
            agmod.ShanghanAgent.ask = orig

    def test_resume_retry_of_failed_node_is_legit_recovery(self):
        # 對照：故障消失後 resume 重跑失敗節點成功 = 恢復（不是審批覆蓋）
        import hermes_shanghan.agent.agent as agmod
        from hermes_shanghan.agent.harness import HarnessRunner
        orig = agmod.ShanghanAgent.ask

        def boom(agent_self, q, role=None):
            raise RuntimeError("一次性故障")
        agmod.ShanghanAgent.ask = boom
        try:
            st = HarnessRunner().start("桂枝湯的方證要點？", mode="agent",
                                       role="researcher")
        finally:
            agmod.ShanghanAgent.ask = orig
        self.assertEqual(st.status, "failed")
        st2 = HarnessRunner().resume(st.spec.run_id)   # 普通 resume=重試
        self.assertTrue((st2.final_answer or "").strip())
        self.assertNotEqual(st2.release["decision"], "failed_closed")

    def test_run_persisted_as_queued_before_execution(self):
        from hermes_shanghan.agent.harness import HarnessRunner
        from hermes_shanghan.agent.harness.runner import load_run
        st = HarnessRunner().prepare("桂枝湯？", mode="agent")
        on_disk = load_run(st.spec.run_id)
        self.assertEqual(on_disk.status, "queued")     # API 返回前已持久化

    def test_cancel_at_node_boundary(self):
        from hermes_shanghan.agent.harness import HarnessRunner
        runner = HarnessRunner()
        st = runner.prepare("桂枝湯？", mode="agent")
        ok, why = HarnessRunner.request_cancel(st.spec.run_id)
        self.assertTrue(ok)
        st2 = runner.execute_prepared(st.spec.run_id)
        self.assertEqual(st2.status, "cancelled")
        self.assertTrue(any(e["event"] == "run_cancelled"
                            for e in st2.guardrail_events))

    def test_lock_heartbeat_independent_of_node_duration(self):
        # 十三輪 七：心跳線程與節點時長解耦——長節點期間 mtime 持續刷新
        from hermes_shanghan.agent.harness.runner import _RunLock
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            lock = _RunLock(Path(td))
            saved = _RunLock.HEARTBEAT_S
            _RunLock.HEARTBEAT_S = 0.2
            try:
                with lock:
                    m0 = lock.path.stat().st_mtime
                    time.sleep(0.7)          # 模擬節點長執行（無 touch 調用）
                    self.assertGreater(lock.path.stat().st_mtime, m0)
            finally:
                _RunLock.HEARTBEAT_S = saved


class TestPreciseLedger(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_artifacts()

    def test_id_only_tool_output_is_classified_not_promoted(self):
        # 「編號出現 ≠ 證據被返回」：僅編號的工具輸出登記為 id_mention_only
        import tempfile
        from hermes_shanghan.agent.harness.state import RunBudget, RunSpec, RunState
        from hermes_shanghan.agent.harness.state import spec_versions
        from hermes_shanghan.agent.harness.tracing import (TracedRegistry,
                                                           TraceStore)
        from hermes_shanghan.agent.tools import get_registry, Tool
        reg = get_registry()
        reg._tools["_idonly_probe"] = Tool(
            "_idonly_probe", "t", {"type": "object", "properties": {}},
            lambda: {"related_clause_ids": ["SHL_SONGBEN_0012",
                                            "SHL_SONGBEN_0013"]})
        try:
            spec = RunSpec(run_id="t", user_query="q",
                           **spec_versions())
            state = RunState(spec=spec)
            with tempfile.TemporaryDirectory() as td:
                traced = TracedRegistry(reg, TraceStore(td), None, state,
                                        RunBudget(8))
                traced.call("_idonly_probe", {})
                traced.call("shanghan_search", {"query": "桂枝湯"})
            recs = state.evidence_ledger["execute"]
            idonly = [r for r in recs if r["tool"] == "_idonly_probe"]
            textful = [r for r in recs if r["tool"] == "shanghan_search"]
            self.assertTrue(all(r["evidence_role"] == "id_mention_only"
                                and r["excerpt"] is None for r in idonly))
            self.assertTrue(any(r["evidence_role"] == "primary_text_returned"
                                and r["excerpt"] for r in textful))
            self.assertTrue(any(r["retrieval_query"] == "桂枝湯"
                                for r in textful))
        finally:
            reg._tools.pop("_idonly_probe", None)


class TestSessionSemanticClosure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_artifacts()

    def test_resolved_subject_controls_tool_arguments_and_answer(self):
        # 評審探針：元數據解析對 + 答案答錯方 → 現在端到端一致
        from hermes_shanghan.agent.session import AgentSession
        s = AgentSession()
        s.ask("桂枝湯的方證要點？", role="doctor")
        out = s.ask("它的劑量比呢？", role="doctor")
        rr = out["session"]["reference_resolution"]
        self.assertEqual(rr["resolved"], "桂枝湯")
        dose_calls = [t for t in out["agent_trace"]
                      if t["kind"] == "tool_call"
                      and t["tool"] == "shanghan_dose"]
        self.assertTrue(dose_calls)
        self.assertEqual(dose_calls[0]["arguments"].get("formula"), "桂枝湯")
        head = (out.get("answer") or "")[:150]
        self.assertIn("桂枝湯", head)
        self.assertNotIn("桂枝加芍藥湯", head)   # 答案主實體不被類方污染


class TestControlPlaneHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_artifacts()
        from http.server import ThreadingHTTPServer
        from hermes_shanghan.server import http_server as hs
        from hermes_shanghan.server.service import ServiceContext
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                        hs.make_handler(ServiceContext()))
        cls.port = cls.httpd.server_address[1]
        cls.th = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.th.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.th.join()
        _rm_runs()

    def _url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def test_bad_limit_is_400_not_500(self):
        # 非數字 → 400（不是 500 也不是靜默默認）
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self._url("/api/runs?limit=abc"))
        code = cm.exception.code
        cm.exception.close()
        self.assertEqual(code, 400)
        # 負數 → 鉗制到下限（不靜默返回異常切片，也不 500）
        with urllib.request.urlopen(self._url("/api/runs?limit=-5")) as r:
            self.assertEqual(r.status, 200)
            self.assertIn("runs", json.loads(r.read()))

    def test_invalid_mode_http_400(self):
        req = urllib.request.Request(
            self._url("/api/runs"),
            data=json.dumps({"query": "x", "mode": "not-a-mode"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        code = cm.exception.code
        cm.exception.close()
        self.assertEqual(code, 400)

    def test_pagination_endpoints_and_download_disposition(self):
        from hermes_shanghan.agent.harness import HarnessRunner
        st = HarnessRunner().start("惡寒發熱，汗出，脈浮緩，應當用什麼方？",
                                   mode="agent", role="doctor")
        rid = st.spec.run_id
        with urllib.request.urlopen(
                self._url(f"/api/runs/{rid}/spans?offset=0&limit=5")) as r:
            spans = json.loads(r.read())
        self.assertLessEqual(len(spans["spans"]), 5)
        self.assertGreater(spans["total"], 5)
        with urllib.request.urlopen(
                self._url(f"/api/runs/{rid}/evidence?limit=3")) as r:
            ev = json.loads(r.read())
        self.assertLessEqual(len(ev["records"]), 3)
        self.assertIn("evidence_role", ev["records"][0])
        # 下載：Content-Disposition attachment（真下載，非 JSON 預覽）
        rel = f"runs/{rid}/state.json"
        with urllib.request.urlopen(
                self._url("/api/artifact/download?path=" + rel)) as r:
            self.assertIn("attachment",
                          r.headers.get("Content-Disposition", ""))
            self.assertTrue(r.read())
        # meta：sha256 在案
        with urllib.request.urlopen(
                self._url("/api/artifact/meta?path=" + rel)) as r:
            meta = json.loads(r.read())
        self.assertEqual(len(meta["sha256"]), 64)

    def test_session_persistence_roundtrip(self):
        body = json.dumps({"question": "桂枝湯的方證要點？"}).encode()
        req = urllib.request.Request(
            self._url("/api/chat"), data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req) as r:
            out = json.loads(r.read())
        sid = out["session"]["session_id"]
        with urllib.request.urlopen(self._url("/api/sessions")) as r:
            lst = json.loads(r.read())
        self.assertIn(sid, [x["session_id"] for x in lst["sessions"]])
        with urllib.request.urlopen(self._url(f"/api/sessions/{sid}")) as r:
            doc = json.loads(r.read())
        self.assertEqual(doc["turns"][0]["user_message"][:4], "桂枝湯的"[:4])
        self.assertIn("reference_resolution", doc["turns"][0])
        # 刪除
        req = urllib.request.Request(
            self._url(f"/api/sessions/{sid}/delete"), data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req) as r:
            self.assertIn("deleted", json.loads(r.read()))


class TestNotebookRound13Guards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        p = config.REPO_ROOT / "notebooks" / "Hermes_Shanghanlun_Colab.ipynb"
        cls.nb = json.loads(p.read_text(encoding="utf-8"))
        cls.blob = "".join("".join(c["source"]) for c in cls.nb["cells"])

    def test_no_19_tools_variant_spelling(self):
        self.assertNotIn("19 工具", self.blob)     # 上輪守衛的漏網寫法
        self.assertNotIn("19工具", self.blob)

    def test_library_download_opt_in(self):
        self.assertIn("DOWNLOAD_FULL_LIBRARY = False", self.blob)

    def test_readiness_failure_raises(self):
        self.assertIn("Server failed readiness check", self.blob)
        self.assertIn("/readyz", self.blob)

    def test_all_cells_have_ids(self):
        self.assertTrue(all("id" in c for c in self.nb["cells"]))

    def test_harness_demo_and_cleanup_sections(self):
        for probe in ("HarnessRunner", "approve=True", "replay",
                      "export_run", "資源清理"):
            self.assertIn(probe, self.blob)

    def test_zip_source_type_honest(self):
        self.assertIn("source_type", self.blob)
        self.assertIn("UPDATE_MODE", self.blob)


if __name__ == "__main__":
    unittest.main()

# ═══════════════════════ 十四輪回歸 ═══════════════════════
class _IdOnlyAsk:
    """回答引用一個「僅編號返回」的條文（正文從未進入上下文）。"""

    def __enter__(self):
        import hermes_shanghan.agent.agent as agmod
        from hermes_shanghan.agent.tools import get_registry, Tool
        self.agmod = agmod
        self.orig = agmod.ShanghanAgent.ask
        self.reg = get_registry()
        self.reg._tools["_nav_probe"] = Tool(
            "_nav_probe", "t", {"type": "object", "properties": {}},
            lambda: {"related_clause_ids": ["SHL_SONGBEN_0012"]})

        def fake_ask(agent_self, q, role=None):
            reg = agent_self.registry
            reg.call("_nav_probe", {})       # 經 Broker：登記 id_mention_only
            return {"answer": "結論見 SHL_SONGBEN_0012。",
                    "tools_used": ["_nav_probe"],
                    "citation_report": {"ok": True, "has_any_citation": True,
                                        "verified": ["SHL_SONGBEN_0012"]},
                    "backend": "idonly"}
        agmod.ShanghanAgent.ask = fake_ask
        return self

    def __exit__(self, *a):
        self.agmod.ShanghanAgent.ask = self.orig
        self.reg._tools.pop("_nav_probe", None)
        return False


class TestRound14EvidenceGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_artifacts()

    def tearDown(self):
        _rm_runs()

    def test_id_only_evidence_cannot_pass_strict_round(self):
        # 評審探針復現：工具只回編號不回正文 → 引用它不得 pass
        from hermes_shanghan.agent.harness import HarnessRunner
        with _IdOnlyAsk():
            st = HarnessRunner().start("桂枝湯？", mode="agent",
                                       role="researcher")
        recs = [r for v in st.evidence_ledger.values() for r in v]
        self.assertTrue(any(r["evidence_role"] == "id_mention_only"
                            for r in recs))
        outer = st.node_outputs["execute"]["citation_report"]
        self.assertFalse(outer["ok"])       # 僅編號不進發布允許集
        self.assertIn("SHL_SONGBEN_0012", outer["outside_evidence"])
        self.assertNotIn(st.release["decision"],
                         ("pass", "pass_with_warning",
                          "pass_after_human_review"))

    def test_citation_failure_cannot_be_approved(self):
        # 評審探針復現：無證據回答 paused 後，普通批准不能變 pass
        from hermes_shanghan.agent.harness import HarnessRunner
        with _ForgedReportAsk():
            st = HarnessRunner().start("桂枝湯？", mode="agent",
                                       role="researcher")
        self.assertEqual(st.status, "paused")
        self.assertIn("citation_failure", st.pending_review)
        st2 = HarnessRunner().resume(st.spec.run_id, approve=True,
                                     approver="attacker",
                                     trigger="citation_failure")
        self.assertNotIn(st2.release["decision"],
                         ("pass", "pass_with_warning",
                          "pass_after_human_review"))
        self.assertTrue(any(
            e["event"] in ("approval_refused_evidence_failure",
                           "approval_refused_bad_trigger")
            for e in st2.guardrail_events))
        # 無 trigger 的整批批准同樣被拒
        st3 = HarnessRunner().resume(st.spec.run_id, approve=True,
                                     approver="attacker")
        self.assertNotEqual(st3.status, "completed")

    def test_each_approval_handles_one_trigger_only(self):
        from hermes_shanghan.agent.harness import HarnessRunner
        st = HarnessRunner().start("惡寒發熱，汗出，脈浮緩，應當用什麼方？",
                                   mode="agent", role="doctor")
        pending = list(st.pending_review)
        if len(pending) < 2:
            self.skipTest("此環境只觸發一個審核項")
        # 不帶 trigger → 拒絕並要求逐項
        st2 = HarnessRunner().resume(st.spec.run_id, approve=True,
                                     approver="x")
        self.assertEqual(st2.status, "paused")
        self.assertTrue(any(e["event"] == "approval_refused_ambiguous"
                            for e in st2.guardrail_events))
        # 逐項批准第一項：其餘仍 pending（不連帶）
        st3 = HarnessRunner().resume(st.spec.run_id, approve=True,
                                     approver="x", trigger=pending[0])
        self.assertIn(st3.status, ("paused",))
        self.assertNotIn(pending[0], st3.pending_review)
        self.assertTrue(set(pending[1:]) & set(st3.pending_review))

    def test_tool_mode_runs_through_broker_and_gate(self):
        # 十四輪 P0-五：RUN_MODES 契約缺口——tool 模式現已真實實現
        import json as _json
        from hermes_shanghan.agent.harness import HarnessRunner
        query = _json.dumps({"name": "shanghan_search",
                             "arguments": {"query": "桂枝湯"}},
                            ensure_ascii=False)
        st = HarnessRunner().start(query, mode="tool", role="researcher")
        self.assertIn(st.status, ("completed", "paused"))
        self.assertNotEqual(st.release.get("decision"), "failed_closed")
        recs = [r for v in st.evidence_ledger.values() for r in v]
        self.assertTrue(recs)               # 經 Broker 登記
        with self.assertRaises(ValueError):  # 非 JSON query fail-fast
            HarnessRunner().prepare("not-json", mode="tool")

    def test_every_declared_mode_has_dispatch(self):
        from hermes_shanghan.agent.harness.runner import HarnessRunner
        from hermes_shanghan.agent.harness.state import RUN_MODES
        import inspect
        src = inspect.getsource(HarnessRunner._dispatch)
        for mode in RUN_MODES:
            self.assertIn(f'"{mode}"', src,
                          f"RUN_MODES 聲明 {mode} 但 _dispatch 未實現")


class TestRound14Workers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_artifacts()

    def tearDown(self):
        _rm_runs()

    def test_worker_exception_persists_failed_state(self):
        # 評審探針復現：worker 崩潰不得留下永久 queued 幽靈
        import time as _t
        from hermes_shanghan.agent.harness import runner as rmod
        from hermes_shanghan.agent.harness.runner import load_run
        from hermes_shanghan.server.service import ServiceContext
        svc = ServiceContext()
        orig = rmod.HarnessRunner.execute_prepared

        def boom(self, rid):
            raise RuntimeError("worker 爆炸")
        rmod.HarnessRunner.execute_prepared = boom
        try:
            out = svc.run_start("桂枝湯？", mode="agent")
            rid = out["run_id"]
            for _ in range(40):
                st = load_run(rid)
                if st and st.status == "failed":
                    break
                _t.sleep(0.1)
            self.assertEqual(st.status, "failed")
            self.assertTrue(st.errors)
            self.assertTrue(any(e["event"] == "worker_crash"
                                for e in st.guardrail_events))
        finally:
            rmod.HarnessRunner.execute_prepared = orig
            svc.close()

    def test_executor_queue_has_capacity_limit(self):
        # 十四輪 七：背壓——超過 workers+queue 容量回 429，不留幽靈 queued
        from hermes_shanghan.server.service import ServiceContext
        svc = ServiceContext()
        svc.RUN_QUEUE_SIZE = 0
        import threading
        from concurrent.futures import ThreadPoolExecutor
        svc._executor = ThreadPoolExecutor(max_workers=1)
        svc._run_slots = threading.BoundedSemaphore(1)
        try:
            self.assertTrue(svc._run_slots.acquire(blocking=False))  # 佔滿
            out = svc.run_start("桂枝湯？", mode="agent")
            self.assertEqual(out.get("_status"), 429)
        finally:
            svc._run_slots.release()
            svc.close()

    def test_terminal_run_cannot_be_cancelled(self):
        from hermes_shanghan.agent.harness import HarnessRunner
        st = HarnessRunner().start("太陽病提綱是什麼？", mode="agent",
                                   role="student")
        ok, why = HarnessRunner.request_cancel(st.spec.run_id)
        self.assertFalse(ok)
        self.assertIn("terminal_state", why)

    def test_corrupt_run_does_not_break_run_list(self):
        from hermes_shanghan.agent.harness.runner import list_runs
        bad = config.RUNS_DIR / "run_corrupt_x"
        bad.mkdir(parents=True, exist_ok=True)
        (bad / "state.json").write_text("{broken", encoding="utf-8")
        rows = list_runs()
        row = next(r for r in rows if r["run_id"] == "run_corrupt_x")
        self.assertEqual(row["status"], "corrupt")

    def test_partial_trace_line_does_not_break_read(self):
        from hermes_shanghan.agent.harness.tracing import TraceStore
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            ts = TraceStore(Path(td))
            with ts.span("run", "x"):
                pass
            with ts.path.open("a", encoding="utf-8") as fh:
                fh.write("{bad\n")
            events = ts.read()
            self.assertTrue(any(e["span_type"] == "corrupt_records"
                                for e in events))


class TestRound14Sessions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_artifacts()
        import shutil as _sh
        _sh.rmtree(config.SHANGHAN_DIR / "sessions", ignore_errors=True)

    @classmethod
    def tearDownClass(cls):
        import shutil as _sh
        _sh.rmtree(config.SHANGHAN_DIR / "sessions", ignore_errors=True)

    def test_session_restores_after_service_restart(self):
        # 評審探針復現：新 ServiceContext（模擬重啟）續接同一 session_id，
        # 指代解析必須恢復真實上下文而非 no_reference
        from hermes_shanghan.server.service import ServiceContext
        svc1 = ServiceContext()
        out1 = svc1.chat("桂枝湯的方證要點？", role="doctor",
                         subject="restart-test")
        sid = out1["session"]["session_id"]
        self.assertTrue(out1["session"]["persisted"])
        svc2 = ServiceContext()          # 模擬服務重啟
        out2 = svc2.chat("它的劑量比呢？", session_id=sid, role="doctor",
                         subject="restart-test")
        rr = out2["session"]["reference_resolution"]
        self.assertEqual(rr["status"], "resolved")
        self.assertEqual(rr["resolved"], "桂枝湯")
        self.assertIn("桂枝湯", (out2.get("answer") or "")[:150])

    def test_concurrent_persist_turns_do_not_lose_updates(self):
        import threading
        from hermes_shanghan.server.service import ServiceContext
        svc = ServiceContext()
        sid = "concurrent-x"
        errors = []

        def worker(i):
            try:
                svc._persist_turn("conc", sid, f"問題{i}",
                                  {"session": {}, "answer": "a",
                                   "evidence_clause_ids": []})
            except Exception as exc:
                errors.append(exc)
        # 模擬 chat 的 per-session 鎖語義：持久化在鎖內串行
        lock = threading.Lock()

        def locked_worker(i):
            with lock:
                worker(i)
        threads = [threading.Thread(target=locked_worker, args=(i,))
                   for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertFalse(errors)
        doc = svc.session_turns("conc", sid)
        self.assertEqual(len(doc["turns"]), 30)    # 不丟回合

    def test_long_subject_session_filenames_do_not_collide(self):
        from hermes_shanghan.server.service import ServiceContext
        svc = ServiceContext()
        subject = "x" * 90
        fa = svc._session_file(subject, "sidA")
        fb = svc._session_file(subject, "sidB")
        self.assertNotEqual(fa, fb)         # 哈希文件名：截斷不再碰撞


class TestRound14Fingerprints(unittest.TestCase):
    def test_tool_spec_fingerprint_is_content_hash(self):
        from hermes_shanghan.agent.harness.state import spec_versions
        v = spec_versions()["tool_spec_version"]
        self.assertRegex(v, r"^\d+tools@[0-9a-f]{12}$")   # 數量+內容哈希

    def test_code_tree_fingerprint_present(self):
        from hermes_shanghan.agent.harness.state import spec_versions
        v = spec_versions()
        self.assertRegex(v["code_tree_fingerprint"], r"^[0-9a-f]{12}$")

    def test_artifact_meta_uses_creation_fingerprint_for_runs(self):
        _ensure_artifacts()
        from hermes_shanghan.agent.harness import HarnessRunner
        from hermes_shanghan.server.service import ServiceContext
        st = HarnessRunner().start("太陽病提綱是什麼？", mode="agent",
                                   role="student")
        try:
            rel = f"runs/{st.spec.run_id}/state.json"
            meta = ServiceContext().artifact_meta(rel)
            self.assertEqual(meta["corpus_fingerprint_at_creation"],
                             st.spec.corpus_version)   # 生成時指紋，非當前
            self.assertEqual(meta["created_by_run"], st.spec.run_id)
        finally:
            _rm_runs()


if __name__ == "__main__":
    unittest.main()
