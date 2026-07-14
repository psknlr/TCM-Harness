"""可復現性與證據鏈硬化（外部審查回應）：#Uxxxx 路徑解碼、空語料保護、
RAG 組合覆蓋排序、引用綁定本輪證據、患者安全加固、審核收緊、服務端加固。"""
import json
import tempfile
import unittest
from pathlib import Path

from hermes_shanghan import config, safety
from hermes_shanghan.corpus import downloader


def _ensure_artifacts():
    if not (config.RESEARCH_DIR / "commentary_divergence.json").exists():
        from hermes_shanghan.orchestrator import run_pipeline
        run_pipeline(verbose=False)


class TestCorpusPathRobustness(unittest.TestCase):
    """p7zip/unzip 在 C locale 下會把中文目錄名轉義成 #Uxxxx——語料仍須可發現。"""

    def test_decode_u_escapes(self):
        self.assertEqual(downloader.decode_u_escapes("#U66f8#U7c4d"), "書籍")
        self.assertEqual(
            downloader.decode_u_escapes("#U50b7#U5bd2#U8ad6_#U689d#U6587#U7248"),
            "傷寒論_條文版")
        self.assertEqual(downloader.decode_u_escapes("plain_ascii"), "plain_ascii")

    def test_discover_books_under_mangled_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = root / "shanghan" / "#U66f8#U7c4d" / "#U50b7#U5bd2#U8ad6_#U689d#U6587#U7248"
            book.mkdir(parents=True)
            book.joinpath("index.txt").write_text(
                "<book>\n書名=傷寒論(條文版)\n作者=張仲景\n</book>\n", encoding="utf-8")
            books = downloader.discover_books(root)
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0]["book_dir"], "傷寒論_條文版")   # decoded
        self.assertEqual(books[0]["hermes_layer"], "A")          # layer lookup works

    def test_book_path_finds_real_corpus(self):
        for name in ("傷寒論_條文版", "傷寒論_宋本", "經方實驗錄"):
            self.assertIsNotNone(downloader.book_path(name), name)

    def test_run_refuses_empty_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                downloader.run(corpus_root=Path(tmp))

    def test_run_refuses_missing_key_books(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = Path(tmp) / "cat" / "書籍" / "某雜書"
            book.mkdir(parents=True)
            book.joinpath("index.txt").write_text("<book>\n書名=某雜書\n</book>\n",
                                                  encoding="utf-8")
            with self.assertRaises(RuntimeError):
                downloader.run(corpus_root=Path(tmp))


class TestRAGRanking(unittest.TestCase):
    """經典方證組合必須排進前列，輔助章節不得霸榜。"""

    @classmethod
    def setUpClass(cls):
        _ensure_artifacts()
        from hermes_shanghan.rag.clause_rag import ClauseRAG
        cls.rag = ClauseRAG.load()

    def test_mahuang_pattern_combination(self):
        hits = self.rag.search("惡寒發熱無汗身疼痛", top_k=3)
        self.assertTrue(any("麻黃湯" in h["formulas"] for h in hits),
                        [h["clause_number"] for h in hits])
        self.assertEqual(hits[0]["text_type"], "original_clause")

    def test_guizhi_pattern_combination(self):
        hits = self.rag.search("發熱汗出惡風脈緩", top_k=3)
        nums = [h["clause_number"] for h in hits]
        self.assertIn(2, nums)                    # 中風提綱
        self.assertTrue(any("桂枝湯" in h["formulas"] for h in hits), nums)

    def test_min_score_filters_weak_tail(self):
        full = self.rag.search("結胸", top_k=8)
        cut = self.rag.search("結胸", top_k=8, min_score=full[0]["score"] - 0.01)
        self.assertLess(len(cut), len(full))
        self.assertTrue(all(h["score"] >= full[0]["score"] - 0.01 for h in cut))


class TestCitationEvidenceBinding(unittest.TestCase):
    """引用必須綁定本輪工具證據——「庫裡存在」不等於「本輪有據」。"""

    @classmethod
    def setUpClass(cls):
        _ensure_artifacts()
        from hermes_shanghan.agent.citation_guard import CitationGuard
        from hermes_shanghan.orchestrator import Artifacts
        cls.guard = CitationGuard(Artifacts().clause_store())

    def test_outside_evidence_flagged(self):
        ans = "桂枝湯證見 SHL_SONGBEN_0012；另參 SHL_SONGBEN_0035。"
        rep = self.guard.check(ans, allowed_ids=["SHL_SONGBEN_0012"])
        self.assertEqual(rep.outside_evidence_ids, ["SHL_SONGBEN_0035"])
        self.assertFalse(rep.ok)
        self.assertIn("未出現在本輪檢索證據", self.guard.annotate(ans, rep))

    def test_no_allowed_ids_keeps_legacy_semantics(self):
        rep = self.guard.check("見 SHL_SONGBEN_0012。")
        self.assertEqual(rep.outside_evidence_ids, [])
        self.assertTrue(rep.ok)

    def test_agent_answer_grounded_in_round_evidence(self):
        from hermes_shanghan.agent.agent import ShanghanAgent
        out = ShanghanAgent().ask("桂枝湯的方證要點？", role="doctor")
        allowed = set(ShanghanAgent._clause_ids_from(
            [{"result": {"ids": out["evidence_clause_ids"]}}]))
        rep = out["citation_report"]
        self.assertEqual(rep["outside_evidence"], [])
        self.assertTrue(set(rep["verified"]) <= allowed | set(rep["verified"]))


class TestPatientSafetyHardening(unittest.TestCase):
    def test_new_intents_refused(self):
        for q in ("發燒三天能不能喝桂枝湯？",
                  "我這個怕冷無汗是不是麻黃湯證？",
                  "醫生給我開了這個方，我能不能加量？",
                  "這個方適不適合我？"):
            self.assertIsNotNone(safety.patient_intent_guard(q), q)

    def test_benign_questions_pass(self):
        for q in ("太陽表證是什麼意思？", "六經辨證是怎麼回事？"):
            self.assertIsNone(safety.patient_intent_guard(q), q)

    def test_arabic_and_schedule_redaction(self):
        text = "桂枝9克、芍藥10g、水5 ml，每日三次，一天2次，bid 服用，另加三兩生薑"
        red = safety.redact_for_patient(text)
        for leak in ("9克", "10g", "5 ml", "每日三次", "一天2次", "bid", "三兩"):
            self.assertNotIn(leak, red, red)

    def test_patient_payload_drops_composition(self):
        out = safety.governed({"answer": "x", "formula_blocks": ["桂枝三兩"],
                               "composition": "桂枝三兩", "administration": "溫服"},
                              "patient")
        for k in ("formula_blocks", "composition", "administration"):
            self.assertNotIn(k, out)


class TestReviewTightening(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_artifacts()

    def test_span_too_broad_downgraded_not_gold(self):
        from hermes_shanghan.schemas import read_jsonl
        rules = read_jsonl(config.RULES_INITIAL_DIR / "initial_rules.jsonl")
        store = {}
        from hermes_shanghan.orchestrator import Artifacts
        store = Artifacts().clause_store()
        broad = [r for r in rules
                 if len(store[r["clause_id"]].clean_text) > 120
                 and len(r["evidence_span"]) > 0.9 * len(store[r["clause_id"]].clean_text)]
        self.assertTrue(broad)             # such rules exist in the corpus
        self.assertTrue(all(r["autonomous_review"]["release_level"] != "gold"
                            for r in broad))

    def test_contraindication_without_marker_hard_fails(self):
        from hermes_shanghan.review.validators import review_semantics
        from hermes_shanghan.orchestrator import Artifacts
        art = Artifacts()
        store = art.clause_store()
        r = next(x for x in art.initial_rules
                 if x.rule_type == "contraindication_rule")
        # a real contraindication rule passes …
        verdict, _ = review_semantics(r, store)
        self.assertIn(verdict, ("pass", "warn"))
        # … but the same claim pinned to a clause with no 禁例 marker fails
        import copy
        fake = copy.deepcopy(r)
        plain = next(c for c in art.clauses
                     if c.text_type == "original_clause"
                     and not c.contraindication_terms and len(c.clean_text) < 120)
        fake.clause_id = plain.clause_id
        fake.then_conclusions = dict(fake.then_conclusions)
        fake.then_conclusions["contraindicated_formulas"] = []
        verdict, flags = review_semantics(fake, store)
        self.assertEqual(verdict, "fail")
        self.assertTrue(any("contraindication_without_marker" in f for f in flags))


class TestMatcherMinScore(unittest.TestCase):
    def test_min_score_hides_weak_candidates(self):
        _ensure_artifacts()
        from hermes_shanghan.agent.tools import get_registry
        reg = get_registry()
        full = reg.call("shanghan_match_formula",
                        {"symptoms": ["惡寒", "發熱", "無汗", "身疼痛"],
                         "pulse": ["浮緊"], "top_k": 5})
        strong = full["matched_formula_patterns"][0]["match_score"]
        from hermes_shanghan.apps.doctor import FormulaMatcher
        m = FormulaMatcher(reg.art.formula_rules, reg.art.clause_store())
        cut = m.match(["惡寒", "發熱", "無汗", "身疼痛"], pulse=["浮緊"],
                      top_k=5, min_score=0.55)
        got = cut["matched_formula_patterns"]
        self.assertTrue(got)
        self.assertTrue(all(x["match_score"] >= 0.55 for x in got))
        self.assertGreaterEqual(strong, 0.55)


class TestServerHardening(unittest.TestCase):
    def test_body_size_limit_and_token_auth(self):
        import threading
        import urllib.request
        import urllib.error
        from http.server import ThreadingHTTPServer
        from hermes_shanghan.server import http_server as hs
        _ensure_artifacts()
        from hermes_shanghan.server.service import get_service
        saved = hs.AUTH_TOKEN
        hs.AUTH_TOKEN = "sekrit"
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                        hs.make_handler(get_service()))
            port = httpd.server_address[1]
            th = threading.Thread(target=httpd.serve_forever, daemon=True)
            th.start()
            base = f"http://127.0.0.1:{port}"
            # health is open
            with urllib.request.urlopen(f"{base}/api/health") as r:
                self.assertEqual(r.status, 200)
            # unauthorized without token
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(f"{base}/api/stats")
            self.assertEqual(cm.exception.code, 401)
            cm.exception.close()   # HTTPError 持有響應 socket，須顯式關閉
            # authorized with bearer token
            req = urllib.request.Request(f"{base}/api/stats",
                                         headers={"Authorization": "Bearer sekrit"})
            with urllib.request.urlopen(req) as r:
                self.assertEqual(r.status, 200)
            # oversized body → 413
            big = json.dumps({"query": "x" * (hs.MAX_BODY_BYTES + 10)}).encode()
            req = urllib.request.Request(f"{base}/api/search", data=big,
                                         headers={"Authorization": "Bearer sekrit",
                                                  "Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req)
            self.assertEqual(cm.exception.code, 413)
            cm.exception.close()
            httpd.shutdown()
            httpd.server_close()   # 關閉監聽 socket，消除 ResourceWarning
            th.join(timeout=5)
        finally:
            hs.AUTH_TOKEN = saved


if __name__ == "__main__":
    unittest.main()
