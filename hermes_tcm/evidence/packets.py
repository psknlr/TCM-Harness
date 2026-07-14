"""EvidencePacket：可獨立核驗、可跨代理傳遞的證據包（Protocol §11.2）。

專家子代理只接收 Evidence Packet，不讀取彼此結論——包本身攜帶
重驗結果與庫指紋，Synthesizer 只基於已驗證的包綜合。
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .coverage import SearchCoverage
from .records import EvidenceRecord, quote_hash


@dataclass
class EvidencePacket:
    packet_id: str
    topic: str
    records: List[EvidenceRecord] = field(default_factory=list)
    coverage: Optional[SearchCoverage] = None
    verification: Dict[str, Any] = field(default_factory=dict)
    library_fingerprint: str = ""
    corpus_version: str = ""
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["records"] = [r.to_dict() for r in self.records]
        d["coverage"] = self.coverage.to_dict() if self.coverage else None
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EvidencePacket":
        recs = [EvidenceRecord.from_dict(r) for r in d.get("records", [])]
        cov = d.get("coverage")
        return cls(packet_id=d.get("packet_id", ""),
                   topic=d.get("topic", ""),
                   records=recs,
                   coverage=SearchCoverage.from_dict(cov) if cov else None,
                   verification=d.get("verification", {}) or {},
                   library_fingerprint=d.get("library_fingerprint", ""),
                   corpus_version=d.get("corpus_version", ""),
                   note=d.get("note", ""))

    @property
    def n_works(self) -> int:
        return len({r.work_id for r in self.records if r.work_id})

    @property
    def evidence_ids(self) -> List[str]:
        return [r.evidence_id for r in self.records]


def packet_id_for(topic: str, records: Sequence[EvidenceRecord]) -> str:
    body = "|".join(sorted(r.quote_hash for r in records))
    digest = hashlib.sha256(f"{topic}|{body}".encode("utf-8")).hexdigest()[:12]
    return f"pkt_{digest}"


def verify_packet(records: Sequence[EvidenceRecord],
                  passage_index=None) -> Dict[str, Any]:
    """逐條重驗：hash 自洽 +（庫可用時）回庫按座標切片對照。

    passage_index：hermes_shanghan.classics.model.PassageIndex；為 None
    時只做 hash 自洽核驗並如實標注 reverified_against_library=False。
    """
    failures: List[Dict] = []
    n_ok = 0
    for r in records:
        if r.verbatim and quote_hash(r.verbatim) != r.quote_hash:
            failures.append({"evidence_id": r.evidence_id,
                             "reason": "quote_hash_mismatch"})
            continue
        if passage_index is not None and r.passage_id:
            # work_title 是編目單元標題（Library._resolve 可匹配）——先用
            # 它定位單元，命中 by-id 緩存則零掃描；缺失時退回全庫掃描
            # （不能把「封頂掃描未命中」誤報為 passage_not_found）
            p = (passage_index.get(r.passage_id, work=r.work_title)
                 if r.work_title else None)
            if p is None:
                p = passage_index.get(
                    r.passage_id,
                    max_scan_units=len(passage_index.lib.units))
            if p is None:
                failures.append({"evidence_id": r.evidence_id,
                                 "reason": "passage_not_found"})
                continue
            sliced = p.flat_text[r.locator.char_start:r.locator.char_end]
            if sliced != r.verbatim:
                failures.append({"evidence_id": r.evidence_id,
                                 "reason": "verbatim_mismatch"})
                continue
        n_ok += 1
    return {"ok": not failures, "n_verified": n_ok,
            "n_failed": len(failures), "failures": failures,
            "reverified_against_library": passage_index is not None}


def build_packet(topic: str, records: Sequence[EvidenceRecord],
                 coverage: Optional[SearchCoverage] = None,
                 passage_index=None,
                 library_fingerprint: str = "",
                 corpus_version: str = "") -> EvidencePacket:
    recs = list(records)
    verification = verify_packet(recs, passage_index)
    return EvidencePacket(
        packet_id=packet_id_for(topic, recs),
        topic=topic,
        records=recs,
        coverage=coverage,
        verification=verification,
        library_fingerprint=library_fingerprint,
        corpus_version=corpus_version,
        note="EvidencePacket V2：逐條 verbatim+座標+quote_hash 已重驗；"
             "覆蓋範圍見 coverage；跨代理傳遞時各專家獨立收包")
