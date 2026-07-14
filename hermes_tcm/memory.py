"""三類記憶分離（Protocol §13.2）。

    Verified Knowledge Memory   只有 V2/V3 核驗事實可進入
    User Correction Memory      用戶指定底本/消歧/偏好/專家修訂
    Run Notes                   只服務當前任務，有 TTL，不成為永久知識

模型生成的古籍總結、方解、病機解釋和「某醫家認為」**不得**自動寫入
永久記憶——寫入口逐條檢查 epistemic_status 與 verification_level。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core.schemas import verification_at_least

MEMORY_KINDS = ("verified_knowledge", "user_correction", "run_notes")

# 模型生成類 epistemic_status：永久記憶寫入口一律拒絕
_MODEL_GENERATED = ("model_hypothesis", "synthesis")


class MemoryWriteRejected(RuntimeError):
    pass


class TCMMemory:
    """單文件 JSONL 存儲（開發版）；三類記憶物理分檔。"""

    RUN_NOTES_TTL_S = 24 * 3600

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, kind: str) -> Path:
        return self.root / f"{kind}.jsonl"

    # ------------------------------------------------------------------
    def write(self, kind: str, entry: Dict[str, Any]) -> Dict:
        if kind not in MEMORY_KINDS:
            raise MemoryWriteRejected(f"未知記憶類型 {kind!r}")
        entry = dict(entry)
        entry["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        if kind == "verified_knowledge":
            status = entry.get("epistemic_status", "")
            level = entry.get("verification_level", "V0")
            if status in _MODEL_GENERATED:
                raise MemoryWriteRejected(
                    "模型生成內容不得自動寫入永久記憶"
                    f"（epistemic_status={status}）")
            if not verification_at_least(level, "V2"):
                raise MemoryWriteRejected(
                    f"永久知識要求 V2+ 核驗（當前 {level}）")
            if not entry.get("evidence_ids"):
                raise MemoryWriteRejected("永久知識必須綁定 evidence_ids")
        if kind == "user_correction":
            entry.setdefault("trust", "unverified_user_correction")
        if kind == "run_notes":
            entry.setdefault("ttl_s", self.RUN_NOTES_TTL_S)
            entry["expires_at"] = time.time() + entry["ttl_s"]
        with self._path(kind).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def read(self, kind: str) -> List[Dict]:
        if kind not in MEMORY_KINDS:
            raise ValueError(f"未知記憶類型 {kind!r}")
        p = self._path(kind)
        if not p.exists():
            return []
        out: List[Dict] = []
        now = time.time()
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if kind == "run_notes" and d.get("expires_at", 0) < now:
                continue        # TTL 過期不返回
            out.append(d)
        return out
