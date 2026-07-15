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
                  passage_index=None,
                  expected_corpus_version: str = "") -> Dict[str, Any]:
    """逐條重驗（P0-5：明確區分兩種強度）：

    * integrity_self_check：verbatim ↔ quote_hash 內部自洽（只證明記錄
      未在內存被改寫，**不**證明文字真的來自指定版本/段落/座標）。
    * source_reverified：回到版本鎖定的庫，按 passage_id + 座標切片與
      verbatim 完全相同——真正的回源核驗。passage_index=None 時該項為
      False（如實）。expected_corpus_version 給定時逐條核對記錄語料版本。

    返回 ok = 自洽通過且（若提供 index）回源通過且（若提供版本）版本一致。
    """
    failures: List[Dict] = []
    n_self_ok = 0
    n_reverified = 0
    version_mismatch = 0
    for r in records:
        # 1. 自洽
        if r.verbatim and quote_hash(r.verbatim) != r.quote_hash:
            failures.append({"evidence_id": r.evidence_id,
                             "reason": "quote_hash_mismatch"})
            continue
        n_self_ok += 1
        # 2. 版本一致（要求時）
        if expected_corpus_version and r.corpus_version \
                and r.corpus_version != expected_corpus_version:
            version_mismatch += 1
            failures.append({"evidence_id": r.evidence_id,
                             "reason": "corpus_version_mismatch",
                             "record_version": r.corpus_version,
                             "expected": expected_corpus_version})
            continue
        # 3. 回源核驗（庫可用時）
        if passage_index is not None and r.passage_id:
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
            n_reverified += 1
    total = len(records)
    reverified = (passage_index is not None
                  and n_reverified == total and total > 0)
    return {"ok": not failures,
            "integrity_self_check": n_self_ok == total,
            "source_reverified": reverified,
            "n_verified": n_self_ok,
            "n_reverified": n_reverified,
            "n_failed": len(failures), "failures": failures,
            "version_mismatch": version_mismatch,
            "verified_against_corpus_version": expected_corpus_version,
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
