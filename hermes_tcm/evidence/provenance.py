"""PROV-O 風格來源鏈（Protocol §5.3）：Entity—Activity—Agent。

記錄 OCR、規範化、人工修訂、規則抽取和模型生成之間的派生鏈；
可導出 JSON-LD（@context 指向 W3C PROV）。純標準庫。
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

PROV_CONTEXT = "http://www.w3.org/ns/prov"

ACTIVITY_TYPES = ("ingest", "ocr", "normalization", "human_revision",
                  "segmentation", "rule_extraction", "model_generation",
                  "verification")


@dataclass
class ProvActivity:
    activity_id: str
    activity_type: str
    agent: str                       # 責任主體（軟件版本/人員標識）
    used: List[str] = field(default_factory=list)      # 輸入 Entity ids
    generated: List[str] = field(default_factory=list)  # 輸出 Entity ids
    started_at: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    ended_at: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.activity_type not in ACTIVITY_TYPES:
            raise ValueError(f"非法 activity_type {self.activity_type!r}")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def activity_id_for(activity_type: str, agent: str, inputs: List[str]) -> str:
    digest = hashlib.sha256(
        f"{activity_type}|{agent}|{'|'.join(sorted(inputs))}".encode("utf-8")
    ).hexdigest()[:12]
    return f"prov_act_{digest}"


class ProvChain:
    """一組派生活動的鏈式視圖；可回答「這個對象是怎麼來的」。"""

    def __init__(self):
        self._activities: Dict[str, ProvActivity] = {}
        self._generated_by: Dict[str, str] = {}

    def record(self, activity: ProvActivity) -> None:
        self._activities[activity.activity_id] = activity
        for entity in activity.generated:
            self._generated_by[entity] = activity.activity_id

    def derivation_of(self, entity_id: str, max_depth: int = 20) -> List[Dict]:
        """entity 的完整派生鏈（新→舊）。環路/超深如實截斷。"""
        chain: List[Dict] = []
        seen = set()
        cur = entity_id
        for _ in range(max_depth):
            act_id = self._generated_by.get(cur)
            if act_id is None or act_id in seen:
                break
            seen.add(act_id)
            act = self._activities[act_id]
            chain.append(act.to_dict())
            if not act.used:
                break
            cur = act.used[0]
        return chain

    def to_jsonld(self) -> Dict[str, Any]:
        return {
            "@context": {"prov": PROV_CONTEXT},
            "@graph": [
                {"@id": a.activity_id,
                 "@type": "prov:Activity",
                 "prov:wasAssociatedWith": a.agent,
                 "prov:used": [{"@id": e} for e in a.used],
                 "prov:generated": [{"@id": e} for e in a.generated],
                 "prov:startedAtTime": a.started_at,
                 "prov:endedAtTime": a.ended_at,
                 "activity_type": a.activity_type}
                for a in self._activities.values()],
        }
