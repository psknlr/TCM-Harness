"""外部審計 P0/P1 修復回歸。

P0-1 統一證據適配層：shanghan 工具的 legacy 證據形狀（evidence_excerpts
     / supporting_clauses / canonical_support）必須進入 V2 證據台賬；
     returns_primary_text 契約不兌現不再靜默通過。
P0-2 DomainRouter：Task Type × Domain 正交路由——「桂枝湯的核心方證」
     路由到 formula.resolve（領域工具優先，全庫旁證補充）。
P0-5 超時治理：滯留線程熔斷 + 非只讀工具超時副作用守衛。
P0-6 /readyz 假就緒修復（503 + ok:false）+ /livez 分離。
P1-2 execution_mode=council 進入主路徑（同一 RunStore / Release Gate）。
P1-4 雙領域註冊表防漂移鉗制。
P1-5 深度參數校驗（嵌套/數組項/長度/pattern/boolean≠integer）。
"""
import tempfile
import threading
import time
import unittest
from pathlib import Path

try:
    from tests.test_tcm_fixture import TCMFixtureCase
except ImportError:
    from test_tcm_fixture import TCMFixtureCase

from hermes_tcm.core.principals import Principal
from hermes_tcm.evidence.ledger import TypedEvidenceLedger
from hermes_tcm.tools import broker as broker_mod
from hermes_tcm.tools.broker import CapabilityBroker
from hermes_tcm.tools.contracts import EvidenceContract, ToolContractV2
from hermes_tcm.tools.registry import ToolNamespaceRegistry


def _mini_broker(contracts, approved=None):
    reg = ToolNamespaceRegistry()
    for c in contracts:
        reg.add(c)
    ledger = TypedEvidenceLedger("")
    return CapabilityBroker(reg, ledger,
                            principal=Principal(subject="t",
                                                role="researcher"),
                            approved_operations=approved or []), ledger


# ---------------------------------------------------------------------------
# P0-1 統一證據適配層
# ---------------------------------------------------------------------------
class TestEvidenceAdapter(unittest.TestCase):
    def _call(self, name, args):
        from hermes_tcm.tools.registry import get_tcm_registry
        ledger = TypedEvidenceLedger("")
        broker = CapabilityBroker(get_tcm_registry(), ledger)
        out = broker.call(name, args)
        return out, ledger, broker

    def test_formula_resolve_registers_v2_evidence(self):
        """契約聲明 returns_primary_text 的傷寒工具不再證據計零。"""
        out, ledger, broker = self._call("formula.resolve",
                                         {"formula": "桂枝湯"})
        self.assertNotIn("error", out)
        recs = ledger.all_records()
        self.assertTrue(recs, "formula.resolve 的條文證據必須入 V2 台賬")
        for r in recs:
            self.assertRegex(r.passage_id, r"SHL_SONGBEN_(?:AUX_)?\d{4}")
            self.assertTrue(r.verbatim)
            self.assertEqual(r.verification_level, "V2")
            self.assertTrue(r.is_primary_text_returned)
            self.assertEqual(r.registered_by, "capability_broker")
            self.assertTrue(r.tool_call_id and r.span_id)
        self.assertFalse([g for g in broker.guardrail_events
                          if g["event"] == "evidence_contract_unfulfilled"])

    def test_case_search_canonical_support_registered(self):
        """canonical_support（條文 id 列表）也是可入賬證據形狀。"""
        out, ledger, _ = self._call("case.search", {"formula": "桂枝湯"})
        self.assertNotIn("error", out)
        self.assertTrue(ledger.all_records())

    def test_dose_tool_fulfills_contract(self):
        out, ledger, broker = self._call("formula.compare_dosage",
                                         {"formula": "桂枝湯"})
        self.assertNotIn("error", out)
        self.assertTrue(ledger.all_records())

    def test_unfulfilled_primary_text_contract_not_silent(self):
        """成功調用 + 契約聲明返回原文 + 台賬零證據 → guardrail 事件。"""
        contract = ToolContractV2(
            name="text.fake_probe", description="t",
            input_schema={"type": "object", "properties": {}},
            func=lambda: {"ok": 1},
            evidence_contract=EvidenceContract(
                returns_primary_text=True,
                evidence_role="primary_text_returned"))
        broker, ledger = _mini_broker([contract])
        out = broker.call("text.fake_probe", {})
        self.assertNotIn("error", out)
        self.assertEqual(len(ledger), 0)
        events = [g["event"] for g in broker.guardrail_events]
        self.assertIn("evidence_contract_unfulfilled", events)

    def test_normalizer_ignores_unresolvable_ids(self):
        """正文不可得的 id 提及不入賬（id_mention_only 不是證據）。"""
        from hermes_tcm.domains.shanghan import normalize_evidence
        recs = normalize_evidence(
            "formula.resolve",
            {"domain": "shanghan", "supporting_clauses": ["SHL_SONGBEN_9999"]})
        self.assertEqual(recs, [])

    def test_normalizer_scopes_to_domain_results(self):
        """非領域結果不觸發適配（含 SHL 樣式文本的普通結果不誤入賬）。"""
        from hermes_tcm.domains.shanghan import normalize_evidence
        recs = normalize_evidence(
            "text.search_passages", {"hits": ["SHL_SONGBEN_0012"]})
        self.assertEqual(recs, [])


