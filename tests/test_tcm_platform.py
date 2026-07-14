"""平台面：AnswerEnvelope / MCP 資源 / Skills 漸進披露 / Domain Packs /
多專家編排 / 語料生命週期。"""
import unittest

from hermes_tcm.corpus.lifecycle import (INGEST_STAGES, IngestRun)
from hermes_tcm.corpus.normalization import build_map, three_layer_view
from hermes_tcm.domains.registry import (DOMAIN_PACKS, call_domain_tool,
                                         list_domain_packs)
from hermes_tcm.envelope import AnswerEnvelope, citation_label
from hermes_tcm.integrations.mcp import (SERVER_INSTRUCTIONS,
                                         ResourceResolver,
                                         export_mcp_manifest,
                                         list_resource_templates)
from hermes_tcm.skills import (list_skills, load_skill, skill_for_task,
                               skills_fingerprint)

try:
    from tests.test_tcm_fixture import TCMFixtureCase
except ImportError:
    from test_tcm_fixture import TCMFixtureCase


class TestAnswerEnvelope(unittest.TestCase):
    def test_no_bare_text(self):
        env = AnswerEnvelope(answer="x", answer_type="research_synthesis")
        d = env.to_dict()
        for key in ("claims", "evidence", "scope", "uncertainty",
                    "limitations", "run", "release"):
            self.assertIn(key, d)

    def test_invalid_type_rejected(self):
        with self.assertRaises(ValueError):
            AnswerEnvelope(answer="x", answer_type="banana")

    def test_citation_label(self):
        label = citation_label({"work_title": "傷寒論", "section": "卷一",
                                "dynasty": "東漢"})
        self.assertEqual(label, "《傷寒論》·卷一（東漢）")


class TestMCPIntegration(TCMFixtureCase):
    def test_server_instructions_front_loaded(self):
        """前 512 字符自包含三大約束（Codex 建議）。"""
        head = SERVER_INSTRUCTIONS[:512]
        self.assertIn("evidence-grounded", head)
        self.assertIn("Never state historical first occurrence", head)
        self.assertIn("untrusted data", head)

    def test_resource_templates(self):
        uris = {t["uriTemplate"] for t in list_resource_templates()}
        for expected in ("tcm://works/{work_id}",
                         "tcm://passages/{passage_id}",
                         "tcm://evidence/{evidence_id}",
                         "tcm://claims/{claim_id}",
                         "tcm://policies/{policy_id}",
                         "tcm://skills/{skill_name}"):
            self.assertIn(expected, uris)

    def test_resolver_reads_policy_and_skill(self):
        r = ResourceResolver()
        pol = r.read("tcm://policies/earliest_attestation")
        self.assertIn("policy", pol)
        skill = r.read("tcm://skills/trace-earliest-attestation")
        self.assertIn("body", skill)
        bad = r.read("tcm://banana/x")
        self.assertIn("error", bad)

    def test_resolver_reads_passage_from_fixture(self):
        from hermes_tcm.tools._shared import searcher
        s = searcher()
        unit = next(u for u in s.lib.units if u["title"] == "漢方遺編")
        p = s.index.unit_passages(unit)[0]
        out = ResourceResolver().read(f"tcm://passages/{p.passage_id}")
        self.assertIn("奔豚", out["text"])
        self.assertEqual(out["evidence"]["work_title"], "漢方遺編")

    def test_manifest_exports_tools_and_instructions(self):
        m = export_mcp_manifest()
        self.assertTrue(m["instructions"])
        names = {t["name"] for t in m["tools"]}
        self.assertIn("citation__trace_quote", names)
        self.assertTrue(all(t["annotations"]["readOnlyHint"]
                            for t in m["tools"]))


class TestSkills(unittest.TestCase):
    def test_progressive_disclosure(self):
        """頂層清單不含正文；load 才給全文。"""
        skills = list_skills()
        self.assertGreaterEqual(len(skills), 4)
        for s in skills:
            self.assertNotIn("body", s)
            self.assertTrue(s["description"])
        full = load_skill("trace-earliest-attestation")
        self.assertIn("Never say historical first occurrence",
                      full["body"])

    def test_skill_for_task(self):
        s = skill_for_task("earliest_attestation")
        self.assertEqual(s["name"], "trace-earliest-attestation")
        self.assertIsNone(skill_for_task("nonexistent_task"))

    def test_fingerprint_stable(self):
        self.assertEqual(skills_fingerprint(), skills_fingerprint())


class TestDomainPacks(unittest.TestCase):
    def test_shanghan_is_first_pack(self):
        packs = {p["domain_id"]: p for p in list_domain_packs()}
        self.assertEqual(packs["shanghan"]["status"], "ready")
        # 其餘領域如實 planned
        for d in ("bencao", "neijing", "warm_disease", "acupuncture"):
            self.assertEqual(packs[d]["status"], "planned")

    def test_planned_domain_honest_error(self):
        out = call_domain_tool("domain.bencao.search", {})
        self.assertIn("未就緒", out["error"])

    def test_shanghan_projection_delegates(self):
        out = call_domain_tool("domain.shanghan.search", {"query": "桂枝湯"})
        self.assertEqual(out["tool"], "domain.shanghan.search")
        self.assertEqual(out["domain"], "shanghan")
        self.assertTrue(out.get("hits"))


class TestOrchestrator(TCMFixtureCase):
    def test_specialists_isolated_and_verified(self):
        from hermes_tcm.agents.orchestrator import ResearchOrchestrator
        orch = ResearchOrchestrator(corpus_version="tcm-fixture-cv")
        out = orch.run("奔豚", task_type="earliest_attestation")
        self.assertGreater(out["n_evidence"], 0)
        roles = {r["role"] for r in out["specialists"]}
        self.assertIn("chronology_specialist", roles)
        self.assertIn("counterevidence_critic", roles)
        # 每個專家有自己的獨立 packet
        pkt_ids = [r["packet_id"] for r in out["specialists"]]
        self.assertEqual(len(pkt_ids), len(set(pkt_ids)))
        self.assertEqual(out["verification"]["authority"],
                         "harness_independent_audit")

    def test_parallel_safety_table(self):
        from hermes_tcm.agents.specialists import PARALLEL_SAFETY
        self.assertIn("evidence_ledger_write",
                      PARALLEL_SAFETY["serial_only"])
        self.assertIn("clinical_release", PARALLEL_SAFETY["serial_only"])


class TestCorpusLifecycle(unittest.TestCase):
    def test_fifteen_stages(self):
        self.assertEqual(len(INGEST_STAGES), 15)
        self.assertEqual(INGEST_STAGES[0], "source_register")
        self.assertEqual(INGEST_STAGES[-1], "readyz")

    def test_no_stage_skipping(self):
        run = IngestRun(source_id="s1")
        run.advance("source_register")
        with self.assertRaises(ValueError):
            run.advance("index_build")      # 跳階
        run.advance("license_and_rights_check")
        self.assertEqual(run.current_stage, "license_and_rights_check")

    def test_normalization_map_identity(self):
        text = "胸脇苦滿，欬而上氣"
        m = build_map(text)
        self.assertEqual(len(m.diplomatic), len(m.normalized))
        self.assertNotEqual(m.diplomatic, m.normalized)   # 脇→脅 欬→咳
        # 座標雙向恆等（1:1 折疊）
        self.assertEqual(m.slice_diplomatic(0, 4), text[:4])
        view = three_layer_view(text)
        self.assertTrue(view["normalization_map_id"].startswith("normmap_"))


if __name__ == "__main__":
    unittest.main()
