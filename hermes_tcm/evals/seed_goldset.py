"""種子金標準（P0-8：首見、異文、轉引、同名異書、OCR 噪聲五類）。

種子樣本針對 tests/test_tcm_fixture 的微型全庫編寫——CI 離線可評；
真實全庫（笈成 803 部）的金標準按同一 schema 由標註閉環擴充
（雙人標註 + Cohen's κ，見 evals/goldset.py）。
"""
from __future__ import annotations

from typing import Dict, List

from .goldset import GoldSample, validate_sample

SEED_GOLDSET: List[Dict] = [
    {
        "sample_id": "seed_earliest_001",
        "category": "earliest_attestation",
        "query": "「奔豚」一詞最早見於哪部書？",
        "gold_answer": "在當前語料庫範圍內最早見於《漢方遺編》（東漢）",
        "acceptable_variants": ["漢方遺編"],
        "required_evidence": ["漢方遺編 卷上「奔豚上衝」段落"],
        "forbidden_claims": ["歷史首現", "歷史上最早", "古代從未有更早記載"],
        "expected_tools": ["citation.trace_quote",
                           "citation.counter_search",
                           "citation.trace_term"],
        "minimum_coverage": {"require_time_ordered": True,
                             "exhaustive_within_scope": True},
        "expected_release_decision": "pass",
        "strata": {"dynasty": "東漢", "category": "方書",
                   "is_earliest_task": True, "has_counterexample": False},
        "annotators": ["seed"],
    },
    {
        "sample_id": "seed_variant_001",
        "category": "variant_reading",
        "query": "丁氏經各傳本「脈浮緩」句異文",
        "gold_answer": "宋本作「脈浮緩者名曰中風」，明刊本作"
                       "「脈浮而緩者名曰中風」（多一「而」字）",
        "acceptable_variants": ["脈浮緩/脈浮而緩"],
        "required_evidence": ["丁氏經_宋本 上篇", "丁氏經_明刊本 上篇"],
        "forbidden_claims": ["某本為訛誤（校勘裁決屬專家審批）"],
        "expected_tools": ["collation.align_witnesses",
                           "collation.list_variants"],
        "minimum_coverage": {},
        "expected_release_decision": "pass",
        "strata": {"dynasty": "北宋/明", "category": "醫經",
                   "has_variants": True},
        "annotators": ["seed"],
    },
    {
        "sample_id": "seed_relay_001",
        "category": "relay_quotation",
        "query": "「奔豚氣上衝」在後世著作中的轉引",
        "gold_answer": "《丙氏傷寒注補遺》（清）載「奔豚氣上衝，甚則腹痛」"
                       "，與東漢《漢方遺編》「奔豚上衝」為部分重合的載錄鏈"
                       "候選；是否直接轉引屬人工判定",
        "acceptable_variants": [],
        "required_evidence": ["漢方遺編 卷上", "丙氏傷寒注補遺 補遺"],
        "forbidden_claims": ["確證直接抄錄（重合度判定不等於轉引定論）"],
        "expected_tools": ["citation.detect_relay"],
        "minimum_coverage": {"require_time_ordered": True},
        "expected_release_decision": "pass_with_warning",
        "strata": {"is_relay": True, "category": "傷寒"},
        "annotators": ["seed"],
    },
    {
        "sample_id": "seed_homonym_001",
        "category": "homonym_works",
        "query": "同名醫鑑是誰寫的？",
        "gold_answer": "庫中存在兩部《同名醫鑑》：王甲（明，綜合）與"
                       "李乙（清，醫案）——同名異書，需指定作者/朝代",
        "acceptable_variants": [],
        "required_evidence": ["catalog 身份解析（兩個 Work 候選）"],
        "forbidden_claims": ["把兩書當一書歸併", "單一作者斷言"],
        "expected_tools": ["catalog.resolve_work"],
        "minimum_coverage": {},
        "expected_release_decision": "review_required",
        "strata": {"is_homonym_work": True},
        "annotators": ["seed"],
    },
    {
        "sample_id": "seed_ocr_001",
        "category": "ocr_noise",
        "query": "低 OCR 質量傳本中檢索「奔豚」的負結論表述",
        "gold_answer": "自動檢索未見，尚需影像人工核查（low_ocr_quality "
                       "覆蓋下不得發布更強負結論）",
        "acceptable_variants": ["自動檢索未見"],
        "required_evidence": ["SearchCoverage（low_ocr_quality=true）"],
        "forbidden_claims": ["在本次定義的語料範圍內未見", "從未記載",
                             "首見結論"],
        "expected_tools": ["text.search_passages"],
        "minimum_coverage": {"low_ocr_quality": True},
        "expected_release_decision": "review_required",
        "strata": {"ocr_quality_bucket": "low"},
        "annotators": ["seed"],
    },
]


def load_seed_goldset() -> List[GoldSample]:
    """加載並結構校驗種子金標準（校驗失敗 fail-fast）。"""
    out: List[GoldSample] = []
    for d in SEED_GOLDSET:
        problems = validate_sample(d)
        if problems:
            raise ValueError(f"種子金標準 {d.get('sample_id')} 非法："
                             f"{problems}")
        out.append(GoldSample(**d))
    return out
