"""外部審計第二批修復回歸：P0-3 / P1-1 / P1-3。

P0-3 依賴倒置（守衛在 test_dependency_inversion.py；此處測行為面）。
P1-1 語義/圖檢索棧：近似語義召回 + 逐字蘊含核驗（召回信號≠證據）、
     圖多跳擴召、零命中語義回退。
P1-3 統一 V2 MCP Server：版本協商、V2 工具面、tcm:// 資源、
     durable tasks（RunStore 持久、節點邊界取消）。
"""
import json
import tempfile
import time
import unittest
from pathlib import Path

try:
    from tests.test_tcm_fixture import TCMFixtureCase
except ImportError:
    from test_tcm_fixture import TCMFixtureCase


# ---------------------------------------------------------------------------
# P1-1 語義檢索：召回信號 ≠ 證據
# ---------------------------------------------------------------------------
class TestSemanticRetrieval(TCMFixtureCase):
    def test_verbatim_entailment_yields_evidence(self):
        from hermes_tcm.retrieval import search_semantic
        out = search_semantic("奔豚")
        self.assertTrue(out.get("available"))
        self.assertGreater(out["n_verbatim"], 0)
        self.assertEqual(len(out["passage_evidence"]), out["n_verbatim"])
        for h in out["hits"]:
            if h["entailment"] == "verbatim":
                self.assertEqual(h["evidence_role"],
                                 "primary_text_returned")

    def test_near_miss_is_recall_signal_not_evidence(self):
        """近失查詢（無段落逐字包含全查詢）：只產召回信號，零證據。"""
        from hermes_tcm.retrieval import search_semantic
        out = search_semantic("奔豚灸法")
        self.assertTrue(out.get("available"))
        self.assertGreater(out["n_recall_signals"], 0)
        self.assertEqual(out["n_verbatim"], 0)
        self.assertEqual(out["passage_evidence"], [])
        for h in out["hits"]:
            self.assertEqual(h["evidence_role"], "recall_signal")

    def test_coverage_declares_semantic_modes(self):
        from hermes_tcm.retrieval import search_semantic
        cov = search_semantic("奔豚")["coverage"]
        self.assertIn("ngram_or_recall", cov["search_modes"])
        self.assertIn("verbatim_entailment_gate", cov["search_modes"])

    def test_semantic_evidence_reverifiable(self):
        """verbatim 證據按座標回庫切片必須逐字一致（可重驗）。"""
        from hermes_tcm.retrieval import search_semantic
        from hermes_tcm.tools._shared import searcher
        out = search_semantic("奔豚")
        s = searcher()
        for ev in out["passage_evidence"]:
            p = s.index.get(ev["passage_id"])
            sliced = p.flat_text[ev["char_start"]:ev["char_end"]]
            self.assertEqual(sliced, ev["verbatim_text"])

    def test_broker_registers_only_verbatim_evidence(self):
        from hermes_tcm.integrations.sdk import TCMClient
        with tempfile.TemporaryDirectory() as td:
            client = TCMClient(store_path=Path(td) / "r.db")
            try:
                out = client.call_tool("text.search_semantic",
                                       {"query": "奔豚"})
                self.assertTrue(out["evidence"])
                out2 = client.call_tool("text.search_semantic",
                                        {"query": "奔豚灸法"})
                self.assertEqual(out2["evidence"], [],
                                 "召回信號不得入證據台賬")
            finally:
                client.close()

    def test_retrieval_modes_all_ready(self):
        from hermes_tcm.retrieval import RETRIEVAL_MODES
        for mode in ("exact", "lexical", "fusion", "semantic", "graph"):
            self.assertEqual(RETRIEVAL_MODES[mode]["status"], "ready", mode)


# ---------------------------------------------------------------------------
# P1-1 圖擴召
# ---------------------------------------------------------------------------
class TestGraphExpansion(TCMFixtureCase):
    def test_clause_bfs_expansion(self):
        from hermes_tcm.retrieval import expand_graph
        out = expand_graph(["SHL_SONGBEN_0012"], hops=2)
        self.assertTrue(out.get("available"))
        self.assertGreater(out["n_nodes"], 1)
        self.assertGreater(out["n_edges"], 0)
        self.assertEqual(out["evidence_role"], "recall_signal")
        hops = {n["hop"] for n in out["nodes"]}
        self.assertIn(1, hops)

    def test_unrecognized_seed_skipped_honestly(self):
        from hermes_tcm.retrieval import expand_graph
        out = expand_graph(["xx"])
        self.assertTrue(out["skipped"])
        self.assertEqual(out["n_edges"], 0)

    def test_graph_tool_returns_no_evidence(self):
        from hermes_tcm.integrations.sdk import TCMClient
        with tempfile.TemporaryDirectory() as td:
            client = TCMClient(store_path=Path(td) / "r.db")
            try:
                out = client.call_tool("graph.expand_neighborhood",
                                       {"seed_ids": ["SHL_SONGBEN_0012"]})
                self.assertGreater(out["result"]["n_nodes"], 1)
                self.assertEqual(out["evidence"], [],
                                 "圖擴召是召回信號，不入台賬")
            finally:
                client.close()


