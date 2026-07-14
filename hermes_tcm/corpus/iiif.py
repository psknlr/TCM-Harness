"""IIIF Presentation API 頁面層（Protocol §5.3）。

Manifest 描述整部數字對象，Canvas 表示某一頁/一葉，Annotation 把
轉錄/批注綁定到頁面或頁面區域（xywh fragment）。

誠實邊界（沿襲 classics.model 的聲明）：影印頁對應、頁碼/行號座標
需要底本掃描件對齊——當前庫是純轉錄文本，本模塊提供 locator 模型與
Manifest 生成器，`iiif_canvas`/`xywh` 字段在無影像對齊時如實留空，
不編造頁碼。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

IIIF_CONTEXT = "http://iiif.io/api/presentation/3/context.json"


@dataclass
class PassageLocator:
    """EvidenceRecord V2 的 locator 塊（Protocol §6.2）。

    影像對齊字段（folio/page/line/iiif_canvas/xywh）在無對齊數據時
    留空——「不知道」不偽裝成「第 47 頁」。
    """
    volume: str = ""
    section: str = ""
    folio: str = ""
    page: Optional[int] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    char_start: int = 0
    char_end: int = 0
    iiif_canvas: str = ""
    xywh: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items()
                if v not in ("", None)}


@dataclass
class Canvas:
    canvas_id: str
    label: str
    width: int = 0
    height: int = 0
    image_uri: str = ""
    annotations: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.canvas_id, "type": "Canvas",
            "label": {"none": [self.label]},
        }
        if self.width and self.height:
            d["width"], d["height"] = self.width, self.height
        if self.image_uri:
            d["items"] = [{
                "type": "AnnotationPage",
                "items": [{"type": "Annotation",
                           "motivation": "painting",
                           "body": {"id": self.image_uri, "type": "Image"},
                           "target": self.canvas_id}]}]
        if self.annotations:
            d["annotations"] = [{"type": "AnnotationPage",
                                 "items": self.annotations}]
        return d


def transcription_annotation(canvas_id: str, text: str,
                             xywh: str = "") -> Dict:
    """W3C Web Annotation 風格的轉錄批注（Body—Target）。"""
    target = canvas_id + (f"#xywh={xywh}" if xywh else "")
    return {"type": "Annotation",
            "motivation": "supplementing",
            "body": {"type": "TextualBody", "value": text,
                     "format": "text/plain"},
            "target": target}


def build_manifest(item_id: str, label: str,
                   canvases: List[Canvas]) -> Dict:
    return {
        "@context": IIIF_CONTEXT,
        "id": f"{item_id}/manifest",
        "type": "Manifest",
        "label": {"none": [label]},
        "items": [c.to_dict() for c in canvases],
    }
