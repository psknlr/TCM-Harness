"""笈成全庫（jicheng）語料指紋（Protocol §5.1/§5.4）。

hermes_tcm 的檢索數據源是 jicheng 全庫（config.LIBRARY_URL =
https://jicheng.tw/files/jcw/book-20180111.7z，sha256 釘定）。證據回庫
核驗與 replay 的前提是：run 記錄的 corpus_version 必須指向**答案實際
取自的那個庫版本**——即 catalog 的 archive_sha256，而非伤寒論規則庫
manifest。本模塊統一產出該指紋。

指紋組成：archive_sha256（庫內容）+ segmentation/index 版本。庫未就緒
時如實返回 no-library 標記——不偽裝成某個版本。
"""
from __future__ import annotations

import json
from typing import Optional

from hermes_shanghan import config

CORPUS_KIND = "jicheng"
SEGMENTATION_VERSION = "classics-passage-v1"
INDEX_VERSION = "charindex-v1"


def library_corpus_version(catalog_path: Optional[object] = None) -> str:
    """jicheng 庫版本標識。就緒時 = jicheng@<archive_sha256前12>；
    未就緒時 = jicheng@no-library（如實，不編造）。"""
    cat = catalog_path or (config.LIBRARY_DIR / "catalog.json")
    try:
        data = json.loads(cat.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return f"{CORPUS_KIND}@no-library"
    archive = (data.get("archive_sha256") or "").strip()
    if not archive:
        return f"{CORPUS_KIND}@unversioned"
    return f"{CORPUS_KIND}@{archive[:12]}"


def library_ready() -> bool:
    return (config.LIBRARY_DIR / "catalog.json").exists()


def corpus_manifest_summary() -> dict:
    """語料清單摘要（readyz / corpus audit 用）。"""
    cat = config.LIBRARY_DIR / "catalog.json"
    if not cat.exists():
        return {"ready": False, "corpus_version": library_corpus_version(),
                "source_url": config.LIBRARY_URL,
                "expected_archive_sha256": config.LIBRARY_SHA256}
    try:
        data = json.loads(cat.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ready": False, "corpus_version": f"{CORPUS_KIND}@corrupt"}
    return {
        "ready": True,
        "corpus_version": library_corpus_version(),
        "n_books": data.get("n_books", 0),
        "n_units": data.get("n_units", 0),
        "archive_sha256": data.get("archive_sha256", ""),
        "source_url": data.get("source_url", config.LIBRARY_URL),
        "segmentation_version": SEGMENTATION_VERSION,
        "index_version": INDEX_VERSION,
    }
