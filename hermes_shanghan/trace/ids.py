"""統一知識標識體系（WorkID / EditionID / FormulaID / HerbID / MethodID /
SyndromeID / SchoolID / ClaimID / CitationEdgeID）。

把既有的字符串鍵（書目錄名、方名、治法名…）映射到穩定 ID 命名空間，
使條文、方劑、證候、治法、注家、學派、觀點與引文邊可以連成同一張
可計算的知識網絡。ID 生成全確定（排序後編號 / 拼音 slug），與既有
`SHL_SONGBEN_xxxx` 條文 ID 兼容並存。

朝代排序鍵用於時間切片與主路徑分析；vendor 元數據缺失朝代的書目
以標準書志學常識補注（DYNASTY_OVERRIDES，均為編輯性元數據，逐條
註明依據，不影響 A 層證據）。
"""
from __future__ import annotations

from typing import Dict, List

from .. import config
from ..schemas import read_jsonl
from ..skills.pinyin import formula_slug

# ---------------------------------------------------------------------------
# 朝代時間軸（用於時間切片；數值僅表先後序，非年代）
# ---------------------------------------------------------------------------
DYNASTY_ORDER = {
    "漢": 0, "東漢": 0, "晉": 5, "南北朝": 7, "隋": 8, "唐": 10, "五代": 15,
    "宋": 20, "遼": 22, "金": 25, "元": 30, "明": 40, "清": 50, "日本": 55,
    "民國": 60, "中華民國": 60, "中華人民共和國": 70,
}
UNKNOWN_DYNASTY_ORDER = 99

# vendor 元數據朝代缺失/欠準的書目補注（編輯性元數據；依據通行書志）
DYNASTY_OVERRIDES = {
    # 丹波元簡（1755-1810）《傷寒論輯義》：日本江戶考證學派
    "傷寒論輯義": "日本",
    # 丹波元胤：日本江戶（承其父丹波元簡之學）
    "中寒論辯證廣注": "日本",
    # 鄭欽安（1824-1911）《傷寒恆論》：清
    "傷寒恆論": "清",
    # 程門雪（1902-1972）《傷寒辨要箋記》：民國—現代
    "傷寒辨要箋記": "民國",
    # 曹穎甫（1866-1938）：著作刊行於民國（vendor 標「清」以生年計）
    "經方實驗錄": "民國",
    "曹氏傷寒金匱發微合刊": "民國",
}


def dynasty_of(book: Dict) -> str:
    return DYNASTY_OVERRIDES.get(book.get("book_dir", ""),
                                 book.get("dynasty", "") or "")


def dynasty_order(dynasty: str) -> int:
    return DYNASTY_ORDER.get(dynasty, UNKNOWN_DYNASTY_ORDER)


# ---------------------------------------------------------------------------
# 註冊表構建
# ---------------------------------------------------------------------------
def build_registry() -> Dict:
    """從語料 manifest 與規則庫構建統一 ID 註冊表（全確定）。"""
    from ..corpus import downloader

    manifest = downloader.load_manifest()
    books = sorted(manifest.get("books", []),
                   key=lambda b: (b.get("category", ""), b.get("book_dir", "")))

    # WorkID：語料中每部著作
    works: List[Dict] = []
    work_of_dir: Dict[str, str] = {}
    for i, b in enumerate(books, 1):
        wid = f"WORK_{i:03d}"
        bdir = b.get("book_dir", "")
        work_of_dir[bdir] = wid
        works.append({
            "work_id": wid,
            "book_dir": bdir,
            "title": b.get("title", bdir),
            "author": b.get("author", ""),
            "dynasty": dynasty_of(b),
            "dynasty_order": dynasty_order(dynasty_of(b)),
            "dynasty_overridden": bdir in DYNASTY_OVERRIDES,
            "layer": b.get("hermes_layer", ""),
            "category": b.get("category", ""),
        })

    # EditionID：傷寒論三個版本底本
    editions = [
        {"edition_id": "ED_SONGBEN", "work_id": "WORK_SHL",
         "book_dir": config.PRIMARY_BOOK, "label": "宋本（趙開美本，條文版編號）"},
        {"edition_id": "ED_SONGBEN_FULL", "work_id": "WORK_SHL",
         "book_dir": config.SONGBEN_FULL_BOOK, "label": "宋本（含輔助篇章）"},
    ]
    for i, vb in enumerate(config.VARIANT_BOOKS, 1):
        editions.append({"edition_id": f"ED_VARIANT_{i}", "work_id": "WORK_SHL",
                         "book_dir": vb, "label": vb})

    # FormulaID：方證規則中的 113 方
    formulas: List[Dict] = []
    for r in sorted(read_jsonl(config.RULES_FORMULA_DIR / "formula_pattern_rules.jsonl"),
                    key=lambda r: r.get("formula", "")):
        name = r.get("formula", "")
        if name:
            formulas.append({"formula_id": "F_" + formula_slug(name).upper(),
                             "name": name,
                             "rule_id": r.get("formula_pattern_rule_id", "")})

    # MethodID：治法規則
    methods: List[Dict] = []
    for r in sorted(read_jsonl(config.RULES_THERAPY_DIR / "therapy_rules.jsonl"),
                    key=lambda r: r.get("therapy_rule_id", "")):
        methods.append({"method_id": "M_" + r.get("therapy_rule_id", ""),
                        "name": r.get("therapy_method", ""),
                        "polarity": r.get("polarity", "")})

    # SyndromeID：六經病證
    syndromes = [{"syndrome_id": "S_" + config.CHANNEL_PINYIN[ch].upper(), "name": ch}
                 for ch in config.SIX_CHANNELS + config.EXTRA_CHANNELS]

    return {
        "note": "統一知識標識註冊表：ID 全確定（排序編號/拼音 slug）；"
                "clause_id 沿用 SHL_SONGBEN_xxxx；SchoolID/ClaimID 見 "
                "schools.json / claims.json；CitationEdgeID 見引文邊資產。",
        "work_shl": {"work_id": "WORK_SHL", "title": "傷寒論", "author": "張仲景",
                     "dynasty": "東漢", "dynasty_order": 0},
        "works": works,
        "work_of_dir": work_of_dir,
        "editions": editions,
        "formulas": formulas,
        "methods": methods,
        "syndromes": syndromes,
        "counts": {"works": len(works), "editions": len(editions),
                   "formulas": len(formulas), "methods": len(methods),
                   "syndromes": len(syndromes)},
    }
