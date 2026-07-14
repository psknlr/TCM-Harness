"""證據平面（P0-1/3/4）：EvidenceRecord V2 / 強類型台賬 / SearchCoverage。"""
import unittest

from hermes_tcm.core.schemas import (legacy_layer_to_roles,
                                     roles_to_legacy_layer,
                                     verification_at_least)
from hermes_tcm.evidence.coverage import (SearchCoverage,
                                          earliest_claim_allowed,
                                          negative_statement)
from hermes_tcm.evidence.ledger import (LedgerWriteViolation,
                                        TypedEvidenceLedger,
                                        mint_broker_token)
from hermes_tcm.evidence.packets import build_packet, verify_packet
from hermes_tcm.evidence.provenance import (ProvActivity, ProvChain,
                                            activity_id_for)
from hermes_tcm.evidence.records import (EvidenceRecord,
                                         from_legacy_p_record, quote_hash)


def _record(eid="ev_1", verbatim="奔豚氣上衝", **kw):
    defaults = dict(
        evidence_id=eid, corpus_version="cv1",
        verbatim=verbatim, quote_hash=quote_hash(verbatim),
        verification_level="V1", tool_call_id="tc1", span_id="sp1",
        registered_by="capability_broker")
    defaults.update(kw)
    return EvidenceRecord(**defaults)


class TestEvidenceRecordV2(unittest.TestCase):
    def test_hash_mismatch_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            EvidenceRecord(evidence_id="ev_x", corpus_version="cv1",
                           verbatim="甲", quote_hash="deadbeef",
                           verification_level="V1")

    def test_v1_without_verbatim_rejected(self):
        """passage_id 存在但正文未返回 → 只能是 V0（強不變量 4）。"""
        with self.assertRaises(ValueError):
            EvidenceRecord(evidence_id="ev_x", corpus_version="cv1",
                           verbatim="", quote_hash="",
                           verification_level="V1")

    def test_v0_metadata_not_primary_text(self):
        r = EvidenceRecord(evidence_id="ev_m", corpus_version="cv1",
                           passage_id="psg_0123456789ab",
                           verification_level="V0")
        self.assertFalse(r.is_primary_text_returned)

    def test_roundtrip(self):
        r = _record()
        r2 = EvidenceRecord.from_dict(r.to_dict())
        self.assertEqual(r2.evidence_id, r.evidence_id)
        self.assertEqual(r2.quote_hash, r.quote_hash)

    def test_invalid_dimensions_rejected(self):
        for field, value in (("source_role", "banana"),
                             ("witness_role", "banana"),
                             ("epistemic_status", "banana"),
                             ("verification_level", "V9")):
            with self.assertRaises(ValueError):
                _record(**{field: value})

    def test_legacy_layer_compat_mapping(self):
        a = legacy_layer_to_roles("A")
        self.assertEqual(a["source_role"], "primary_text")
        self.assertEqual(a["epistemic_status"], "verbatim")
        self.assertEqual(roles_to_legacy_layer(**a), "A")
        b = legacy_layer_to_roles("B")
        self.assertEqual(b["witness_role"], "variant_witness")
        self.assertEqual(roles_to_legacy_layer(**b), "B")
        self.assertEqual(roles_to_legacy_layer(
            **legacy_layer_to_roles("E")), "E")

    def test_verification_order(self):
        self.assertTrue(verification_at_least("V3", "V2"))
        self.assertFalse(verification_at_least("V1", "V2"))
        self.assertFalse(verification_at_least("banana", "V0"))

    def test_from_legacy_p_record(self):
        legacy = {"passage_id": "psg_0123456789ab", "work_id": "某書",
                  "work_title": "某書", "author": "某人", "dynasty": "明",
                  "category": "方書", "section": "卷一",
                  "verbatim_text": "奔豚者氣上衝也",
                  "char_start": 0, "char_end": 7,
                  "quote_hash": quote_hash("奔豚者氣上衝也"),
                  "retrieval_query": "奔豚", "retrieval_rank": 0}
        r = from_legacy_p_record(legacy, corpus_version="cv1")
        self.assertEqual(r.verification_level, "V1")   # 無註冊表→V1
        self.assertEqual(r.source_role, "formula_book")
        self.assertTrue(r.work_id.startswith("urn:tcm:work:"))
        self.assertTrue(r.is_primary_text_returned)

    def test_from_legacy_tampered_hash_rejected(self):
        legacy = {"passage_id": "psg_0123456789ab", "work_id": "某書",
                  "verbatim_text": "奔豚者氣上衝也",
                  "quote_hash": "0000000000000000"}
        with self.assertRaises(ValueError):
            from_legacy_p_record(legacy, corpus_version="cv1")


