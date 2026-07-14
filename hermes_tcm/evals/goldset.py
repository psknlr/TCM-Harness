"""分層金標準（Protocol §16.2，P0-8）。

五類 P0 金標準類別：首見、異文、轉引、同名異書、OCR 噪聲。
金標準不隨機抽樣，按朝代/書類/文本長度/OCR 質量等因素分層；
重要樣本雙人獨立標注，計算 Cohen's κ。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

GOLD_CATEGORIES = ("earliest_attestation", "variant_reading",
                   "relay_quotation", "homonym_works", "ocr_noise")

STRATA_FACTORS = ("dynasty", "category", "text_length_bucket",
                  "ocr_quality_bucket", "has_variants", "is_homonym_work",
                  "is_relay", "is_commentary", "is_earliest_task",
                  "has_counterexample", "clinical_risk")


@dataclass
class GoldSample:
    sample_id: str
    category: str
    query: str
    gold_answer: str
    acceptable_variants: List[str] = field(default_factory=list)
    required_evidence: List[str] = field(default_factory=list)
    forbidden_claims: List[str] = field(default_factory=list)
    expected_tools: List[str] = field(default_factory=list)
    minimum_coverage: Dict[str, Any] = field(default_factory=dict)
    expected_release_decision: str = "pass"
    strata: Dict[str, Any] = field(default_factory=dict)
    annotators: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.category not in GOLD_CATEGORIES:
            raise ValueError(f"非法金標準類別 {self.category!r}")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def validate_sample(d: Dict) -> List[str]:
    """金標準樣本結構校驗：缺字段清單（空=合格）。"""
    problems: List[str] = []
    for f in ("sample_id", "category", "query", "gold_answer"):
        if not d.get(f):
            problems.append(f"缺少 {f}")
    if d.get("category") not in GOLD_CATEGORIES:
        problems.append(f"非法類別 {d.get('category')!r}")
    if d.get("category") == "earliest_attestation":
        if "citation.counter_search" not in (d.get("expected_tools") or []):
            problems.append("首見類樣本必須期望 citation.counter_search")
        if not d.get("forbidden_claims"):
            problems.append("首見類樣本必須聲明 forbidden_claims"
                            "（如「歷史首現」）")
    return problems


def stratify(samples: Sequence[Dict]) -> Dict[str, Dict]:
    """按分層因素統計覆蓋（暴露分層缺口）。"""
    out: Dict[str, Dict] = {f: {} for f in STRATA_FACTORS}
    for s in samples:
        strata = s.get("strata") or {}
        for f in STRATA_FACTORS:
            v = str(strata.get(f, "（未標）"))
            out[f][v] = out[f].get(v, 0) + 1
    return out


def cohens_kappa(labels_a: Sequence[str], labels_b: Sequence[str]) -> float:
    """雙標注者 Cohen's κ（確定性；標簽集自動歸併）。"""
    if len(labels_a) != len(labels_b) or not labels_a:
        raise ValueError("兩組標注長度必須一致且非空")
    n = len(labels_a)
    cats = sorted(set(labels_a) | set(labels_b))
    po = sum(1 for a, b in zip(labels_a, labels_b) if a == b) / n
    pe = sum((labels_a.count(c) / n) * (labels_b.count(c) / n)
             for c in cats)
    if pe == 1.0:
        return 1.0
    return round((po - pe) / (1 - pe), 4)
