"""結論平面（P0-5）：ClaimRecord / Policy DSL / 反證義務 / 逐主張核驗。"""
import unittest

from hermes_tcm.claims.counterevidence import counter_search_obligations
from hermes_tcm.claims.policy_dsl import (ConclusionPolicyEngine,
                                          DEFAULT_POLICIES,
                                          policy_fingerprint)
from hermes_tcm.claims.records import ClaimRecord, claim_id_for
from hermes_tcm.claims.verifier import ClaimVerifier
from hermes_tcm.evidence.coverage import SearchCoverage
from hermes_tcm.evidence.ledger import TypedEvidenceLedger, mint_broker_token
from hermes_tcm.evidence.records import EvidenceRecord, quote_hash


def _ev(eid, verbatim, work_id="urn:tcm:work:aaa", witness="urn:tcm:witness:bbb",
        author="", dynasty="", level="V2", **kw):
    return EvidenceRecord(
        evidence_id=eid, corpus_version="cv1", work_id=work_id,
        witness_id=witness, verbatim=verbatim,
        quote_hash=quote_hash(verbatim), verification_level=level,
        tool_call_id="tc", span_id="sp",
        registered_by="capability_broker", author=author, dynasty=dynasty,
        **kw)


def _claim(ctype, text="測試主張", evidence=(), counter=False, scope=""):
    return ClaimRecord(claim_id=claim_id_for(text, ctype), claim_text=text,
                       claim_type=ctype,
                       supporting_evidence=list(evidence),
                       counter_search_performed=counter, scope_id=scope)


def _full_coverage(**kw):
    defaults = dict(coverage_id="cov_1", corpus_versions=["cv1"],
                    exhaustive_within_scope=True,
                    search_modes=["exact", "variant_folded",
                                  "dynasty_ordered"])
    defaults.update(kw)
    return SearchCoverage(**defaults)


class TestClaimRecord(unittest.TestCase):
    def test_risk_defaults_by_type(self):
        self.assertEqual(_claim("earliest_attestation").risk,
                         "chronological")
        self.assertEqual(_claim("clinical_recommendation").risk, "clinical")

    def test_invalid_type_rejected(self):
        with self.assertRaises(ValueError):
            _claim("banana")


