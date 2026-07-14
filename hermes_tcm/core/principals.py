"""主體（Principal）：角色 × 使用目的（Protocol §14.1–14.2）。

同一個角色還必須帶 purpose_of_use：研究者可以查看古代劑量原文，
但患者教育接口不能把該劑量轉換成現代可執行處方——角色與目的是
兩個獨立的授權維度。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict

ROLES = ("public", "student", "researcher", "editor", "clinician",
         "corpus_admin", "system_admin")

PURPOSES_OF_USE = ("historical_research", "teaching", "textual_criticism",
                   "publication", "clinical_reference", "patient_education",
                   "corpus_maintenance")

# 舊角色（hermes_shanghan）→ 新角色映射，兼容適配器使用
LEGACY_ROLE_MAP = {
    "patient": "public",
    "student": "student",
    "researcher": "researcher",
    "doctor": "clinician",
}


@dataclass
class Principal:
    subject: str
    role: str = "researcher"
    purpose_of_use: str = "historical_research"
    tenant_id: str = ""
    attributes: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.role not in ROLES:
            mapped = LEGACY_ROLE_MAP.get(self.role)
            if mapped is None:
                raise ValueError(f"未知角色 {self.role!r}（可用：{ROLES}）")
            self.role = mapped
        if self.purpose_of_use not in PURPOSES_OF_USE:
            raise ValueError(f"未知使用目的 {self.purpose_of_use!r}"
                             f"（可用：{PURPOSES_OF_USE}）")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Principal":
        return cls(subject=d.get("subject", ""),
                   role=d.get("role", "researcher"),
                   purpose_of_use=d.get("purpose_of_use",
                                        "historical_research"),
                   tenant_id=d.get("tenant_id", ""),
                   attributes=d.get("attributes", {}) or {})
