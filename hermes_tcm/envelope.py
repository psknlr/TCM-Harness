"""AnswerEnvelope：統一輸出協議（Protocol §15，P0 表格「工具輸出未聲明
語料範圍=0」的載體）。

所有端點返回統一信封，禁止只返回一段裸文本：answer + claims +
evidence + scope + uncertainty + limitations + run + release。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

ANSWER_TYPES = ("research_synthesis", "negative_result", "refusal",
                "clarification_needed", "tool_result")


@dataclass
class AnswerEnvelope:
    answer: str
    answer_type: str = "research_synthesis"
    claims: List[Dict] = field(default_factory=list)
    evidence: List[Dict] = field(default_factory=list)
    scope: Dict[str, Any] = field(default_factory=dict)
    uncertainty: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    run: Dict[str, Any] = field(default_factory=dict)
    release: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.answer_type not in ANSWER_TYPES:
            raise ValueError(f"非法 answer_type {self.answer_type!r}")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AnswerEnvelope":
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})


def citation_label(evidence: Dict) -> str:
    """證據 → 人類可讀引用標籤：《書名》章節（朝代）。"""
    title = evidence.get("work_title") or evidence.get("work_id", "")
    section = evidence.get("section", "")
    dynasty = evidence.get("dynasty", "")
    label = f"《{title}》"
    if section:
        label += f"·{section}"
    if dynasty:
        label += f"（{dynasty}）"
    return label


def evidence_entry(record: Dict) -> Dict:
    """EvidenceRecord dict → 信封 evidence 條目（帶 resource URI）。"""
    return {"evidence_id": record.get("evidence_id", ""),
            "citation_label": citation_label(record),
            "passage_id": record.get("passage_id", ""),
            "quote_hash": record.get("quote_hash", ""),
            "verification_level": record.get("verification_level", ""),
            "resource_uri": f"tcm://evidence/{record.get('evidence_id', '')}"}