class TestSemanticFallbackInFanout(TCMFixtureCase):
    def test_zero_hit_general_search_triggers_semantic_fallback(self):
        from hermes_tcm.integrations.sdk import TCMClient
        with tempfile.TemporaryDirectory() as td:
            client = TCMClient(store_path=Path(td) / "r.db")
            try:
                out = client.research("查一下奔豚灸法")
                row = client.store.load(out["run_id"])
                results = row["state"].get("retrieval_results") or []
                fallbacks = [r for r in results
                             if r.get("fallback") ==
                             "zero_hit_semantic_recall"]
                self.assertTrue(fallbacks, results)
                self.assertTrue(fallbacks[0]["ok"])
            finally:
                client.close()


# ---------------------------------------------------------------------------
# P1-3 統一 V2 MCP Server
# ---------------------------------------------------------------------------
class TestUnifiedMCPServer(TCMFixtureCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from hermes_tcm.integrations.mcp_server import TCMMCPServer
        cls._dbtmp = tempfile.TemporaryDirectory()
        cls.store_path = Path(cls._dbtmp.name) / "runs.db"
        cls.srv = TCMMCPServer(store_path=cls.store_path)

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()
        cls._dbtmp.cleanup()
        super().tearDownClass()

    def _call(self, method, params=None, id_=1):
        return self.srv.handle({"jsonrpc": "2.0", "id": id_,
                                "method": method,
                                "params": params or {}})

    def test_version_negotiation(self):
        from hermes_tcm.integrations.mcp_server import \
            SUPPORTED_PROTOCOL_VERSIONS
        r = self._call("initialize",
                       {"protocolVersion":
                        SUPPORTED_PROTOCOL_VERSIONS[1]})
        self.assertEqual(r["result"]["protocolVersion"],
                         SUPPORTED_PROTOCOL_VERSIONS[1])
        # 未知版本 → 回應最新支持版本（不是最舊）
        r = self._call("initialize", {"protocolVersion": "1999-01-01"})
        self.assertEqual(r["result"]["protocolVersion"],
                         SUPPORTED_PROTOCOL_VERSIONS[0])
        self.assertTrue(r["result"]["capabilities"]["experimental"]
                        ["tasks"]["durable"])
        self.assertTrue(r["result"]["instructions"])

    def test_tools_list_is_v2_surface(self):
        names = {t["name"] for t in
                 self._call("tools/list")["result"]["tools"]}
        for expect in ("text__search_passages", "text__search_semantic",
                       "graph__expand_neighborhood", "formula__resolve",
                       "citation__trace_quote", "tcm__research"):
            self.assertIn(expect, names)

    def test_tools_call_through_broker_with_evidence(self):
        r = self._call("tools/call", {"name": "text__search_passages",
                                      "arguments": {"query": "奔豚"}})
        payload = json.loads(r["result"]["content"][0]["text"])
        self.assertGreater(payload["result"]["n_hits"], 0)
        self.assertTrue(payload["evidence"])

    def test_durable_tasks_lifecycle_and_restart(self):
        from hermes_tcm.integrations.mcp_server import TCMMCPServer
        r = self._call("tasks/submit",
                       {"query": "「奔豚」一詞最早見於哪部書？"})
        tid = r["result"]["task_id"]
        status = ""
        for _ in range(100):
            status = self._call("tasks/status",
                                {"task_id": tid})["result"]["status"]
            if status in ("completed", "failed", "blocked", "paused"):
                break
            time.sleep(0.1)
        self.assertEqual(status, "completed")
        res = self._call("tasks/result", {"task_id": tid})
        env = json.loads(res["result"]["content"][0]["text"])["envelope"]
        self.assertEqual(env["release"]["decision"], "pass")
        # durable：新實例（模擬服務重啟）仍可續查同一任務
        srv2 = TCMMCPServer(store_path=self.store_path)
        try:
            st = srv2.handle({"jsonrpc": "2.0", "id": 9,
                              "method": "tasks/status",
                              "params": {"task_id": tid}})
            self.assertEqual(st["result"]["status"], "completed")
        finally:
            srv2.close()
        listed = self._call("tasks/list")["result"]["tasks"]
        self.assertIn(tid, [t["run_id"] for t in listed])

    def test_cancel_terminal_task_is_noop(self):
        r = self._call("tasks/cancel", {"task_id": "run_nonexistent"})
        self.assertFalse(r["result"]["cancelled"])

    def test_resources_read(self):
        r = self._call("resources/read",
                       {"uri": "tcm://policies/current"})
        self.assertIn("policies", r["result"]["contents"][0]["text"])
        templates = self._call("resources/templates/list")["result"]
        self.assertTrue(templates["resourceTemplates"])

    def test_unknown_method_and_notification(self):
        r = self._call("nope/x")
        self.assertEqual(r["error"]["code"], -32601)
        # notification（無 id）不產生響應
        out = self.srv.handle({"jsonrpc": "2.0",
                               "method": "notifications/initialized"})
        self.assertIsNone(out)

    def test_invalid_role_fails_closed(self):
        from hermes_tcm.integrations.mcp_server import TCMMCPServer
        with tempfile.TemporaryDirectory() as td:
            srv = TCMMCPServer(store_path=Path(td) / "r.db",
                               role="superuser", purpose="bogus")
            try:
                self.assertEqual(srv.client.principal.role, "public")
                self.assertEqual(srv.client.principal.purpose_of_use,
                                 "patient_education")
            finally:
                srv.close()


if __name__ == "__main__":
    unittest.main()