# ---------------------------------------------------------------------------
# P0-2 DomainRouter：Task Type × Domain 正交路由
# ---------------------------------------------------------------------------
class TestDomainRouting(unittest.TestCase):
    def test_formula_pattern_classification(self):
        from hermes_tcm.harness.controller import classify_task
        self.assertEqual(classify_task("桂枝湯的核心方證是什麼？"),
                         "formula_pattern")
        self.assertEqual(classify_task("桂枝汤的核心方证是什么？"),
                         "formula_pattern")

    def test_existing_rules_take_precedence(self):
        """既有分類規則優先——不被領域細分遮蔽。"""
        from hermes_tcm.harness.controller import classify_task
        self.assertEqual(classify_task("桂枝湯的方劑源流"),
                         "formula_lineage")
        self.assertEqual(classify_task("桂枝湯最早見於哪部書"),
                         "earliest_attestation")
        self.assertEqual(classify_task("查一下奔豚"), "general_search")

    def test_case_and_herb_cues(self):
        from hermes_tcm.harness.controller import classify_task
        self.assertEqual(classify_task("桂枝湯醫案"), "case_study")
        self.assertEqual(classify_task("桂枝的配伍"), "herb_profile")

    def test_route_returns_orthogonal_dimensions(self):
        from hermes_tcm.harness.router import route
        r = route("桂枝湯的核心方證是什麼？")
        self.assertEqual(r["task_type"], "formula_pattern")
        self.assertEqual(r["domains"], ["shanghan"])
        self.assertEqual(r["retrieval_strategy"],
                         "domain_first_then_library")
        self.assertEqual(r["entities"][0]["name"], "桂枝湯")

    def test_entity_longest_match_suppresses_substring(self):
        from hermes_tcm.domains.shanghan import link_entities
        ents = link_entities("桂枝加葛根湯的方證")
        names = [e["name"] for e in ents]
        self.assertIn("桂枝加葛根湯", names)
        self.assertNotIn("桂枝湯", names)


class TestFormulaPatternFullRun(TCMFixtureCase):
    def test_research_reaches_domain_evidence(self):
        """審計實測案例：此前 paused + 零證據，現在必須取到領域證據。"""
        from hermes_tcm.integrations.sdk import TCMClient
        with tempfile.TemporaryDirectory() as td:
            client = TCMClient(store_path=Path(td) / "runs.db")
            try:
                out = client.research("桂枝湯的核心方證是什麼？")
                env = out["envelope"]
                self.assertEqual(out["status"], "completed")
                self.assertEqual(env["release"]["decision"], "pass")
                self.assertTrue(env["evidence"],
                                "領域證據必須出現在信封允許集")
                self.assertIn("桂枝湯", env["answer"])
            finally:
                client.close()


