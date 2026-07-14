"""身份鏈（P0-2）：URN / WorkRegistry 歸組 / 同名異書拆分 / 可審計解析。"""
import unittest

from hermes_shanghan.corpus import library

from hermes_tcm.core.identity import (classify_source_type, detect_recension,
                                      merge_conflicts, parse_urn, passage_urn,
                                      witness_urn, work_urn)
from hermes_tcm.corpus.registry import WorkRegistry

try:
    from tests.test_tcm_fixture import TCMFixtureCase
except ImportError:
    from test_tcm_fixture import TCMFixtureCase


class TestUrns(unittest.TestCase):
    def test_urn_roundtrip(self):
        u = work_urn("傷寒論")
        kind, slug = parse_urn(u)
        self.assertEqual(kind, "work")
        self.assertEqual(u, f"urn:tcm:work:{slug}")

    def test_work_id_stable_across_processes(self):
        # sha256 slug：同輸入永遠同 id（不依賴進程 hash 種子）
        self.assertEqual(work_urn("傷寒論"), work_urn("傷寒論"))
        self.assertNotEqual(work_urn("傷寒論"), work_urn("金匱要略"))

    def test_disambiguator_changes_work_id(self):
        # 同名異書：消歧鍵不同 → 不同 work_id
        self.assertNotEqual(work_urn("同名醫鑑", "王甲|明"),
                            work_urn("同名醫鑑", "李乙|清"))

    def test_passage_urn_preserves_psg_format(self):
        # psg_ 12hex 是測試釘死的兼容面：URN 只包裹不改寫
        u = passage_urn("psg_0123456789ab")
        self.assertTrue(u.endswith("psg_0123456789ab"))

    def test_parse_rejects_garbage(self):
        self.assertIsNone(parse_urn("http://example.com/x"))
        self.assertIsNone(parse_urn("urn:tcm:nonsense:abc"))

    def test_recension_and_source_type(self):
        self.assertEqual(detect_recension("傷寒論_宋本"), "宋本")
        self.assertEqual(detect_recension("傷寒論"), "")
        self.assertEqual(classify_source_type("傷寒論點校本"),
                         "modern_edition")
        self.assertEqual(classify_source_type("傷寒論_宋本"),
                         "transcription")

    def test_merge_conflicts(self):
        a = {"author": "王甲", "dynasty": "明"}
        b = {"author": "李乙", "dynasty": "清"}
        conflicts = merge_conflicts(a, b)
        self.assertEqual({c["field"] for c in conflicts},
                         {"author", "dynasty"})
        # 空字段不構成衝突
        self.assertEqual(merge_conflicts(a, {"author": "", "dynasty": ""}),
                         [])


class TestWorkRegistry(TCMFixtureCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.reg = WorkRegistry(library.Library(cls.root))

    def test_homonym_works_not_merged(self):
        """同名異書：author/dynasty 衝突 → 拆分 + needs_review。"""
        res = self.reg.resolve_work("同名醫鑑")
        self.assertTrue(res.needs_human_adjudication)
        self.assertGreaterEqual(len(res.candidates), 2)
        self.assertFalse(res.resolved_work_id)   # 多義不強行解析
        wids = {c["work_id"] for c in res.candidates}
        self.assertEqual(len(wids), 2)
        for wid in wids:
            self.assertEqual(self.reg.works[wid].identity_status,
                             "needs_review")

    def test_witnesses_of_same_work_grouped(self):
        """傳本後綴只分 Witness 不拆 Work。"""
        res = self.reg.resolve_work("丁氏經")
        self.assertTrue(res.resolved_work_id)
        work = self.reg.works[res.resolved_work_id]
        self.assertEqual(len(work.witness_ids), 2)
        recensions = {self.reg.witnesses[w].recension
                      for w in work.witness_ids}
        self.assertEqual(recensions, {"宋本", "明刊本"})

    def test_resolution_auditable(self):
        """自動歸併必須輸出匹配依據/衝突/置信度/裁決標記（原則 4）。"""
        for r in self.reg.resolutions:
            self.assertTrue(r.matched_on or r.note)
            self.assertIsInstance(r.confidence, float)
        homonym = [r for r in self.reg.resolutions
                   if r.query == "同名醫鑑"]
        self.assertTrue(all(r.needs_human_adjudication for r in homonym))

    def test_witness_for_unit_chain(self):
        lib = library.Library(self.root)
        unit = next(u for u in lib.units if u["title"] == "漢方遺編")
        w = self.reg.witness_for_unit(unit["id"])
        self.assertIsNotNone(w)
        self.assertTrue(w.witness_id.startswith("urn:tcm:witness:"))
        self.assertTrue(w.work_id.startswith("urn:tcm:work:"))
        self.assertTrue(w.item_id.startswith("urn:tcm:item:"))
        work = self.reg.work_for_unit(unit["id"])
        self.assertIn(w.witness_id, work.witness_ids)

    def test_stats_counts(self):
        s = self.reg.stats()
        self.assertGreaterEqual(s["n_needs_review"], 2)
        self.assertEqual(s["library_fingerprint"], "tcm-fixture")

    def test_empty_metadata_unit_does_not_absorb_conflicts(self):
        """回歸：空元數據單元先入桶時，不得靜默吸收互相衝突的同名單元
        （桶內逐成員比對，不只比桶首）。"""
        import tempfile
        from pathlib import Path
        from hermes_shanghan.corpus import library as _lib
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            books = root / _lib.BOOKS_SUBDIR
            books.mkdir(parents=True)
            for name, meta in (
                    ("庚書_0", "書名=庚書\n"),                    # 空作者朝代，排最前
                    ("庚書_a", "書名=庚書\n作者=甲\n朝代=明\n"),
                    ("庚書_b", "書名=庚書\n作者=乙\n朝代=清\n")):
                d = books / name
                d.mkdir()
                d.joinpath("index.txt").write_text(
                    f"<book>\n{meta}分類=綜合\n</book>\n\n"
                    "=====卷=====\n\n文。\n", encoding="utf-8")
            cat = _lib.build_catalog(root, archive_sha256="z")
            _lib.build_char_index(root, cat)
            reg = WorkRegistry(_lib.Library(root))
            works = [w for w in reg.works.values()
                     if "庚書" in w.canonical_title]
            self.assertGreaterEqual(len(works), 2)
            self.assertTrue(reg.resolve_work("庚書")
                            .needs_human_adjudication)


if __name__ == "__main__":
    unittest.main()
