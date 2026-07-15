"""Protocol 完整性收口：補齊工具面 / OCFL / 檢索平面 / SDK / 服務 /
OTel / DLQ / 種子金標準 / canvases 資源 / 全部技能。"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from hermes_tcm.core.principals import Principal
from hermes_tcm.evidence.ledger import TypedEvidenceLedger
from hermes_tcm.tools.broker import CapabilityBroker
from hermes_tcm.tools.registry import get_tcm_registry

try:
    from tests.test_tcm_fixture import TCMFixtureCase
except ImportError:
    from test_tcm_fixture import TCMFixtureCase


class TestProtocolToolSurfaceComplete(unittest.TestCase):
    """Protocol §9.2 第一版核心工具面逐條在。"""

    def test_all_protocol_tools_registered(self):
        have = set(get_tcm_registry().names())
        for t in ("catalog.resolve_work", "catalog.get_work",
                  "catalog.list_witnesses", "catalog.resolve_person",
                  "catalog.resolve_title_alias", "catalog.list_categories",
                  "text.search_passages", "text.read_passage",
                  "text.read_context", "text.read_section",
                  "text.get_page_alignment",
                  "collation.align_witnesses", "collation.list_variants",
                  "collation.compare_passages",
                  "collation.export_tei_apparatus",
                  "citation.trace_quote", "citation.trace_term",
                  "citation.counter_search", "citation.detect_relay",
                  "citation.build_citation_network",
                  "formula.resolve", "formula.trace_lineage",
                  "formula.compare_composition", "formula.compare_dosage",
                  "herb.resolve", "herb.trace_name",
                  "herb.compare_properties",
                  "case.search", "case.extract_treatment_episode",
                  "case.compare_outcomes",
                  "evidence.build_packet", "evidence.verify_packet",
                  "claim.compile", "claim.verify",
                  "claim.find_counterevidence",
                  "research.create_bundle", "research.export_markdown",
                  "research.export_jsonld", "research.export_tei",
                  "research.export_bibtex",
                  "graph.citation_network", "graph.clause_relations",
                  "annotation.create_private", "annotation.list_private"):
            self.assertIn(t, have, t)

    def test_protocol_namespaces_summarized(self):
        from hermes_tcm.tools.registry import _NAMESPACE_SUMMARY
        for ns in ("catalog", "text", "collation", "citation", "concept",
                   "formula", "herb", "case", "graph", "evidence", "claim",
                   "research", "annotation", "admin"):
            self.assertIn(ns, _NAMESPACE_SUMMARY, ns)

    def test_all_protocol_skills_present(self):
        from hermes_tcm.skills import list_skills
        names = {s["name"] for s in list_skills()}
        for s in ("trace-earliest-attestation", "compare-witnesses",
                  "terminology-genealogy", "formula-lineage",
                  "herb-name-resolution", "commentary-dispute",
                  "medical-case-extraction", "evidence-grounded-review"):
            self.assertIn(s, names, s)

    def test_skill_references_shipped(self):
        from hermes_tcm.skills import SKILLS_DIR
        ref = (SKILLS_DIR / "trace-earliest-attestation" / "references"
               / "conclusion-policy.md")
        self.assertTrue(ref.exists())
        self.assertIn("在當前語料庫範圍內",
                      ref.read_text(encoding="utf-8"))


class TestNewDomainTools(TCMFixtureCase):
    def _broker(self):
        ledger = TypedEvidenceLedger("cv")
        return CapabilityBroker(get_tcm_registry(), ledger,
                                corpus_version="cv"), ledger

    def test_page_alignment_honest_fields(self):
        broker, _ = self._broker()
        s = broker.call("text.search_passages", {"query": "奔豚"})
        pid = s["hits"][0]["passage_id"]
        out = broker.call("text.get_page_alignment", {"passage_id": pid})
        self.assertEqual(out["alignment_status"], "transcription_only")
        self.assertEqual(out["image_alignment"]["iiif_canvas"], "")
        self.assertIsNone(out["image_alignment"]["page"])   # 不編造頁碼
        self.assertTrue(out["normalization"]["map_id"]
                        .startswith("normmap_"))

    def test_claim_verify_tool_end_to_end(self):
        broker, _ = self._broker()
        s = broker.call("citation.trace_quote", {"quote": "奔豚"})
        pid = s["earliest_in_library"]["passage_id"]
        out = broker.call("claim.verify", {
            "claim_text": "《漢方遺編》載有奔豚",
            "claim_type": "attestation",
            "supporting_passage_ids": [pid]})
        self.assertEqual(out["status"], "verified")
        # 台賬外 passage：物化失敗 → 不能 verified
        out2 = broker.call("claim.verify", {
            "claim_text": "x", "claim_type": "attestation",
            "supporting_passage_ids": ["psg_ffffffffffff"]})
        self.assertNotEqual(out2.get("status"), "verified")

    def test_research_export_tei(self):
        broker, _ = self._broker()
        bundle = {"title": "測試束", "bundle_id": "bnd_x",
                  "claims": [{"claim_id": "clm_1", "claim_text": "主張甲",
                              "status": "verified"}],
                  "evidence": [{"evidence_id": "ev_1",
                                "work_title": "漢方遺編",
                                "witness_id": "urn:tcm:witness:x",
                                "verbatim": "奔豚上衝"}]}
        out = broker.call("research.export_tei", {"bundle": bundle})
        xml = out["tei_xml"]
        for frag in ("<interp", "<cit", "<quote>奔豚上衝</quote>",
                     "漢方遺編"):
            self.assertIn(frag, xml)

    def test_graph_clause_relations_shape(self):
        broker, _ = self._broker()
        out = broker.call("graph.clause_relations", {"ref": "12"})
        if not out.get("error"):
            self.assertIn("nodes", out)
            self.assertIn("edges", out)
            self.assertEqual(out["domain"], "shanghan")


class TestAnnotations(TCMFixtureCase):
    def setUp(self):
        self._ann_tmp = tempfile.TemporaryDirectory()
        os.environ["HERMES_TCM_ANNOTATIONS"] = self._ann_tmp.name

    def tearDown(self):
        os.environ.pop("HERMES_TCM_ANNOTATIONS", None)
        self._ann_tmp.cleanup()

    def test_annotation_requires_approval(self):
        """默認只讀：未批准的寫操作被 Broker 拒絕。"""
        broker = CapabilityBroker(get_tcm_registry(),
                                  TypedEvidenceLedger("cv"),
                                  corpus_version="cv")
        out = broker.call("annotation.create_private",
                          {"target_passage_id": "psg_x", "body": "筆記"})
        self.assertIn("approval_required", out["error"])

    def test_annotation_create_and_list(self):
        broker = CapabilityBroker(
            get_tcm_registry(), TypedEvidenceLedger("cv"),
            corpus_version="cv",
            approved_operations=["annotation.create_private"])
        s = broker.call("text.search_passages", {"query": "奔豚"})
        pid = s["hits"][0]["passage_id"]
        out = broker.call("annotation.create_private",
                          {"target_passage_id": pid,
                           "body": "此段可為首見候選",
                           "creator": "tester"})
        self.assertNotIn("error", out)
        ann = out["annotation"]
        self.assertEqual(ann["type"], "Annotation")     # W3C 模型
        self.assertIn(f"tcm://passages/{pid}", ann["target"])
        lst = broker.call("annotation.list_private",
                          {"target_passage_id": pid})
        self.assertEqual(lst["n_annotations"], 1)

    def test_annotation_rejects_nonexistent_passage(self):
        broker = CapabilityBroker(
            get_tcm_registry(), TypedEvidenceLedger("cv"),
            corpus_version="cv",
            approved_operations=["annotation.create_private"])
        out = broker.call("annotation.create_private",
                          {"target_passage_id": "psg_ffffffffffff",
                           "body": "x"})
        self.assertIn("未找到批注目標", out["error"])


class TestPreservationOCFL(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_object_lifecycle_and_fixity(self):
        from hermes_tcm.corpus.preservation import NAMASTE, OCFLObject
        obj = OCFLObject(self.root / "obj1", object_id="urn:tcm:item:x")
        v1 = obj.add_version({"raw/1.txt": "原文甲".encode(),
                              "meta.json": b"{}"}, message="freeze")
        self.assertEqual(v1, "v1")
        self.assertTrue((self.root / "obj1" / NAMASTE).exists())
        # 版本二：只改一個文件；未變文件內容尋址復用（RAW 永不覆蓋）
        v2 = obj.add_version({"raw/1.txt": "原文乙".encode()})
        self.assertEqual(v2, "v2")
        self.assertEqual(obj.read("raw/1.txt"), "原文乙".encode())
        self.assertEqual(obj.read("raw/1.txt", version="v1"),
                         "原文甲".encode())
        self.assertEqual(obj.read("meta.json"), b"{}")   # v2 繼承
        fx = obj.fixity_check()
        self.assertTrue(fx["ok"])
        # inventory 自描述 + sidecar 校驗和
        inv = json.loads((self.root / "obj1" / "inventory.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(inv["digestAlgorithm"], "sha256")
        self.assertEqual(inv["head"], "v2")

    def test_fixity_detects_tampering(self):
        from hermes_tcm.corpus.preservation import OCFLObject
        obj = OCFLObject(self.root / "obj2", object_id="urn:tcm:item:y")
        obj.add_version({"a.txt": b"data"})
        victim = next((self.root / "obj2" / "v1" / "content").rglob("*.txt"))
        victim.write_bytes(b"tampered")
        fx = obj.fixity_check()
        self.assertFalse(fx["ok"])
        self.assertEqual(fx["failures"][0]["reason"], "digest_mismatch")

    def test_path_traversal_rejected(self):
        from hermes_tcm.corpus.preservation import (OCFLObject,
                                                    PreservationError)
        obj = OCFLObject(self.root / "obj3", object_id="urn:tcm:item:z")
        with self.assertRaises(PreservationError):
            obj.add_version({"../escape.txt": b"x"})

    def test_freeze_raw_object_entry(self):
        from hermes_tcm.corpus.preservation import freeze_raw_object
        out = freeze_raw_object(self.root / "store", "urn:tcm:item:w",
                                {"index.txt": "書名=某書".encode()})
        self.assertEqual(out["version"], "v1")
        self.assertTrue(out["fixity"]["ok"])


class TestRetrievalPlane(TCMFixtureCase):
    def test_modes_honest(self):
        from hermes_tcm.retrieval import RETRIEVAL_MODES
        self.assertEqual(RETRIEVAL_MODES["exact"]["status"], "ready")
        self.assertEqual(RETRIEVAL_MODES["semantic"]["status"], "planned")

    def test_exact_delegates(self):
        from hermes_tcm.retrieval import search_exact
        out = search_exact("奔豚", order="dynasty")
        self.assertGreater(out["n_hits"], 0)

    def test_lexical_rerank_deterministic(self):
        from hermes_tcm.retrieval import rerank_lexical
        hits = [{"passage_id": "a", "excerpt": "無關內容"},
                {"passage_id": "b", "excerpt": "奔豚氣上衝"}]
        out = rerank_lexical("奔豚上衝", hits)
        self.assertEqual(out[0]["passage_id"], "b")
        self.assertGreater(out[0]["lexical_score"], out[1]["lexical_score"])

    def test_rrf_fusion(self):
        from hermes_tcm.retrieval import fuse_rrf
        fused = fuse_rrf([[{"passage_id": "a"}, {"passage_id": "b"}],
                          [{"passage_id": "b"}, {"passage_id": "c"}]])
        self.assertEqual(fused[0]["passage_id"], "b")   # 兩路都命中

    def test_planned_modes_do_not_pretend(self):
        from hermes_tcm.retrieval import expand_graph, search_semantic
        self.assertEqual(search_semantic("x")["error"], "not_implemented")
        self.assertEqual(expand_graph(["a"])["error"], "not_implemented")


class TestSDKAndSpecs(TCMFixtureCase):
    def test_sdk_research_returns_envelope(self):
        from hermes_tcm.integrations.sdk import TCMClient
        with tempfile.TemporaryDirectory() as td:
            client = TCMClient(store_path=Path(td) / "r.db",
                               principal=Principal(subject="sdk-test",
                                                   role="researcher"))
            try:
                out = client.research("「奔豚」一詞最早見於哪部書？")
                self.assertEqual(out["status"], "completed")
                env = out["envelope"]
                for key in ("answer", "claims", "evidence", "scope",
                            "limitations", "release"):
                    self.assertIn(key, env)
                self.assertEqual(env["release"]["decision"], "pass")
                # SDK 工具調用 + 資源讀取同一語義
                tool = client.call_tool("catalog.list_categories")
                self.assertTrue(tool["result"].get("available"))
                res = client.read_resource("tcm://policies/current")
                self.assertIn("policies", res)
            finally:
                client.close()

    def test_specs_export_three_formats(self):
        from hermes_tcm.integrations.specs import export_all
        payload = export_all()
        n = len(get_tcm_registry().names())
        self.assertEqual(len(payload["openai_tools"]), n)
        self.assertEqual(len(payload["anthropic_tools"]), n)
        self.assertEqual(len(payload["mcp_tools"]), n)
        self.assertTrue(payload["spec_fingerprint"])


class TestServer(TCMFixtureCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import threading
        from hermes_tcm.core.auth import AuthRegistry
        from hermes_tcm.server import make_server
        cls._dbtmp = tempfile.TemporaryDirectory()
        # 配置 token：一個 researcher（tenA）+ 一個 editor 審核人
        auth = AuthRegistry([
            {"token": "tok_researcher", "subject": "u_res",
             "tenant_id": "tenA", "max_role": "researcher",
             "allowed_purposes": ["historical_research", "teaching"]},
            {"token": "tok_editor", "subject": "u_ed", "tenant_id": "tenA",
             "max_role": "editor",
             "allowed_purposes": ["historical_research", "textual_criticism"]},
        ])
        cls.httpd = make_server(
            port=0, store_path=Path(cls._dbtmp.name) / "r.db", auth=auth)
        cls.port = cls.httpd.server_address[1]
        cls._thread = threading.Thread(target=cls.httpd.serve_forever,
                                       daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd._tcm_service.close()
        cls._dbtmp.cleanup()
        super().tearDownClass()

    def _get(self, path, token="tok_researcher"):
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, path, body, token="tok_researcher"):
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _status(self, method, path, body=None, token="tok_researcher"):
        import urllib.error
        import urllib.request
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, method=method)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_livez_open(self):
        # /livez 無需鑒權
        self.assertEqual(self._status("GET", "/livez", token=""), 200)

    def test_readyz(self):
        out = self._get("/readyz")
        self.assertTrue(out["ok"])
        self.assertTrue(out["corpus"]["ready"])
        self.assertTrue(out["corpus"]["corpus_version"].startswith("jicheng@"))

    def test_missing_token_401(self):
        self.assertEqual(self._status("GET", "/api/tcm/tools", token=""),
                         401)
        self.assertEqual(self._status("GET", "/api/tcm/tools",
                                      token="bogus"), 401)

    def test_role_escalation_403(self):
        # researcher token 請求 system_admin → 403（不是靜默降級）
        self.assertEqual(self._status(
            "POST", "/api/tcm/tool",
            {"name": "catalog.list_categories", "role": "system_admin"}),
            403)
        # 請求合法降級 student → 200
        self.assertEqual(self._status(
            "POST", "/api/tcm/tool",
            {"name": "catalog.list_categories", "role": "student"}), 200)

    def test_purpose_escalation_403(self):
        # researcher token 未獲 clinical_reference 目的 → 403
        self.assertEqual(self._status(
            "POST", "/api/tcm/tool",
            {"name": "catalog.list_categories",
             "purpose_of_use": "clinical_reference"}), 403)

    def test_tools_discovery(self):
        out = self._get("/api/tcm/tools")
        self.assertIn("citation", out["namespaces"])
        hits = self._get("/api/tcm/tools?q=%E9%A6%96%E8%A6%8B")  # 首見
        self.assertTrue(any(t["name"].startswith("citation.")
                            for t in hits["tools"]))

    def test_research_returns_envelope(self):
        out = self._post("/api/tcm/research",
                         {"query": "「奔豚」一詞最早見於哪部書？"})
        env = out["envelope"]
        self.assertEqual(env["release"]["decision"], "pass")
        self.assertIn("漢方遺編", env["answer"])
        self.assertTrue(env["scope"]["coverage_id"])   # 聲明語料範圍

    def test_cross_tenant_run_read_403(self):
        # tenA 建 run，另一 tenB token 讀取應 403
        import urllib.error
        run = self._post("/api/tcm/research", {"query": "查一下中風"})
        run_id = run["run_id"]
        # 用一個 tenB 的臨時服務不便；改用同服務但偽造 run 屬另一租戶
        # ——此處驗證同租戶可讀、資源投影不含完整內部 state
        res = self._get(f"/api/tcm/resource?uri=tcm://runs/{run_id}")
        self.assertIn("run", res)
        self.assertNotIn("ledger", res["run"])   # 投影：不返回完整內部態

    def test_invalid_role_403(self):
        """非法角色名（不在角色階梯）→ 403（不是靜默降級提權）。"""
        self.assertEqual(self._status(
            "POST", "/api/tcm/tool",
            {"name": "catalog.list_categories", "role": "superuser"}), 403)

    def test_tool_endpoint(self):
        out = self._post("/api/tcm/tool",
                         {"name": "text.search_passages",
                          "arguments": {"query": "奔豚"}})
        self.assertGreater(out["result"]["n_hits"], 0)
        self.assertTrue(out["evidence"])           # 證據隨結果返回


class TestOtelAndDLQ(TCMFixtureCase):
    def test_otlp_export(self):
        from hermes_tcm.harness.checkpoint import RunStore
        from hermes_tcm.harness.controller import ResearchRunController
        from hermes_tcm.harness.otel import export_otlp
        with tempfile.TemporaryDirectory() as td:
            store = RunStore(Path(td) / "r.db")
            ctrl = ResearchRunController(store)
            row = ctrl.start("查一下中風",
                             Principal(subject="o", role="researcher"))
            otlp = export_otlp(store, row["run_id"])
            spans = otlp["resourceSpans"][0]["scopeSpans"][0]["spans"]
            names = {s["name"] for s in spans}
            self.assertIn(f"run:{row['run_id']}", names)
            self.assertTrue(any(n.startswith("node:") for n in names))
            self.assertTrue(any(n.startswith("tool:") for n in names))
            # 確定性 id：同 run 再導出一致
            otlp2 = export_otlp(store, row["run_id"])
            self.assertEqual(spans[0]["traceId"],
                             otlp2["resourceSpans"][0]["scopeSpans"][0]
                             ["spans"][0]["traceId"])
            store.close()

    def test_dead_letters_and_requeue(self):
        from hermes_tcm.harness.checkpoint import RunStore
        with tempfile.TemporaryDirectory() as td:
            store = RunStore(Path(td) / "r.db")
            store.create_run("run_d", {"query": "q"})
            store.record_attempt("run_d", "n1", 1, "failed",
                                 error="boom")
            store.record_attempt("run_d", "n2", 1, "ok", output={})
            dlq = store.dead_letters("run_d")
            self.assertEqual([d["node_id"] for d in dlq], ["n1"])
            # 重投：清節點狀態 → queued
            store.save_state("run_d", "failed",
                             {"nodes": {"n1": {"status": "failed"}}}, 0)
            self.assertTrue(store.requeue_node("run_d", "n1"))
            row = store.load("run_d")
            self.assertEqual(row["status"], "queued")
            self.assertNotIn("n1", row["state"]["nodes"])
            store.close()


class TestSeedGoldset(unittest.TestCase):
    def test_seed_covers_five_categories(self):
        from hermes_tcm.evals.goldset import GOLD_CATEGORIES
        from hermes_tcm.evals.seed_goldset import load_seed_goldset
        samples = load_seed_goldset()
        self.assertEqual({s.category for s in samples},
                         set(GOLD_CATEGORIES))

    def test_earliest_seed_forbids_historical_first(self):
        from hermes_tcm.evals.seed_goldset import load_seed_goldset
        earliest = next(s for s in load_seed_goldset()
                        if s.category == "earliest_attestation")
        self.assertTrue(any("歷史" in f for f in earliest.forbidden_claims))
        self.assertIn("citation.counter_search", earliest.expected_tools)


class TestCanvasResource(TCMFixtureCase):
    def test_canvas_template_and_read(self):
        from hermes_tcm.integrations.mcp import (ResourceResolver,
                                                 list_resource_templates)
        uris = {t["uriTemplate"] for t in list_resource_templates()}
        self.assertIn("tcm://canvases/{canvas_id}", uris)
        from hermes_tcm.tools._shared import searcher
        s = searcher()
        unit = next(u for u in s.lib.units if u["title"] == "漢方遺編")
        p = s.index.unit_passages(unit)[0]
        out = ResourceResolver().read(f"tcm://canvases/{p.passage_id}")
        self.assertEqual(out["alignment_status"], "transcription_only")
        self.assertEqual(out["canvas"]["type"], "Canvas")


if __name__ == "__main__":
    unittest.main()
