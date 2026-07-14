"""SearchCoverage：檢索範圍與「負證據」（Protocol §7，P0-3）。

全古籍研究中最危險的不是查不到，而是把「沒有查到」寫成「從未出現」。
每次檢索必須產生 SearchCoverage；負結論的措辭由覆蓋狀態**強制決定**
（Harness 策略，不再是工具文本說明）。
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

STOP_REASONS = ("complete", "scan_capped", "budget_exhausted", "timeout",
                "sampled", "error")


@dataclass
class SearchCoverage:
    coverage_id: str
    corpus_versions: List[str] = field(default_factory=list)
    included_categories: List[str] = field(default_factory=list)
    excluded_categories: List[str] = field(default_factory=list)
    dynasty_range: List[str] = field(default_factory=list)
    candidate_works: int = 0
    works_scanned: int = 0
    passages_scanned: int = 0
    query_forms: List[str] = field(default_factory=list)
    search_modes: List[str] = field(default_factory=list)
    scan_capped: bool = False
    exhaustive_within_scope: bool = False
    sampled_only: bool = False
    low_ocr_quality: bool = False
    earlier_partial_candidates: int = 0
    known_gaps: List[str] = field(default_factory=list)
    stop_reason: str = "complete"
    created_at: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    def __post_init__(self):
        if self.stop_reason not in STOP_REASONS:
            raise ValueError(f"非法 stop_reason {self.stop_reason!r}")
        # 一致性：掃描封頂/抽樣 與 exhaustive 互斥（自相矛盾的覆蓋聲明
        # 構造期即拒絕）
        if self.exhaustive_within_scope and (self.scan_capped
                                             or self.sampled_only):
            raise ValueError("覆蓋聲明矛盾：scan_capped/sampled_only 與 "
                             "exhaustive_within_scope 不能同時為真")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SearchCoverage":
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})


def coverage_id_for(query_forms: List[str], scope_note: str = "") -> str:
    digest = hashlib.sha256(
        ("|".join(sorted(query_forms)) + "\0" + scope_note).encode("utf-8")
    ).hexdigest()[:12]
    return f"cov_{digest}"


# ---------------------------------------------------------------------------
# 結論表達規則（Protocol §7.1 表格，逐行落地為策略函數）
# ---------------------------------------------------------------------------
def negative_statement(cov: SearchCoverage) -> Dict[str, Any]:
    """給定覆蓋狀態，返回允許的負結論措辭與強制限定語。

    返回 {"allowed": bool, "statement": str, "forbidden": [...]}——
    caller（synthesis / claim verifier）必須採用 statement 措辭，
    不得使用 forbidden 中的絕對化表達。
    """
    forbidden = ["古代從未記載", "歷史上首次出現", "從未出現", "絕無記載"]
    if cov.low_ocr_quality:
        return {"allowed": True,
                "statement": "自動檢索未見，尚需影像人工核查",
                "forbidden": forbidden,
                "reason": "low_ocr_quality"}
    if cov.sampled_only:
        return {"allowed": True,
                "statement": "抽樣結果未見",
                "forbidden": forbidden,
                "reason": "sampled_only"}
    if cov.scan_capped or cov.stop_reason in ("scan_capped",
                                              "budget_exhausted", "timeout"):
        return {"allowed": True,
                "statement": "在已掃描部分未見",
                "forbidden": forbidden,
                "reason": "scan_capped"}
    if cov.exhaustive_within_scope and cov.corpus_versions:
        return {"allowed": True,
                "statement": "在本次定義的語料範圍內未見",
                "forbidden": forbidden,
                "reason": "exhaustive_within_scope"}
    return {"allowed": False,
            "statement": "",
            "forbidden": forbidden + ["未見", "未有記載"],
            "reason": "coverage_insufficient：範圍未定義或版本未凍結，"
                      "不得發布任何負結論"}


def earliest_claim_allowed(cov: SearchCoverage) -> Dict[str, Any]:
    """「首見」結論的覆蓋前提：存在更早部分匹配候選時禁止發布。"""
    if cov.earlier_partial_candidates > 0:
        return {"allowed": False,
                "reason": f"存在 {cov.earlier_partial_candidates} 個更早"
                          "部分匹配候選——不得發布「首見」結論，需人工核驗"}
    neg = negative_statement(cov)
    if not neg["allowed"]:
        return {"allowed": False, "reason": neg["reason"]}
    if neg["reason"] != "exhaustive_within_scope":
        # 抽樣/封頂/OCR 不足的覆蓋只支持相應級別的負結論——最早載錄
        # 可能落在未掃描部分，「首見」+全庫限定語即是過度聲明
        return {"allowed": False,
                "reason": f"覆蓋非窮盡（{neg['reason']}）——「首見」結論需 "
                          "exhaustive_within_scope 覆蓋；當前覆蓋僅支持"
                          f"「{neg['statement']}」級別的負結論"}
    return {"allowed": True,
            "forced_qualifier": "在當前語料庫範圍內",
            "reason": neg["reason"]}
