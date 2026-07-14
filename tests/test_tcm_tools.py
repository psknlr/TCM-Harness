"""工具面（P0-7/10）：命名空間註冊表 / Broker 中介 / legacy 適配 / 覆蓋登記。"""
import unittest
from pathlib import Path

from hermes_tcm.core.principals import Principal
from hermes_tcm.evidence.ledger import TypedEvidenceLedger
from hermes_tcm.harness.budget import RunBudgetV2
from hermes_tcm.harness.run_spec import BudgetSpec
from hermes_tcm.tools.adapters import LEGACY_TOOL_MAP, resolve_legacy_tool
from hermes_tcm.tools.broker import CapabilityBroker
from hermes_tcm.tools.contracts import ToolContractV2
from hermes_tcm.tools.registry import (ToolNamespaceRegistry,
                                       get_tcm_registry)

try:
    from tests.test_tcm_fixture import TCMFixtureCase
except ImportError:
    from test_tcm_fixture import TCMFixtureCase


class TestContracts(unittest.TestCase):
    def test_namespace_required(self):
        with self.assertRaises(ValueError):
            ToolContractV2(name="no_namespace", description="x",
                           input_schema={"type": "object"}, func=lambda: {})

    def test_write_tool_requires_approval(self):
        """默認只讀；非只讀工具必須聲明審批等級。"""
        with self.assertRaises(ValueError):
            ToolContractV2(name="admin.delete", description="x",
                           input_schema={"type": "object"},
                           func=lambda: {}, side_effect="admin",
                           approval="none")

    def test_spec_exports(self):
        reg = get_tcm_registry()
        c = reg.get("citation.trace_quote")
        spec = c.spec()
        # 契約必備欄目（Protocol §9.3）
        for key in ("use_when", "do_not_use_when", "side_effect",
                    "evidence_contract", "failure_modes", "schema_hash"):
            self.assertIn(key, spec)
        self.assertEqual(spec["side_effect"], "read_only")
        self.assertTrue(spec["evidence_contract"]
                        ["requires_coverage_record"])
        # 三種導出格式的名稱轉換
        self.assertEqual(c.openai_spec()["function"]["name"],
                         "citation__trace_quote")
        self.assertTrue(c.mcp_spec()["annotations"]["readOnlyHint"])


class TestRegistryDiscovery(unittest.TestCase):
    def setUp(self):
        self.reg = get_tcm_registry()

    def test_all_namespaces_present(self):
        ns = set(self.reg.namespaces())
        for expected in ("catalog", "text", "collation", "citation",
                         "concept", "formula", "herb", "case", "evidence",
                         "claim", "research"):
            self.assertIn(expected, ns)

    def test_namespaces_do_not_leak_schemas(self):
        """頂層可發現面只有名稱清單，不平鋪 schema。"""
        ns = self.reg.namespaces()
        for entry in ns.values():
            self.assertNotIn("input_schema", str(entry.keys()))

    def test_discover_by_intent(self):
        hits = self.reg.discover("首見 最早", limit=5)
        self.assertTrue(any(h["name"].startswith("citation.")
                            for h in hits))

    def test_all_tools_read_only(self):
        for name in self.reg.names():
            self.assertEqual(self.reg.get(name).side_effect, "read_only",
                             name)


class TestLegacyAdapters(unittest.TestCase):
    def test_protocol_mappings(self):
        """Protocol §17 明文映射逐條在。"""
        self.assertEqual(LEGACY_TOOL_MAP["classics_trace_citation"]["tool"],
                         "citation.trace_quote")
        self.assertEqual(
            LEGACY_TOOL_MAP["classics_search_passages"]["tool"],
            "text.search_passages")
        self.assertIsNone(resolve_legacy_tool("shanghan_match_formula"))

    def test_mapped_targets_exist(self):
        reg = get_tcm_registry()
        for legacy, m in LEGACY_TOOL_MAP.items():
            self.assertIsNotNone(reg.get(m["tool"]),
                                 f"{legacy} → {m['tool']} 不存在")


