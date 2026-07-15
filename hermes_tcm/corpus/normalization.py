"""三層文本模型：RAW—DIPLOMATIC—NORMALIZED（Protocol §5.2）。

    RAW         原始文件（永不覆蓋；由 OCFL 風格對象存儲持有）
    DIPLOMATIC  外交轉錄層：保持原字、異體、缺字、版式
    NORMALIZED  規範化視圖：異體折疊等（檢索用）

必須維護 normalized character ↔ diplomatic character 的座標映射，
否則規範化後雖然更好檢索，卻無法保證引用仍可回到原始版本。

現有 fold_variants 是 **1:1 字符映射**（str.translate 單字符替換），
故 NormalizationMap 在此實現下是恆等座標映射——本模塊把這一事實
顯式建模並自動驗證，未來引入非 1:1 規則（繁簡合併/缺字補全）時
map_offset 的契約不變、實現換成區間映射表。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from ..platform import fold_variants

NORMALIZATION_MAP_VERSION = "identity-1to1-v1"


@dataclass
class NormalizationMap:
    """diplomatic → normalized 的座標映射（雙向）。

    當前折疊規則是 1:1 映射：normalized[i] 恆對應 diplomatic[i]。
    構造時驗證此不變量——規則悄悄變成非 1:1 時 fail-fast 而不是
    靜默給出錯座標。
    """
    map_id: str
    diplomatic: str
    normalized: str
    version: str = NORMALIZATION_MAP_VERSION
    # 非 1:1 區段（當前實現恆為空；字段保留給未來區間映射）
    irregular_spans: List[Tuple[int, int, int, int]] = field(
        default_factory=list)

    def __post_init__(self):
        if len(self.diplomatic) != len(self.normalized):
            raise ValueError(
                "規範化映射違例：diplomatic 與 normalized 長度不一致"
                f"（{len(self.diplomatic)} ≠ {len(self.normalized)}）——"
                "當前 NormalizationMap 僅支持 1:1 折疊")

    def to_diplomatic_offset(self, normalized_offset: int) -> int:
        return normalized_offset

    def to_normalized_offset(self, diplomatic_offset: int) -> int:
        return diplomatic_offset

    def slice_diplomatic(self, norm_start: int, norm_end: int) -> str:
        return self.diplomatic[self.to_diplomatic_offset(norm_start):
                               self.to_diplomatic_offset(norm_end)]


def normalization_map_id(diplomatic: str) -> str:
    digest = hashlib.sha256(
        f"{NORMALIZATION_MAP_VERSION}\0{diplomatic}".encode("utf-8")
    ).hexdigest()[:12]
    return f"normmap_{digest}"


def build_map(diplomatic: str) -> NormalizationMap:
    return NormalizationMap(map_id=normalization_map_id(diplomatic),
                            diplomatic=diplomatic,
                            normalized=fold_variants(diplomatic))


def three_layer_view(diplomatic: str) -> Dict[str, str]:
    """一段外交轉錄文本的三層視圖（RAW 由對象存儲持有，此處給出指紋）。"""
    m = build_map(diplomatic)
    return {
        "raw_sha256": hashlib.sha256(
            diplomatic.encode("utf-8")).hexdigest()[:16],
        "diplomatic": m.diplomatic,
        "normalized": m.normalized,
        "normalization_map_id": m.map_id,
        "normalization_version": m.version,
    }
