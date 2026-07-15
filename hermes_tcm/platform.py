"""平台服務網關（P0-3 依賴倒置的唯一縫隙之一）。

依賴方向治理：hermes_tcm 內核對 legacy 包（hermes_shanghan）的全部
訪問收斂到**兩個**允許模塊——

    hermes_tcm/platform.py          平台層能力（本模塊）
    hermes_tcm/domains/shanghan.py  shanghan Domain Pack 接縫

其餘內核代碼一律 import 本模塊的服務函數，不得直接 import
hermes_shanghan（tests/test_dependency_inversion.py AST 級強制）。

「平台層」指與具體領域無關、歷史上落位在 hermes_shanghan 包內的
能力：classics 全庫檢索內核、編目庫（Library/catalog）、字符歸一
（異體折疊）、朝代序、版本指紋。它們屬於未來的獨立 platform 包；
在物理搬遷之前，本模塊就是它們的接口——調用方只依賴這裡的簽名，
搬遷時只改本模塊。

全部訪問惰性解析（函數內 import）：導入 hermes_tcm 不強制拉起
legacy 包的重資產。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# 用戶可讀的語料獲取提示（多處錯誤信息共用，單一定義防漂移）
LIBRARY_FETCH_HINT = ("全庫未就緒：請先運行 "
                      "`python3 -m hermes_shanghan library fetch`")


# ---------------------------------------------------------------------------
# 字符歸一 / 詞形（textutil）
# ---------------------------------------------------------------------------
def fold_variants(text: str) -> str:
    from hermes_shanghan.textutil import fold_variants as fn
    return fn(text)


def normalize_query(text: str) -> str:
    from hermes_shanghan.textutil import normalize_query as fn
    return fn(text)


def variant_map() -> Dict:
    """異體字折疊表（規範化規則指紋用；不可得時空表）。"""
    try:
        from hermes_shanghan import textutil
        return dict(getattr(textutil, "_VARIANT_MAP", {}))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 朝代序 / 著作題名（classics.model）
# ---------------------------------------------------------------------------
def dynasty_rank(dynasty: str) -> int:
    from hermes_shanghan.classics.model import dynasty_rank as fn
    return fn(dynasty)


def unranked_rank() -> int:
    from hermes_shanghan.classics.model import UNRANKED
    return UNRANKED


def work_base_title(title: str) -> str:
    from hermes_shanghan.classics.model import work_base_title as fn
    return fn(title)


# ---------------------------------------------------------------------------
# 全庫編目 / 檢索內核（corpus.library + classics）
# ---------------------------------------------------------------------------
def library():
    """hermes_shanghan.corpus.library 模塊（Library/catalog 構件）。"""
    from hermes_shanghan.corpus import library as mod
    return mod


def classics_searcher():
    """classics.PassageSearcher（庫未就緒返回 None）。"""
    from hermes_shanghan.classics.tools import _searcher
    return _searcher()


def classics_tools():
    """classics 工具實現模塊（t_search_passages / t_trace_citation /
    t_compare_witnesses / t_concept_drift / t_resolve_term /
    t_export_evidence_packet / _attach_evidence …）。"""
    from hermes_shanghan.classics import tools as mod
    return mod


def passage_evidence(passage, unit, char_start: int, char_end: int,
                     retrieval_query: str = "") -> Dict:
    """classics P 層段落證據記錄構造（verbatim+座標+quote_hash）。"""
    from hermes_shanghan.classics.evidence import passage_evidence as fn
    return fn(passage, unit, char_start, char_end,
              retrieval_query=retrieval_query)


# ---------------------------------------------------------------------------
# legacy 配置 / 指紋 / 註冊表
# ---------------------------------------------------------------------------
def legacy_data_dir() -> Path:
    from hermes_shanghan import config
    return config.DATA_DIR


def legacy_spec_versions() -> Dict:
    from hermes_shanghan.agent.harness.state import spec_versions
    return spec_versions()


def legacy_domain_plugins() -> Dict:
    """legacy 領域插件表（防漂移鉗制/合併視圖用）。"""
    from hermes_shanghan.domains import DOMAINS
    return DOMAINS