# ---------------------------------------------------------------------------
# P0-5 超時治理
# ---------------------------------------------------------------------------
class TestTimeoutGovernance(unittest.TestCase):
    def tearDown(self):
        with broker_mod._ZOMBIE_LOCK:
            broker_mod._ZOMBIE_THREADS.clear()

    def _slow_contract(self, name="text.slow_probe", side_effect="read_only",
                       approval="none"):
        return ToolContractV2(
            name=name, description="t",
            input_schema={"type": "object", "properties": {}},
            func=lambda: (time.sleep(0.4), {"ok": 1})[1],
            side_effect=side_effect, approval=approval,
            timeout_ms=50)

    def test_timeout_registers_zombie_and_guardrail(self):
        broker, _ = _mini_broker([self._slow_contract()])
        out = broker.call("text.slow_probe", {})
        self.assertIn("timeout", out.get("error", ""))
        events = [g["event"] for g in broker.guardrail_events]
        self.assertIn("tool_timeout_thread_leaked", events)
        with broker_mod._ZOMBIE_LOCK:
            self.assertTrue(broker_mod._ZOMBIE_THREADS)

    def test_write_tool_timeout_flags_side_effect_risk(self):
        c = self._slow_contract(name="annotation.slow_write",
                                side_effect="annotate", approval="prompt")
        broker, _ = _mini_broker([c], approved=["annotation.slow_write"])
        out = broker.call("annotation.slow_write", {})
        self.assertIn("timeout", out.get("error", ""))
        events = [g["event"] for g in broker.guardrail_events]
        self.assertIn("timeout_side_effect_risk", events)

    def test_circuit_opens_at_zombie_cap(self):
        stop = threading.Event()
        fillers = []
        for _ in range(broker_mod.MAX_ZOMBIE_THREADS):
            t = threading.Thread(target=stop.wait, daemon=True)
            t.start()
            fillers.append(t)
        with broker_mod._ZOMBIE_LOCK:
            broker_mod._ZOMBIE_THREADS.extend(fillers)
        try:
            broker, _ = _mini_broker([self._slow_contract()])
            out = broker.call("text.slow_probe", {})
            self.assertIn("circuit_open", out.get("error", ""))
            events = [g["event"] for g in broker.guardrail_events]
            self.assertIn("timeout_circuit_open", events)
        finally:
            stop.set()


# ---------------------------------------------------------------------------
# P1-5 深度參數校驗
# ---------------------------------------------------------------------------
class TestDeepValidation(unittest.TestCase):
    def setUp(self):
        contract = ToolContractV2(
            name="text.schema_probe", description="t",
            input_schema={
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "minimum": 1},
                    "name": {"type": "string", "pattern": "^[a-z]+$",
                             "maxLength": 4},
                    "tags": {"type": "array",
                             "items": {"type": "string"}, "maxItems": 2},
                    "filters": {"type": "object",
                                "properties": {
                                    "dynasty": {"type": "string",
                                                "minLength": 1}},
                                "required": ["dynasty"],
                                "additionalProperties": False}},
                "required": ["n"]},
            func=lambda **kw: {"ok": 1})
        self.broker, _ = _mini_broker([contract])

    def _err(self, args):
        return self.broker.call("text.schema_probe", args).get("error", "")

    def test_boolean_not_accepted_as_integer(self):
        self.assertIn("boolean 不是數值", self._err({"n": True}))

    def test_array_item_type_enforced(self):
        self.assertIn("tags[1]", self._err({"n": 1, "tags": ["a", 3]}))

    def test_max_items_enforced(self):
        self.assertIn("項數", self._err({"n": 1, "tags": ["a", "b", "c"]}))

    def test_nested_required_enforced(self):
        self.assertIn("缺少必填字段 dynasty",
                      self._err({"n": 1, "filters": {}}))

    def test_nested_additional_properties_rejected(self):
        self.assertIn("未知字段",
                      self._err({"n": 1, "filters": {"dynasty": "漢",
                                                     "extra": 1}}))

    def test_string_pattern_and_length(self):
        self.assertIn("pattern", self._err({"n": 1, "name": "ABC"}))
        self.assertIn("長度", self._err({"n": 1, "name": "abcde"}))

    def test_valid_arguments_pass(self):
        out = self.broker.call("text.schema_probe",
                               {"n": 2, "name": "abc", "tags": ["a"],
                                "filters": {"dynasty": "漢"}})
        self.assertNotIn("error", out)


