"""annotation.*：批注工具（Protocol §5.3 W3C Web Annotation + §14.4）。

Annotation = Body—Target：把專家意見/實體標注/異文判斷連接到具體
段落。寫入級別遵循 §14.4：新建**私人**批注 = auto_or_prompt
（contract.approval="prompt"，Broker 要求 approved_operations 先批）；
修改公共元數據等更高級別操作不在本工具面提供。

存儲：JSONL（路徑經 HERMES_TCM_ANNOTATIONS 覆蓋，默認
data/tcm_annotations/，不入庫）。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from .contracts import EvidenceContract, ToolContractV2
from ._shared import searcher, unavailable

ANNOTATION_MOTIVATIONS = ("commenting", "tagging", "identifying",
                          "editing", "questioning")


def _store_path() -> Path:
    override = os.environ.get("HERMES_TCM_ANNOTATIONS", "")
    if override:
        root = Path(override)
    else:
        from ..platform import legacy_data_dir
        root = legacy_data_dir() / "tcm_annotations"
    root.mkdir(parents=True, exist_ok=True)
    return root / "annotations.jsonl"


def t_create_private(target_passage_id: str, body: str,
                     motivation: str = "commenting",
                     creator: str = "", char_start: int = 0,
                     char_end: int = 0) -> Dict:
    """新建私人批注（W3C Web Annotation Body—Target）。

    target 必須是庫中真實段落（fail-closed：不存在的段落不可批注）；
    批注屬私人層，不進入公共元數據，不影響證據台賬。"""
    if motivation not in ANNOTATION_MOTIVATIONS:
        return {"error": f"非法 motivation {motivation!r}"
                         f"（可用：{ANNOTATION_MOTIVATIONS}）"}
    if not (body or "").strip():
        return {"error": "批注內容不得為空"}
    s = searcher()
    if s is None:
        return unavailable("annotation.create_private")
    p = s.index.get(target_passage_id)
    if p is None:
        return {"error": f"未找到批注目標段落 {target_passage_id}"
                         "（不存在的段落不可批注）"}
    target = f"tcm://passages/{target_passage_id}"
    if char_end > char_start >= 0:
        hi = min(char_end, len(p.flat_text))
        target += f"#char={char_start},{hi}"
    ann_id = "ann_" + hashlib.sha256(
        f"{target}\0{body}\0{creator}".encode("utf-8")).hexdigest()[:12]
    record = {
        "@context": "http://www.w3.org/ns/anno.jsonld",
        "id": ann_id,
        "type": "Annotation",
        "motivation": motivation,
        "body": {"type": "TextualBody", "value": body,
                 "format": "text/plain"},
        "target": target,
        "creator": creator or "anonymous",
        "visibility": "private",
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    path = _store_path()
    # 幂等：同 id 已存在不重寫
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                if json.loads(line).get("id") == ann_id:
                    return {"tool": "annotation.create_private",
                            "available": True, "annotation": record,
                            "deduplicated": True}
            except json.JSONDecodeError:
                continue
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"tool": "annotation.create_private", "available": True,
            "annotation": record,
            "note": "私人批注：不進入公共元數據/證據台賬；發布公共校勘"
                    "結論屬 expert_approval 級別（本工具面不提供）"}


def t_list_private(target_passage_id: str = "",
                   creator: str = "", limit: int = 20) -> Dict:
    path = _store_path()
    out: List[Dict] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                a = json.loads(line)
            except json.JSONDecodeError:
                continue
            if target_passage_id and \
                    target_passage_id not in a.get("target", ""):
                continue
            if creator and a.get("creator") != creator:
                continue
            out.append(a)
    return {"tool": "annotation.list_private", "available": True,
            "n_annotations": len(out),
            "annotations": out[-max(1, min(limit, 100)):]}


def register(reg) -> None:
    meta_ec = EvidenceContract(returns_primary_text=False,
                               evidence_role="metadata_only",
                               minimum_locator=["passage_id"])
    reg.add(ToolContractV2(
        name="annotation.create_private",
        description="新建私人批注（W3C Web Annotation Body—Target，"
                    "綁定真實段落/字符區間）。寫操作：需先獲 prompt 級"
                    "審批（默認只讀原則）。",
        input_schema={"type": "object", "properties": {
            "target_passage_id": {"type": "string"},
            "body": {"type": "string"},
            "motivation": {"type": "string",
                           "enum": list(ANNOTATION_MOTIVATIONS)},
            "creator": {"type": "string"},
            "char_start": {"type": "integer", "default": 0},
            "char_end": {"type": "integer", "default": 0}},
            "required": ["target_passage_id", "body"]},
        func=t_create_private,
        use_when=["研究者記錄段落級私人意見/標注"],
        do_not_use_when=["發布公共校勘結論（expert_approval 級別）"],
        side_effect="annotate",
        approval="prompt",
        evidence_contract=meta_ec,
        failure_modes=["corpus_unavailable", "passage_not_found",
                       "approval_required"]))
    reg.add(ToolContractV2(
        name="annotation.list_private",
        description="列出私人批注（按段落/創建者過濾）。",
        input_schema={"type": "object", "properties": {
            "target_passage_id": {"type": "string"},
            "creator": {"type": "string"},
            "limit": {"type": "integer", "default": 20}},
            "required": []},
        func=t_list_private,
        use_when=["查閱已有私人批注"],
        evidence_contract=meta_ec,
        failure_modes=[]))
