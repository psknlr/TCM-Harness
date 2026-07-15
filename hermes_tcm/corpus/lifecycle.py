"""語料接入流水線與 Corpus Manifest V2（Protocol §5.4）。

15 階段接入流水線的狀態機 + 版本凍結清單。每次發布新的 corpus
version 必須凍結 catalog_hash / raw_assets_hash / 規範化規則 /
切分版本 / 索引版本——replay 對比的前提是凍結並記錄環境。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

INGEST_STAGES = (
    "source_register",
    "license_and_rights_check",
    "checksum_and_archive_validation",
    "encoding_detection",
    "raw_object_freeze",
    "structural_parse",
    "diplomatic_transcription",
    "normalization_with_mapping",
    "work_witness_resolution",
    "volume_section_passage_segmentation",
    "tei_and_iiif_generation",
    "index_build",
    "sampling_qa",
    "corpus_manifest_publish",
    "readyz",
)

STAGE_ORDER = {s: i for i, s in enumerate(INGEST_STAGES)}


@dataclass
class IngestRun:
    """一次語料接入的階段狀態（嚴格順序推進，跳階即錯）。"""
    source_id: str
    stages: Dict[str, Dict] = field(default_factory=dict)

    def advance(self, stage: str, detail: Optional[Dict] = None) -> None:
        if stage not in STAGE_ORDER:
            raise ValueError(f"未知接入階段 {stage!r}")
        idx = STAGE_ORDER[stage]
        done = [s for s in self.stages if self.stages[s].get("ok")]
        expected = [s for s in INGEST_STAGES[:idx]]
        missing = [s for s in expected if s not in done]
        if missing:
            raise ValueError(f"階段 {stage} 之前有未完成階段：{missing}"
                             "（接入流水線不允許跳階）")
        self.stages[stage] = {"ok": True,
                              "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                              **(detail or {})}

    @property
    def current_stage(self) -> str:
        done = [s for s in INGEST_STAGES if self.stages.get(s, {}).get("ok")]
        return done[-1] if done else ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CorpusManifestV2:
    """語料版本凍結清單（Protocol §5.4）。"""
    corpus_version: str
    catalog_hash: str = ""
    raw_assets_hash: str = ""
    tei_hash: str = ""
    normalization_rules_hash: str = ""
    segmentation_version: str = ""
    index_version: str = ""
    published_at: str = ""
    n_works: int = 0
    n_witnesses: int = 0
    known_gaps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def fingerprint(self) -> str:
        blob = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def build_manifest_v2(library_root: Path, corpus_version: str,
                      registry_stats: Optional[Dict] = None,
                      known_gaps: Optional[List[str]] = None
                      ) -> CorpusManifestV2:
    """從一個就緒的庫目錄構建凍結清單。catalog.json 不存在時如實報錯
    （fail-closed：沒有編目就沒有可凍結的版本）。"""
    catalog_path = Path(library_root) / "catalog.json"
    if not catalog_path.exists():
        raise FileNotFoundError(f"catalog.json 不存在：{catalog_path}"
                                "——請先完成 index_build 階段")
    raw = catalog_path.read_bytes()
    cat = json.loads(raw.decode("utf-8"))
    stats = registry_stats or {}
    return CorpusManifestV2(
        corpus_version=corpus_version,
        catalog_hash=_sha256_bytes(raw),
        raw_assets_hash=cat.get("archive_sha256", ""),
        normalization_rules_hash=_normalization_rules_hash(),
        segmentation_version="classics-passage-v1",
        index_version="charindex-v1",
        published_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        n_works=stats.get("n_works", 0),
        n_witnesses=stats.get("n_witnesses", cat.get("n_units", 0)),
        known_gaps=list(known_gaps or []))


def _normalization_rules_hash() -> str:
    """規範化規則指紋 = 異體字折疊表內容哈希（規則變更即指紋變）。"""
    try:
        from ..platform import variant_map
        table = variant_map()
        blob = json.dumps(sorted((str(k), str(v)) for k, v in table.items()),
                          ensure_ascii=False)
        return _sha256_bytes(blob.encode("utf-8"))
    except Exception:
        return ""