# ---------------------------------------------------------------------------
# P0-6 /readyz 假就緒修復 + /livez
# ---------------------------------------------------------------------------
class TestReadiness(TCMFixtureCase):
    def test_ready_and_not_ready_branches(self):
        from hermes_shanghan import config
        from hermes_tcm.integrations.sdk import TCMClient
        from hermes_tcm.server import readiness_report
        with tempfile.TemporaryDirectory() as td:
            client = TCMClient(store_path=Path(td) / "runs.db")
            try:
                payload, code = readiness_report(client)
                self.assertEqual(code, 200)
                self.assertTrue(payload["ok"])
                shanghan = next(p for p in payload["domain_packs"]
                                if p["domain_id"] == "shanghan")
                self.assertEqual(shanghan["status"], "ready")
                self.assertTrue(shanghan["healthy"])
                # 語料缺失 → 503 + ok:false + 缺失組件（不再假就緒）
                saved = config.LIBRARY_DIR
                config.LIBRARY_DIR = Path(td) / "no_library"
                try:
                    payload2, code2 = readiness_report(client)
                finally:
                    config.LIBRARY_DIR = saved
                self.assertEqual(code2, 503)
                self.assertFalse(payload2["ok"])
                self.assertIn("corpus", payload2["missing"])
            finally:
                client.close()

    def test_livez_endpoint_separate(self):
        import json
        import urllib.request
        from hermes_tcm.server import make_server
        with tempfile.TemporaryDirectory() as td:
            httpd = make_server(port=0, store_path=Path(td) / "r.db")
            port = httpd.server_address[1]
            th = threading.Thread(target=httpd.serve_forever, daemon=True)
            th.start()
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/livez") as resp:
                    self.assertEqual(
                        json.loads(resp.read().decode())["ok"], True)
            finally:
                httpd.shutdown()
                httpd._tcm_service.close()


# ---------------------------------------------------------------------------
# P1-2 execution_mode=council 進入主路徑
# ---------------------------------------------------------------------------
class TestCouncilMode(TCMFixtureCase):
    def test_invalid_execution_mode_rejected(self):
        from hermes_tcm.harness.run_spec import RunSpecV2
        with self.assertRaises(ValueError):
            RunSpecV2(run_id="r", query="q",
                      principal=Principal(subject="t", role="researcher"),
                      execution_mode="swarm")

    def test_council_run_recorded_with_release_gate(self):
        from hermes_tcm.integrations.sdk import TCMClient
        with tempfile.TemporaryDirectory() as td:
            client = TCMClient(store_path=Path(td) / "runs.db")
            try:
                out = client.research("「奔豚」一詞最早見於哪部書？",
                                      execution_mode="council")
                self.assertIn(out["status"],
                              ("completed", "paused", "blocked", "failed"))
                self.assertIn("decision", out["envelope"]["release"])
                row = client.store.load(out["run_id"])
                self.assertEqual(row["spec"]["execution_mode"], "council")
                self.assertTrue(row["state"]["claims"])
                self.assertTrue(out["envelope"]["evidence"])
                # 審批續跑未接入合議重跑：resume 不得以 single DAG 覆蓋
                before = row["status"]
                row2 = client.controller.resume(
                    out["run_id"], approve="semantic_support_review")
                self.assertEqual(row2["status"], before)
            finally:
                client.close()


# ---------------------------------------------------------------------------
# P1-4 雙領域註冊表防漂移
# ---------------------------------------------------------------------------
class TestDomainRegistryConsistency(unittest.TestCase):
    def test_no_drift_between_registries(self):
        from hermes_tcm.domains.registry import legacy_consistency_problems
        self.assertEqual(legacy_consistency_problems(), [])

    def test_unified_view_covers_both(self):
        from hermes_tcm.domains.registry import unified_domain_view
        rows = {r["domain_id"]: r for r in unified_domain_view()}
        self.assertEqual(rows["shanghan"]["v2_status"], "ready")
        self.assertEqual(rows["shanghan"]["legacy_status"], "active")
        self.assertTrue(rows["shanghan"]["has_evidence_normalizer"])
        self.assertEqual(rows["jingui"]["v2_status"], "planned")
        self.assertTrue(rows["classics"]["platform_plugin"])

    def test_shanghan_seams_loadable(self):
        from hermes_tcm.domains.registry import get_domain_pack
        pack = get_domain_pack("shanghan")
        self.assertTrue(callable(pack.load_evidence_normalizer()))
        self.assertTrue(callable(pack.load_entity_linker()))


if __name__ == "__main__":
    unittest.main()