class TestPolicyDSL(unittest.TestCase):
    def setUp(self):
        self.engine = ConclusionPolicyEngine()

    def test_versioned_and_fingerprinted(self):
        self.assertTrue(self.engine.version)
        self.assertEqual(self.engine.fingerprint,
                         policy_fingerprint(DEFAULT_POLICIES))
        exported = self.engine.to_json()
        import json
        loaded = json.loads(exported)
        self.assertEqual(loaded["policy_version"], self.engine.version)

    def test_earliest_requires_counter_search(self):
        ev = [_ev("ev_1", "奔豚上衝", dynasty="東漢")]
        claim = _claim("earliest_attestation", evidence=["ev_1"],
                       counter=False)
        out = self.engine.evaluate(claim, ev, coverage=_full_coverage(),
                                   tools_used=["citation.trace_quote",
                                               "citation.counter_search"])
        self.assertEqual(out["verdict"], "fail")
        self.assertTrue(any("反證搜索" in v for v in out["violations"]))

    def test_earliest_requires_tools(self):
        ev = [_ev("ev_1", "奔豚上衝")]
        claim = _claim("earliest_attestation", evidence=["ev_1"],
                       counter=True)
        out = self.engine.evaluate(claim, ev, coverage=_full_coverage(),
                                   tools_used=[])
        self.assertEqual(out["verdict"], "fail")
        self.assertTrue(any("缺少必需工具" in v for v in out["violations"]))

    def test_earliest_passes_and_forces_qualifier(self):
        ev = [_ev("ev_1", "奔豚上衝", dynasty="東漢")]
        claim = _claim("earliest_attestation", evidence=["ev_1"],
                       counter=True)
        out = self.engine.evaluate(
            claim, ev, coverage=_full_coverage(),
            tools_used=["citation.trace_quote", "citation.counter_search"])
        self.assertEqual(out["verdict"], "pass")
        self.assertIn("在當前語料庫範圍內", out["forced_qualifiers"])

    def test_earliest_blocked_by_earlier_partial_candidate(self):
        ev = [_ev("ev_1", "奔豚上衝")]
        claim = _claim("earliest_attestation", evidence=["ev_1"],
                       counter=True)
        cov = _full_coverage(earlier_partial_candidates=1)
        out = self.engine.evaluate(
            claim, ev, coverage=cov,
            tools_used=["citation.trace_quote", "citation.counter_search"])
        self.assertEqual(out["verdict"], "fail")

    def test_consensus_requires_three_works(self):
        ev = [_ev("ev_1", "文甲", work_id="urn:tcm:work:a", author="甲",
                  dynasty="明"),
              _ev("ev_2", "文乙", work_id="urn:tcm:work:b", author="乙",
                  dynasty="清")]
        claim = _claim("broad_consensus", evidence=["ev_1", "ev_2"],
                       counter=True)
        out = self.engine.evaluate(claim, ev, coverage=_full_coverage())
        self.assertEqual(out["verdict"], "fail")
        self.assertTrue(any("distinct_works" in v
                            for v in out["violations"]))

    def test_semantic_drift_forbids_frequency_only(self):
        """必須避免的錯誤之四：頻次證據不得單獨支持語義主張。"""
        freq_only = [_ev("ev_1", "統計摘要", work_id="urn:tcm:work:a",
                         dynasty="明",
                         epistemic_status="source_assertion"),
                     _ev("ev_2", "統計摘要2", work_id="urn:tcm:work:b",
                         dynasty="清",
                         epistemic_status="source_assertion")]
        claim = _claim("semantic_drift", evidence=["ev_1", "ev_2"])
        out = self.engine.evaluate(claim, freq_only,
                                   coverage=_full_coverage())
        self.assertEqual(out["verdict"], "fail")
        self.assertTrue(any("頻次" in v for v in out["violations"]))

    def test_clinical_role_restricted_and_mandatory_review(self):
        ev = [_ev("ev_1", "某方主之")]
        claim = _claim("clinical_recommendation", evidence=["ev_1"])
        out = self.engine.evaluate(claim, ev, role="researcher")
        self.assertEqual(out["verdict"], "fail")     # 角色限制
        out2 = self.engine.evaluate(claim, ev, role="clinician")
        self.assertEqual(out2["verdict"], "review_required")

    def test_negative_requires_coverage(self):
        claim = _claim("negative_result")
        out = self.engine.evaluate(claim, [], coverage=None)
        self.assertEqual(out["verdict"], "fail")
        out2 = self.engine.evaluate(claim, [], coverage=_full_coverage())
        self.assertEqual(out2["verdict"], "pass")

    def test_unknown_claim_type_fails_closed(self):
        claim = _claim("attestation")
        engine = ConclusionPolicyEngine(policies={})
        out = engine.evaluate(claim, [])
        self.assertEqual(out["verdict"], "fail")

    def test_verification_level_floor(self):
        weak = [_ev("ev_1", "原文", level="V1")]
        claim = _claim("earliest_attestation", evidence=["ev_1"],
                       counter=True)
        out = self.engine.evaluate(
            claim, weak, coverage=_full_coverage(),
            tools_used=["citation.trace_quote", "citation.counter_search"])
        self.assertEqual(out["verdict"], "fail")     # 需 V2


class TestCounterObligations(unittest.TestCase):
    def test_short_term_uses_variant_timeline(self):
        claim = _claim("earliest_attestation", text="奔豚首見")
        obs = counter_search_obligations(claim, ["奔豚"])
        kinds = {o["kind"] for o in obs}
        self.assertIn("variant_form_search", kinds)
        self.assertNotIn("earlier_partial_match", kinds)   # <8 字無半探針

    def test_long_quote_gets_partial_probe(self):
        claim = _claim("earliest_attestation", text="長引文首見")
        obs = counter_search_obligations(claim, ["奔豚者從少腹起上衝咽喉"])
        kinds = {o["kind"] for o in obs}
        self.assertIn("earlier_partial_match", kinds)

    def test_consensus_needs_dissenting_search(self):
        claim = _claim("broad_consensus")
        obs = counter_search_obligations(claim, ["某說"])
        self.assertIn("dissenting_source", {o["kind"] for o in obs})