class TestTypedLedger(unittest.TestCase):
    def test_broker_token_required(self):
        led = TypedEvidenceLedger("cv1")
        with self.assertRaises(LedgerWriteViolation):
            led.register("n1", _record(), object())     # 偽造令牌

    def test_forged_token_class_rejected(self):
        led = TypedEvidenceLedger("cv1")

        class FakeToken:
            owner = "capability_broker"
        with self.assertRaises(LedgerWriteViolation):
            led.register("n1", _record(), FakeToken())

    def test_missing_binding_fields_rejected(self):
        led = TypedEvidenceLedger("cv1")
        tok = mint_broker_token()
        with self.assertRaises(LedgerWriteViolation):
            led.register("n1", _record(tool_call_id="", span_id=""), tok)

    def test_wrong_registered_by_rejected(self):
        led = TypedEvidenceLedger("cv1")
        tok = mint_broker_token()
        with self.assertRaises(LedgerWriteViolation):
            led.register("n1", _record(registered_by="model_output"), tok)

    def test_corpus_version_mismatch_rejected(self):
        led = TypedEvidenceLedger("cv1")
        tok = mint_broker_token()
        with self.assertRaises(LedgerWriteViolation):
            led.register("n1", _record(corpus_version="cv2"), tok)

    def test_register_dedup_and_allowlist(self):
        led = TypedEvidenceLedger("cv1")
        tok = mint_broker_token()
        r = _record(passage_id="psg_0123456789ab")
        self.assertTrue(led.register("n1", r, tok))
        self.assertFalse(led.register("n1", r, tok))    # 去重
        self.assertEqual(led.primary_text_ids(), ["ev_1"])
        self.assertEqual(led.citable_passage_ids(), ["psg_0123456789ab"])
        # V0 元數據記錄不進允許集
        led.register("n1", EvidenceRecord(
            evidence_id="ev_meta", corpus_version="cv1",
            passage_id="psg_ffffffffffff", verification_level="V0",
            tool_call_id="tc", span_id="sp",
            registered_by="capability_broker"), tok)
        self.assertNotIn("ev_meta", led.primary_text_ids())

    def test_roundtrip_requires_token(self):
        led = TypedEvidenceLedger("cv1")
        tok = mint_broker_token()
        led.register("n1", _record(), tok)
        d = led.to_dict()
        led2 = TypedEvidenceLedger.from_dict(d, tok)
        self.assertEqual(len(led2), 1)
        with self.assertRaises(LedgerWriteViolation):
            TypedEvidenceLedger.from_dict(d, object())

    def test_integrity_audit(self):
        led = TypedEvidenceLedger("cv1")
        tok = mint_broker_token()
        led.register("n1", _record(), tok)
        self.assertEqual(led.verify_integrity(), [])


class TestSearchCoverage(unittest.TestCase):
    def test_negative_statement_matrix(self):
        """Protocol §7.1 表格逐行。"""
        full = SearchCoverage(coverage_id="c1", corpus_versions=["cv1"],
                              exhaustive_within_scope=True)
        self.assertEqual(negative_statement(full)["statement"],
                         "在本次定義的語料範圍內未見")
        capped = SearchCoverage(coverage_id="c2", scan_capped=True,
                                stop_reason="scan_capped")
        self.assertEqual(negative_statement(capped)["statement"],
                         "在已掃描部分未見")
        sampled = SearchCoverage(coverage_id="c3", sampled_only=True,
                                 stop_reason="sampled")
        self.assertEqual(negative_statement(sampled)["statement"],
                         "抽樣結果未見")
        ocr = SearchCoverage(coverage_id="c4", low_ocr_quality=True)
        self.assertIn("影像人工核查", negative_statement(ocr)["statement"])

    def test_bare_negative_forbidden(self):
        """範圍未定義/版本未凍結 → 不得發布任何負結論。"""
        bare = SearchCoverage(coverage_id="c5")     # 無版本、非窮盡
        out = negative_statement(bare)
        self.assertFalse(out["allowed"])
        self.assertIn("古代從未記載", out["forbidden"])

    def test_contradictory_coverage_rejected(self):
        with self.assertRaises(ValueError):
            SearchCoverage(coverage_id="c6", scan_capped=True,
                           exhaustive_within_scope=True)

    def test_earliest_blocked_by_partial_candidates(self):
        cov = SearchCoverage(coverage_id="c7", corpus_versions=["cv1"],
                             exhaustive_within_scope=True,
                             earlier_partial_candidates=2)
        gate = earliest_claim_allowed(cov)
        self.assertFalse(gate["allowed"])
        clean = SearchCoverage(coverage_id="c8", corpus_versions=["cv1"],
                               exhaustive_within_scope=True)
        gate2 = earliest_claim_allowed(clean)
        self.assertTrue(gate2["allowed"])
        self.assertEqual(gate2["forced_qualifier"], "在當前語料庫範圍內")


class TestPacketsAndProvenance(unittest.TestCase):
    def test_packet_verify_and_roundtrip(self):
        recs = [_record(), _record(eid="ev_2", verbatim="脈浮緩")]
        pkt = build_packet("測試", recs, corpus_version="cv1")
        self.assertTrue(pkt.verification["ok"])
        self.assertFalse(pkt.verification["reverified_against_library"])
        from hermes_tcm.evidence.packets import EvidencePacket
        pkt2 = EvidencePacket.from_dict(pkt.to_dict())
        self.assertEqual(pkt2.packet_id, pkt.packet_id)
        self.assertEqual(len(pkt2.records), 2)

    def test_packet_detects_tampering(self):
        r = _record()
        r.verbatim = "被篡改的文本"          # hash 不再匹配
        v = verify_packet([r])
        self.assertFalse(v["ok"])
        self.assertEqual(v["failures"][0]["reason"], "quote_hash_mismatch")

    def test_prov_chain(self):
        chain = ProvChain()
        a1 = ProvActivity(activity_id=activity_id_for("ingest", "sys",
                                                      ["raw1"]),
                          activity_type="ingest", agent="sys",
                          used=["raw1"], generated=["dip1"])
        a2 = ProvActivity(activity_id=activity_id_for("normalization",
                                                      "sys", ["dip1"]),
                          activity_type="normalization", agent="sys",
                          used=["dip1"], generated=["norm1"])
        chain.record(a1)
        chain.record(a2)
        deriv = chain.derivation_of("norm1")
        self.assertEqual([d["activity_type"] for d in deriv],
                         ["normalization", "ingest"])
        graph = chain.to_jsonld()["@graph"]
        self.assertEqual(len(graph), 2)


if __name__ == "__main__":
    unittest.main()