class TestBrokerPipeline(TCMFixtureCase):
    def _broker(self, role="researcher", purpose="historical_research",
                budget=None):
        ledger = TypedEvidenceLedger("tcm-fixture-cv")
        principal = Principal(subject="t", role=role,
                              purpose_of_use=purpose)
        reg = get_tcm_registry().for_role(role)
        return CapabilityBroker(reg, ledger, principal=principal,
                                budget=budget,
                                corpus_version="tcm-fixture-cv"), ledger

    def test_unknown_tool_denied(self):
        broker, _ = self._broker()
        out = broker.call("nonexistent.tool", {})
        self.assertIn("unknown tool", out["error"])

    def test_arg_validation(self):
        broker, _ = self._broker()
        out = broker.call("citation.trace_quote", {"bogus_arg": 1})
        self.assertIn("參數校驗失敗", out["error"])

    def test_search_registers_v2_evidence_and_coverage(self):
        broker, ledger = self._broker()
        out = broker.call("text.search_passages",
                          {"query": "奔豚", "order": "dynasty"})
        self.assertNotIn("error", out)
        self.assertGreater(out["n_hits"], 0)
        self.assertIn("coverage", out)
        cov = out["coverage"]
        self.assertTrue(cov["coverage_id"].startswith("cov_"))
        self.assertIn("dynasty_ordered", cov["search_modes"])
        # Broker 已把 legacy passage_evidence 轉為 V2 並入賬
        self.assertGreater(len(ledger), 0)
        for rec in ledger.all_records():
            self.assertEqual(rec.registered_by, "capability_broker")
            self.assertTrue(rec.work_id.startswith("urn:tcm:work:"))
            self.assertTrue(rec.witness_id.startswith("urn:tcm:witness:"))
            self.assertEqual(rec.verification_level, "V2")   # 身份鏈完整
            self.assertEqual(rec.coverage_id, cov["coverage_id"])
        self.assertIn(cov["coverage_id"], broker.coverages)

    def test_legacy_name_adapts_to_new_tool(self):
        broker, ledger = self._broker()
        out = broker.call("classics_search_passages", {"query": "奔豚"})
        self.assertEqual(out["tool"], "text.search_passages")
        self.assertGreater(len(ledger), 0)

    def test_trace_quote_counter_search_and_coverage(self):
        broker, _ = self._broker()
        out = broker.call("citation.trace_quote", {"quote": "奔豚"})
        self.assertNotIn("error", out)
        earliest = out["earliest_in_library"]
        self.assertEqual(earliest["dynasty"], "東漢")     # 漢方遺編最早
        self.assertIn("在庫首現≠歷史首現", out["honesty"])
        self.assertIn("coverage", out)

    def test_budget_exhaustion_stops_calls(self):
        budget = RunBudgetV2(BudgetSpec(max_tool_calls=1))
        broker, _ = self._broker(budget=budget)
        broker.call("text.search_passages", {"query": "奔豚"})
        out = broker.call("text.search_passages", {"query": "中風"})
        self.assertIn("BUDGET_EXHAUSTED", out["error"])

    def test_purpose_denial_patient_education_dosage(self):
        """患者教育目的禁止劑量換算輸出（目的限制獨立於角色）。"""
        broker, _ = self._broker(role="researcher",
                                 purpose="patient_education")
        out = broker.call("formula.compare_dosage", {"formula": "桂枝湯"})
        self.assertIn("purpose_denied", out.get("error", ""))
        self.assertTrue(any(e["event"] == "purpose_denied"
                            for e in broker.guardrail_events))

    def test_catalog_resolution_via_broker(self):
        broker, _ = self._broker()
        out = broker.call("catalog.resolve_work", {"title": "同名醫鑑"})
        self.assertTrue(out["resolution"]["needs_human_adjudication"])

    def test_collation_and_tei_export(self):
        broker, _ = self._broker()
        out = broker.call("collation.align_witnesses",
                          {"work": "丁氏經", "query": "中風"})
        self.assertGreaterEqual(out["n_witnesses"], 2)
        tei = broker.call("collation.export_tei_apparatus",
                          {"work": "丁氏經", "query": "中風"})
        self.assertIn("<app", tei["tei_xml"])
        self.assertIn("<lem", tei["tei_xml"])
        self.assertIn("<rdg", tei["tei_xml"])

    def test_cache_hit_reregisters_evidence(self):
        broker, ledger = self._broker()
        broker.call("text.search_passages", {"query": "奔豚"})
        n1 = len(ledger)
        out = broker.call("text.search_passages", {"query": "奔豚"})
        self.assertTrue(out.get("cache_hit"))
        self.assertEqual(len(ledger), n1)       # 去重：不重複入賬

    def test_read_tool_no_coverage_guardrail(self):
        """回歸：閱讀類工具不要求覆蓋記錄，成功閱讀不誤發護欄事件。"""
        broker, _ = self._broker()
        s = broker.call("text.search_passages", {"query": "奔豚"})
        pid = s["hits"][0]["passage_id"]
        broker.guardrail_events = []
        broker.call("text.read_passage", {"passage_id": pid})
        self.assertFalse(any(e["event"] == "coverage_missing"
                             for e in broker.guardrail_events))

    def test_dosage_capability_labeled(self):
        """回歸：formula.compare_dosage 標 dosage_conversion（不被前綴
        規則搶先標成 formula_recommendation）。"""
        from hermes_tcm.tools.broker import _tool_capability
        self.assertEqual(_tool_capability("formula.compare_dosage"),
                         "dosage_conversion")


class TestUnavailableHandling(unittest.TestCase):
    """庫未就緒（available:False）不得被當成成功結果緩存/入賬。"""

    def setUp(self):
        import tempfile
        from hermes_shanghan import config
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = config.LIBRARY_DIR
        config.LIBRARY_DIR = Path(self._tmp.name)   # 空庫

    def tearDown(self):
        from hermes_shanghan import config
        config.LIBRARY_DIR = self._saved
        self._tmp.cleanup()

    def test_unavailable_not_ok_not_cached(self):
        broker = CapabilityBroker(get_tcm_registry(),
                                  TypedEvidenceLedger("cv"),
                                  corpus_version="cv")
        broker.call("text.search_passages", {"query": "奔豚"})
        self.assertFalse(broker.audit_tail(1)[0]["ok"])
        out2 = broker.call("text.search_passages", {"query": "奔豚"})
        self.assertFalse(out2.get("cache_hit"))   # 未就緒不入緩存

    def test_citation_tools_propagate_unavailable(self):
        broker = CapabilityBroker(get_tcm_registry(),
                                  TypedEvidenceLedger("cv"),
                                  corpus_version="cv")
        for tool, args in (("citation.detect_relay", {"quote": "奔豚上衝"}),
                           ("citation.build_citation_network",
                            {"quote": "奔豚上衝"}),
                           ("collation.list_variants",
                            {"work": "傷寒論", "query": "中風"})):
            out = broker.call(tool, args)
            self.assertFalse(out.get("available", True),
                             f"{tool} 未傳播 available:False")


if __name__ == "__main__":
    unittest.main()