class TestClaimVerifier(unittest.TestCase):
    def setUp(self):
        self.ledger = TypedEvidenceLedger("cv1")
        self.tok = mint_broker_token()
        self.rec = _ev("ev_1", "奔豚上衝，灸其核上", dynasty="東漢")
        self.ledger.register("n1", self.rec, self.tok)
        self.verifier = ClaimVerifier(self.ledger)

    def test_outside_ledger_evidence_fails_attribution(self):
        """台賬外證據=偽造（核心不變量 3）。"""
        claim = _claim("attestation", evidence=["ev_NOT_IN_LEDGER"])
        self.verifier.verify(claim)
        self.assertEqual(claim.status, "failed")
        self.assertEqual(claim.verification["attribution"], "fail")

    def test_verified_attestation(self):
        claim = _claim("attestation",
                       text="《某書》載：「奔豚上衝，灸其核上」",
                       evidence=["ev_1"])
        self.verifier.verify(claim, coverage=_full_coverage())
        self.assertEqual(claim.status, "verified")
        self.assertEqual(claim.verification["quotation"], "pass")

    def test_quotation_tamper_detected(self):
        self.rec.verbatim = "被篡改"
        claim = _claim("attestation", text="《某書》載：「被篡改」",
                       evidence=["ev_1"])
        self.verifier.verify(claim)
        self.assertEqual(claim.status, "failed")
        self.assertEqual(claim.verification["quotation"], "fail")

    def test_semantic_support_not_bypassed_by_empty_work_title(self):
        """回歸：空 work_title 的 '' in text 恆真，不得旁路語義支持核驗。"""
        rec = _ev("ev_empty", "某段原文", work_id="urn:tcm:work:z",
                  author="", dynasty="明")
        rec.work_title = ""     # 空標題
        self.ledger.register("n2", rec, self.tok)
        claim = _claim("attestation", text="與證據無關的主張文本",
                       evidence=["ev_empty"])
        self.verifier.verify(claim, coverage=_full_coverage())
        # 摘錄不在主張文本、work_title 為空 → 語義支持不通過 → needs_review
        self.assertEqual(claim.verification["semantic_support"], "review")

    def test_contradicting_evidence_forces_review(self):
        """回歸：登記在案的反對證據必須進人工複核，不得直接 verified。"""
        contra = _ev("ev_contra", "反例原文", dynasty="宋")
        self.ledger.register("n3", contra, self.tok)
        claim = _claim("attestation",
                       text="《某書》載：「奔豚上衝，灸其核上」",
                       evidence=["ev_1"])
        claim.contradicting_evidence = ["ev_contra"]
        self.verifier.verify(claim, coverage=_full_coverage())
        self.assertEqual(claim.status, "needs_review")
        self.assertEqual(claim.verification["contradiction"], "review")

    def test_earliest_time_ordered_fails_closed_without_coverage(self):
        """回歸：coverage=None 時 require_time_ordered/首見覆蓋要求
        fail-closed（不靜默放行）。"""
        engine = ConclusionPolicyEngine()
        ev = [_ev("ev_1", "奔豚上衝", dynasty="東漢")]
        claim = _claim("earliest_attestation", evidence=["ev_1"],
                       counter=True)
        out = engine.evaluate(
            claim, ev, coverage=None,
            tools_used=["citation.trace_quote", "citation.counter_search"])
        self.assertEqual(out["verdict"], "fail")

    def test_per_claim_coverage_lookup(self):
        good = _full_coverage(coverage_id="cov_good")
        bad = _full_coverage(coverage_id="cov_bad",
                             exhaustive_within_scope=False,
                             stop_reason="scan_capped", scan_capped=True,
                             earlier_partial_candidates=3)
        c1 = _claim("earliest_attestation", text="首見甲",
                    evidence=["ev_1"], counter=True, scope="cov_good")
        c2 = _claim("earliest_attestation", text="首見乙",
                    evidence=["ev_1"], counter=True, scope="cov_bad")
        summary = self.verifier.verify_all(
            [c1, c2],
            tools_used=["citation.trace_quote", "citation.counter_search"],
            coverage_lookup={"cov_good": good, "cov_bad": bad})
        self.assertEqual(c1.status, "verified")
        self.assertEqual(c2.status, "failed")      # 更早候選覆蓋 → fail
        self.assertEqual(summary["n_failed"], 1)


if __name__ == "__main__":
    unittest.main()
