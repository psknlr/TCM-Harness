"""EvidenceRecord V2（Protocol §6.2，P0-1）。

從 P 層專用對象升級為全局通用證據對象：完整文獻身份鏈
（work/witness/edition/item/unit/passage）+ 三層文本（verbatim/
diplomatic/normalized）+ locator + 五個正交維度角色 + 質量 + 檢索
上下文 + 來源鏈。

強不變量（構造期執行，fail-fast）：

* verbatim 摘錄、座標、quote_hash 必須同時在場且互相一致；
* verification_level 聲明 V1+ 時必須可逐字重驗（hash 對得上）；
* `passage_id` 存在但未返回正文（verbatim 為空）只能是 V0 元數據
  記錄，不能算正文證據。
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

from ..core.schemas import (EPISTEMIC_STATUSES, SOURCE_ROLES,
                            VERIFICATION_LEVELS, WITNESS_ROLES,
                            category_to_source_role, legacy_layer_to_roles)
from ..core.identity import passage_urn, unit_urn, witness_urn, work_urn
from ..corpus.iiif import PassageLocator


def quote_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def evidence_id_for(passage_id: str, char_start: int, char_end: int,
                    qhash: str) -> str:
    digest = hashlib.sha256(
        f"{passage_id}|{char_start}|{char_end}|{qhash}".encode("utf-8")
    ).hexdigest()[:12]
    return f"ev_{digest}"


@dataclass
class EvidenceRecord:
    """全局通用證據記錄。字段組織與 Protocol §6.2 JSON 一一對應。"""
    evidence_id: str
    corpus_version: str
    # —— 身份鏈 ——
    work_id: str = ""
    witness_id: str = ""
    edition_id: str = ""
    item_id: str = ""
    text_unit_id: str = ""
    passage_id: str = ""
    # —— 定位 ——
    locator: PassageLocator = field(default_factory=PassageLocator)
    # —— 文本 ——
    verbatim: str = ""
    diplomatic: str = ""
    normalized: str = ""
    quote_hash: str = ""
    passage_hash: str = ""
    source_asset_sha256: str = ""
    normalization_map_id: str = ""
    # —— 正交維度角色 ——
    source_role: str = "compilation"
    witness_role: str = "base_witness"
    epistemic_status: str = "verbatim"
    verification_level: str = "V0"
    # —— 質量 ——
    transcription_method: str = "transcription"
    ocr_confidence: Optional[float] = None
    metadata_confidence: Optional[float] = None
    identity_confidence: Optional[float] = None
    # —— 檢索上下文 ——
    retrieval_query: str = ""
    query_variants: tuple = ()
    retrieval_rank: int = 0
    tool_call_id: str = ""
    span_id: str = ""
    coverage_id: str = ""
    # —— 來源 ——
    ingest_activity_id: str = ""
    extractor_version: str = ""
    registered_by: str = ""      # 只有 capability_broker 可入台賬
    # —— 展示 ——
    work_title: str = ""
    author: str = ""
    dynasty: str = ""
    category: str = ""
    section: str = ""

    def __post_init__(self):
        if self.source_role not in SOURCE_ROLES:
            raise ValueError(f"非法 source_role {self.source_role!r}")
        if self.witness_role not in WITNESS_ROLES:
            raise ValueError(f"非法 witness_role {self.witness_role!r}")
        if self.epistemic_status not in EPISTEMIC_STATUSES:
            raise ValueError(f"非法 epistemic_status {self.epistemic_status!r}")
        if self.verification_level not in VERIFICATION_LEVELS:
            raise ValueError(
                f"非法 verification_level {self.verification_level!r}")
        # 強不變量：V1+ 必須有可重驗的逐字摘錄
        if self.verification_level != "V0":
            if not self.verbatim:
                raise ValueError(
                    "證據完整性違例：verification_level≥V1 但 verbatim 為空"
                    "——passage_id 存在而正文未返回只能是 V0 元數據記錄")
            if quote_hash(self.verbatim) != self.quote_hash:
                raise ValueError(
                    "證據完整性違例：verbatim 與 quote_hash 不一致"
                    "（摘錄、hash 必須同時核驗，不能只信其一）")

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["locator"] = self.locator.to_dict()
        d["query_variants"] = list(self.query_variants)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EvidenceRecord":
        d = dict(d)
        loc = d.pop("locator", {}) or {}
        d["locator"] = PassageLocator(**{k: v for k, v in loc.items()
                                         if k in PassageLocator.__dataclass_fields__})
        d["query_variants"] = tuple(d.get("query_variants") or ())
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})

    @property
    def is_primary_text_returned(self) -> bool:
        """正文確實返回（模型能讀到原文）——發布允許集的判定依據。"""
        return bool(self.verbatim) and self.verification_level != "V0"

    def legacy_view(self) -> str:
        """A/B/C/D/E/P 兼容視圖。"""
        from ..core.schemas import roles_to_legacy_layer
        return roles_to_legacy_layer(self.source_role, self.witness_role,
                                     self.epistemic_status)


# ---------------------------------------------------------------------------
# 兼容構造：舊 P 層記錄 → EvidenceRecord V2
# ---------------------------------------------------------------------------
def from_legacy_p_record(rec: Dict[str, Any], corpus_version: str = "",
                         work_registry=None) -> EvidenceRecord:
    """classics.evidence.passage_evidence 的 dict → EvidenceRecord V2。

    verbatim/座標/quote_hash 逐項搬運並在構造期重驗；身份鏈由
    WorkRegistry 解析（不可得時以 unit_id 派生確定性 URN，如實標注
    identity_confidence 較低）。"""
    unit_id = rec.get("work_id", "")        # 舊 P 層的 work_id 是編目單元 id
    verbatim = rec.get("verbatim_text", "") or ""
    qhash = rec.get("quote_hash", "") or quote_hash(verbatim)
    # 顯式提供的 hash 與摘錄不符=篡改，拒絕而非靜默降級（fail-closed）
    if verbatim and qhash != quote_hash(verbatim):
        raise ValueError("legacy 記錄完整性違例：quote_hash 與 "
                         "verbatim_text 不一致（疑似篡改，拒絕轉換）")
    witness = work_registry.witness_for_unit(unit_id) if work_registry else None
    work = work_registry.work_for_unit(unit_id) if work_registry else None
    level = "V1" if verbatim else "V0"
    if level == "V1" and witness is not None:
        level = "V2"        # 身份鏈完整 → 歸屬核驗達成
    return EvidenceRecord(
        evidence_id=evidence_id_for(rec.get("passage_id", ""),
                                    rec.get("char_start", 0),
                                    rec.get("char_end", 0), qhash),
        corpus_version=corpus_version,
        work_id=(work.work_id if work else work_urn(rec.get("work_title", "")
                                                    or unit_id)),
        witness_id=(witness.witness_id if witness else witness_urn(unit_id)),
        edition_id=(witness.edition_id if witness else ""),
        item_id=(witness.item_id if witness else ""),
        text_unit_id=unit_urn(unit_id, rec.get("section", "")),
        passage_id=rec.get("passage_id", ""),
        locator=PassageLocator(section=rec.get("section", ""),
                               char_start=rec.get("char_start", 0),
                               char_end=rec.get("char_end", 0)),
        verbatim=verbatim,
        normalized="",       # 折疊視圖按需派生（1:1 映射）
        quote_hash=qhash,
        source_role=category_to_source_role(rec.get("category", "")),
        witness_role=("modern_edition"
                      if witness and witness.source_type == "modern_edition"
                      else "base_witness"),
        epistemic_status="verbatim" if verbatim else "source_assertion",
        verification_level=level,
        identity_confidence=(0.9 if witness else 0.5),
        retrieval_query=rec.get("retrieval_query", ""),
        retrieval_rank=rec.get("retrieval_rank", 0),
        work_title=rec.get("work_title", ""),
        author=rec.get("author", ""),
        dynasty=rec.get("dynasty", ""),
        category=rec.get("category", ""),
        section=rec.get("section", ""))


def from_legacy_layer(layer: str, **kwargs) -> Dict[str, str]:
    """A/B/C/D/E → 正交角色 dict（構造 EvidenceRecord 的快捷路徑）。"""
    return legacy_layer_to_roles(layer)
