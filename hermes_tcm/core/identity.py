"""不可混淆的文獻身份層級（Protocol §5.1，P0-2）。

    Work        抽象著作          urn:tcm:work:<slug>
    Witness     一個具體傳本      urn:tcm:witness:<slug>
    Edition     一個刻本/抄本/整理本（可折疊進 Witness，單獨可尋址）
    Item        某館藏或數字化對象 urn:tcm:item:<slug>
    TextUnit    卷、篇、章、條、案 urn:tcm:unit:<slug>
    Passage     可引用最小段落     urn:tcm:passage:<psg_id>

身份解析原則（硬約束）：

1. 書名相同不等於同一著作。
2. 現代整理本不等於古代傳本。
3. 輯佚本、節本、重訂本、增補本不得自動歸併。
4. 所有自動歸併必須輸出：匹配依據、衝突字段、置信度、是否需人工裁決。
5. work_id 一經發布不得隨書名變化而變化（slug 取自 sha256 摘要，
   與展示標題解耦）。

本層不讓 LLM 決定兩個同名古籍是不是同一著作（必須避免的錯誤之三）：
歸併是確定性規則 + 人工裁決標記，never 模型判斷。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

URN_PREFIX = "urn:tcm"
RE_URN = re.compile(r"^urn:tcm:(work|witness|edition|item|unit|passage):"
                    r"([A-Za-z0-9_\-.]+)$")

IDENTITY_STATUSES = ("curated", "auto_grouped", "needs_review")

# 傳本標記詞：出現在標題後綴時提示這是特定傳本/整理形態，
# 不同標記的單元**不得**自動歸併為同一 Witness
RECENSION_MARKERS = ("宋本", "桂本", "條文版", "千金翼方版", "古本", "影印",
                    "點校", "校注", "輯佚", "節本", "重訂", "增補", "合刊")


def _slug(namespace: str, value: str) -> str:
    """跨進程穩定 slug：sha256（不用內置 hash()——帶進程隨機種子）。"""
    return hashlib.sha256(
        f"{namespace}\0{value}".encode("utf-8")).hexdigest()[:12]


def work_urn(base_title: str, disambiguator: str = "") -> str:
    key = base_title if not disambiguator else f"{base_title}#{disambiguator}"
    return f"{URN_PREFIX}:work:{_slug('work', key)}"


def witness_urn(unit_id: str) -> str:
    return f"{URN_PREFIX}:witness:{_slug('witness', unit_id)}"


def edition_urn(unit_id: str, edition_statement: str = "") -> str:
    return f"{URN_PREFIX}:edition:{_slug('edition', f'{unit_id}|{edition_statement}')}"


def item_urn(unit_id: str) -> str:
    return f"{URN_PREFIX}:item:{_slug('item', unit_id)}"


def unit_urn(unit_id: str, section: str = "") -> str:
    return f"{URN_PREFIX}:unit:{_slug('unit', f'{unit_id}|{section}')}"


def passage_urn(passage_id: str) -> str:
    """Passage URN 直接複用 classics 層的穩定 psg_ id（同一庫版本下
    永遠相同，可入論文穩定引用）。"""
    return f"{URN_PREFIX}:passage:{passage_id}"


def parse_urn(urn: str) -> Optional[Tuple[str, str]]:
    """urn:tcm:work:abc123 → ("work", "abc123")；非法 URN 返回 None。"""
    m = RE_URN.match((urn or "").strip())
    if not m:
        return None
    return m.group(1), m.group(2)


# ---------------------------------------------------------------------------
# 身份記錄
# ---------------------------------------------------------------------------
@dataclass
class WorkRecord:
    work_id: str                        # urn:tcm:work:...
    canonical_title: str
    title_aliases: List[str] = field(default_factory=list)
    attributed_authors: List[str] = field(default_factory=list)
    work_period: str = ""               # 著作時代（朝代）
    genre: str = ""                     # 分類（醫經/方書/本草/…）
    identity_status: str = "auto_grouped"   # curated|auto_grouped|needs_review
    witness_ids: List[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WitnessRecord:
    witness_id: str                     # urn:tcm:witness:...
    work_id: str
    unit_id: str                        # 底層編目單元 id（笈成目錄鍵）
    title: str                          # 傳本標題（如「傷寒論_宋本」）
    recension: str = ""                 # 傳本系統（如「宋本系」）
    edition_statement: str = ""
    edition_id: str = ""                # urn:tcm:edition:...
    publication_period: str = ""        # 刊刻/抄寫時代
    holding_institution: str = ""
    source_type: str = "transcription"  # woodblock_print|manuscript|transcription|modern_edition
    author: str = ""
    dynasty: str = ""
    category: str = ""
    item_id: str = ""                   # urn:tcm:item:...

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DigitalItem:
    item_id: str
    witness_id: str
    files: List[str] = field(default_factory=list)
    rights: str = ""
    source_sha256: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# 身份解析結果（原則 4：自動歸併必須可審計）
# ---------------------------------------------------------------------------
@dataclass
class IdentityResolution:
    """一次書名→Work 解析/歸併判定的完整可審計輸出。"""
    query: str
    resolved_work_id: str = ""
    matched_on: List[str] = field(default_factory=list)     # 匹配依據
    conflicting_fields: List[Dict] = field(default_factory=list)
    confidence: float = 0.0
    needs_human_adjudication: bool = False
    candidates: List[Dict] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def classify_source_type(title: str, dynasty: str = "") -> str:
    """標題/朝代 → source_type 的確定性啟發（如實保守：預設 transcription，
    「現代整理本不等於古代傳本」由 modern_edition 顯式分開）。"""
    t = title or ""
    if any(k in t for k in ("點校", "校注", "校釋", "白話", "今譯")):
        return "modern_edition"
    if any(k in t for k in ("抄本", "稿本", "寫本")):
        return "manuscript"
    if (dynasty or "").strip() in ("民國",) and "整理" in t:
        return "modern_edition"
    return "transcription"


def detect_recension(title: str) -> str:
    """標題後綴中的傳本標記（「傷寒論_宋本」→「宋本」）。無標記返回空。"""
    parts = (title or "").split("_")
    if len(parts) < 2:
        return ""
    suffix = parts[-1]
    for marker in RECENSION_MARKERS:
        if marker in suffix:
            return suffix
    return suffix if suffix else ""


def merge_conflicts(a: Dict[str, str], b: Dict[str, str]) -> List[Dict]:
    """兩個編目單元歸併為同一 Work 前的字段衝突檢測。

    author/dynasty 不同即為衝突（同名異書的主信號）；返回衝突清單，
    調用方據此決定 needs_human_adjudication。"""
    out: List[Dict] = []
    for f in ("author", "dynasty"):
        va, vb = (a.get(f) or "").strip(), (b.get(f) or "").strip()
        if va and vb and va != vb:
            out.append({"field": f, "a": va, "b": vb})
    return out
