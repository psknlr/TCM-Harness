"""OCFL 風格長期保存對象存儲（Protocol §5.3）。

Oxford Common File Layout 的核心性質逐項落地（純標準庫）：

* 完整性：全部內容文件 sha256 入 manifest，fixity 可重驗；
* 可解析性：inventory.json 自描述（無需本系統也能讀懂佈局）；
* 版本化：v1/v2/… 版本目錄，內容尋址去重（同內容不重存）；
* 穩健性：RAW 永不覆蓋——新版本只追加，舊版本文件不動；
* NAMASTE 標記文件聲明對象類型與版本。

用途：古籍原始文件、TEI、IIIF、索引清單與處理記錄的持久化佈局
（接入流水線 05 raw_object_freeze 階段的存儲後端）。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Dict, List, Optional

OCFL_VERSION = "1.1"
NAMASTE = f"0=ocfl_object_{OCFL_VERSION}"


class PreservationError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class OCFLObject:
    """單個保存對象：root/ 下 NAMASTE + inventory.json + vN/content/。"""

    def __init__(self, root: Path, object_id: str = ""):
        self.root = Path(root)
        namaste = self.root / NAMASTE
        inv = self.root / "inventory.json"
        if inv.exists():
            self.inventory = json.loads(inv.read_text(encoding="utf-8"))
            if object_id and self.inventory["id"] != object_id:
                raise PreservationError(
                    f"對象 id 不匹配：{self.inventory['id']} ≠ {object_id}")
        else:
            if not object_id:
                raise PreservationError("新建對象必須提供 object_id")
            self.root.mkdir(parents=True, exist_ok=True)
            namaste.write_text(f"ocfl_object_{OCFL_VERSION}\n",
                               encoding="utf-8")
            self.inventory = {
                "id": object_id,
                "type": f"https://ocfl.io/{OCFL_VERSION}/spec/#inventory",
                "digestAlgorithm": "sha256",
                "head": "v0",
                "manifest": {},          # digest → [實際存儲路徑]
                "versions": {},
            }
            self._write_inventory()

    # ------------------------------------------------------------------
    def _write_inventory(self) -> None:
        blob = json.dumps(self.inventory, ensure_ascii=False, indent=1,
                          sort_keys=True)
        (self.root / "inventory.json").write_text(blob, encoding="utf-8")
        # sidecar 校驗和（OCFL 要求）
        (self.root / "inventory.json.sha256").write_text(
            _sha256(blob.encode("utf-8")) + " inventory.json\n",
            encoding="utf-8")

    @property
    def head(self) -> str:
        return self.inventory["head"]

    def _next_version(self) -> str:
        return f"v{int(self.head[1:]) + 1}"

    # ------------------------------------------------------------------
    def add_version(self, files: Dict[str, bytes],
                    message: str = "") -> str:
        """追加新版本：files = {邏輯路徑: 內容}。內容尋址去重——已存在
        的 digest 不重複落盤；RAW 永不覆蓋（只能新增版本）。"""
        if not files:
            raise PreservationError("新版本必須包含至少一個文件")
        version = self._next_version()
        state: Dict[str, List[str]] = {}
        for logical, data in sorted(files.items()):
            if logical.startswith("/") or ".." in logical.split("/"):
                raise PreservationError(f"非法邏輯路徑：{logical}")
            digest = _sha256(data)
            if digest not in self.inventory["manifest"]:
                rel = f"{version}/content/{logical}"
                dest = self.root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                self.inventory["manifest"][digest] = [rel]
            state.setdefault(digest, []).append(logical)
        # 版本 state 繼承上一版未變動文件（全量邏輯視圖）
        prev = self.inventory["versions"].get(self.head, {}).get("state", {})
        new_logical = {p for paths in state.values() for p in paths}
        for digest, paths in prev.items():
            keep = [p for p in paths if p not in new_logical]
            if keep:
                state.setdefault(digest, []).extend(keep)
        self.inventory["versions"][version] = {
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "message": message,
            "state": {d: sorted(p) for d, p in sorted(state.items())},
        }
        self.inventory["head"] = version
        self._write_inventory()
        return version

    # ------------------------------------------------------------------
    def read(self, logical_path: str,
             version: Optional[str] = None) -> bytes:
        version = version or self.head
        v = self.inventory["versions"].get(version)
        if v is None:
            raise PreservationError(f"未知版本 {version}")
        for digest, paths in v["state"].items():
            if logical_path in paths:
                rel = self.inventory["manifest"][digest][0]
                return (self.root / rel).read_bytes()
        raise PreservationError(
            f"版本 {version} 中無邏輯文件 {logical_path}")

    def fixity_check(self) -> Dict:
        """完整性重驗：manifest 中每個 digest 對照磁盤內容。"""
        failures: List[Dict] = []
        n_ok = 0
        for digest, paths in self.inventory["manifest"].items():
            p = self.root / paths[0]
            if not p.exists():
                failures.append({"digest": digest, "path": paths[0],
                                 "reason": "missing"})
                continue
            if _sha256(p.read_bytes()) != digest:
                failures.append({"digest": digest, "path": paths[0],
                                 "reason": "digest_mismatch"})
                continue
            n_ok += 1
        return {"ok": not failures, "n_verified": n_ok,
                "n_failed": len(failures), "failures": failures}


def freeze_raw_object(store_root: Path, object_id: str,
                      files: Dict[str, bytes],
                      message: str = "raw_object_freeze") -> Dict:
    """接入流水線 05 階段入口：把一批原始文件凍結為保存對象版本。"""
    slug = hashlib.sha256(object_id.encode("utf-8")).hexdigest()[:16]
    obj = OCFLObject(Path(store_root) / slug, object_id=object_id)
    version = obj.add_version(files, message=message)
    fixity = obj.fixity_check()
    if not fixity["ok"]:
        raise PreservationError(f"凍結後 fixity 校驗失敗：{fixity}")
    return {"object_id": object_id, "object_root": str(obj.root),
            "version": version, "fixity": fixity,
            "n_files": len(files)}
