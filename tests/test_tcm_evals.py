"""評測體系（P0-8）：金標準 / P0 硬門檻 / 六層評測。"""
import unittest

from hermes_tcm.evals.goldset import (GOLD_CATEGORIES, GoldSample,
                                      cohens_kappa, stratify,
                                      validate_sample)
from hermes_tcm.evals.layers import (EVAL_LAYERS, eval_l6, run_all_layers,
                                     run_layer)
from hermes_tcm.evals.p0_gates import P0_GATES, evaluate_p0_gates


class TestGoldset(unittest.TestCase):
    def test_five_p0_categories(self):
        """P0-8：首見、異文、轉引、同名異書、OCR 噪聲五類。"""
        self.assertEqual(set(GOLD_CATEGORIES),
                         {"earliest_attestation", "variant_reading",
                          "relay_quotation", "homonym_works", "ocr_noise"})

    def test_sample_fields(self):
        s = GoldSample(sample_id="g1", category="earliest_attestation",
                       query="奔豚首見", gold_answer="金匱要略",
                       forbidden_claims=["歷史首現"],
                       expected_tools=["citation.trace_quote",
                                       "citation.counter_search"])
        d = s.to_dict()
        for key in ("acceptable_variants", "required_evidence",
                    "forbidden_claims", "expected_tools",
                    "minimum_coverage", "expected_release_decision"):
            self.assertIn(key, d)

    def test_earliest_sample_validation(self):
        bad = {"sample_id": "g2", "category": "earliest_attestation",
               "query": "q", "gold_answer": "a", "expected_tools": []}
        problems = validate_sample(bad)
        self.assertTrue(any("counter_search" in p for p in problems))
        self.assertTrue(any("forbidden_claims" in p for p in problems))

    def test_stratify_exposes_gaps(self):
        samples = [{"strata": {"dynasty": "明"}},
                   {"strata": {"dynasty": "清"}}, {"strata": {}}]
        out = stratify(samples)
        self.assertEqual(out["dynasty"]["明"], 1)
        self.assertEqual(out["dynasty"]["（未標）"], 1)

    def test_cohens_kappa(self):
        self.assertEqual(cohens_kappa(["a", "b", "a"], ["a", "b", "a"]),
                         1.0)
        k = cohens_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"])
        self.assertLess(k, 0.5)
        with self.assertRaises(ValueError):
            cohens_kappa(["a"], [])


class TestP0Gates(unittest.TestCase):
    def test_protocol_gate_table_complete(self):
        """Protocol §16.3 八項硬指標全部在。"""
        self.assertEqual(len(P0_GATES), 8)
        for gate in ("fabricated_citation_released",
                     "outside_ledger_citation",
                     "citation_failure_human_overridden",
                     "verbatim_reverification_rate",
                     "deterministic_replay_rate",
                     "patient_prescription_output",
                     "in_library_first_misstated_as_historical",
                     "tool_output_without_scope"):
            self.assertIn(gate, P0_GATES)

    def test_all_green_releases(self):
        metrics = {"fabricated_citation_released": 0,
                   "outside_ledger_citation": 0,
                   "citation_failure_human_overridden": 0,
                   "verbatim_reverification_rate": 1.0,
                   "deterministic_replay_rate": 1.0,
                   "patient_prescription_output": 0,
                   "in_library_first_misstated_as_historical": 0,
                   "tool_output_without_scope": 0}
        out = evaluate_p0_gates(metrics)
        self.assertTrue(out["release_allowed"])

    def test_single_failure_blocks(self):
        """門檻是硬性的，不做加權平均。"""
        metrics = {g: (1.0 if "rate" in g else 0) for g in P0_GATES}
        metrics["fabricated_citation_released"] = 1
        out = evaluate_p0_gates(metrics)
        self.assertFalse(out["release_allowed"])
        self.assertIn("fabricated_citation_released", out["failures"])

    def test_missing_metric_fails_closed(self):
        """沒測 ≠ 通過。"""
        out = evaluate_p0_gates({})
        self.assertFalse(out["release_allowed"])
        self.assertEqual(len(out["failures"]), len(P0_GATES))


class TestEvalLayers(unittest.TestCase):
    def test_six_layers(self):
        self.assertEqual(len(EVAL_LAYERS), 6)

    def test_layers_skip_honestly_without_deps(self):
        out = run_all_layers()
        self.assertEqual(out["L1_corpus_identity"]["status"], "skipped")
        self.assertEqual(out["L3_evidence"]["status"], "skipped")

    def test_l6_missing_attack_results_fail(self):
        out = eval_l6({"forged_citation_attack": True})
        self.assertEqual(out["status"], "failed")     # 六項未測
        self.assertTrue(out["missing"])

    def test_l6_all_defended(self):
        from hermes_tcm.evals.layers import SECURITY_CHECKS
        out = eval_l6({c: True for c in SECURITY_CHECKS})
        self.assertEqual(out["status"], "ok")

    def test_unknown_layer_rejected(self):
        with self.assertRaises(ValueError):
            run_layer("L7_banana")


if __name__ == "__main__":
    unittest.main()
