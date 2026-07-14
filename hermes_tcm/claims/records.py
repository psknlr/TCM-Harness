"""ClaimRecord：結構化主張（Protocol §8.1）。

最終答案不直接由 LLM 生成整段 prose 再掃描引用，而是先形成
Claim Graph：每個事實性主張都是一個 ClaimRecord，綁定支持/反對
證據與覆蓋範圍，經策略引擎逐主張核驗後才進入綜合表達。
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from ..core.schemas import CLAIM_RISKS

CLAIM_TYPES = (
    "earliest_attestation",   # 首見/最早載錄
    "attestation",            # 某書載有某文/某術語
    "broad_consensus",        # 普遍認為/多數注家
    "variant_reading",        # 傳本異文
    "quotation_relay",        # 轉引關係
    "semantic_drift",         # 概念/語義演變
    "formula_lineage",        # 方劑源流
    "negative_result",        # 未見記載（必須綁定 SearchCoverage）
    "synthesis",              # 綜合推斷
    "clinical_recommendation",  # 臨床建議（最高風險）
)

CLAIM_STATUSES = ("draft", "verified", "failed", "needs_review")

# claim_type → 默認風險維度
CLAIM_TYPE_RISK = {
    "earliest_attestation": "chronological",
    "attestation": "descriptive",
    "broad_consensus": "consensus",
    "variant_reading": "descriptive",
    "quotation_relay": "chronological",
    "semantic_drift": "causal",
    "formula_lineage": "chronological",
    "negative_result": "descriptive",
    "synthesis": "causal",
    "clinical_recommendation": "clinical",
}


@dataclass
class ClaimRecord:
    claim_id: str
    claim_text: str
    claim_type: str
    risk: str = ""
    scope_id: str = ""                 # SearchCoverage id
    epistemic_status: str = "bounded_inference"
    supporting_evidence: List[str] = field(default_factory=list)
    contradicting_evidence: List[str] = field(default_factory=list)
    counter_search_performed: bool = False
    forced_qualifiers: List[str] = field(default_factory=list)
    status: str = "draft"
    verification: Dict[str, Any] = field(default_factory=dict)
    policy_id: str = ""
    policy_version: str = ""
    notes: str = ""

    def __post_init__(self):
        if self.claim_type not in CLAIM_TYPES:
            raise ValueError(f"非法 claim_type {self.claim_type!r}"
                             f"（可用：{CLAIM_TYPES}）")
        if not self.risk:
            self.risk = CLAIM_TYPE_RISK[self.claim_type]
        if self.risk not in CLAIM_RISKS:
            raise ValueError(f"非法 claim_risk {self.risk!r}")
        if self.status not in CLAIM_STATUSES:
            raise ValueError(f"非法 claim status {self.status!r}")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ClaimRecord":
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})


def claim_id_for(claim_text: str, claim_type: str) -> str:
    digest = hashlib.sha256(
        f"{claim_type}\0{claim_text}".encode("utf-8")).hexdigest()[:12]
    return f"clm_{digest}"
