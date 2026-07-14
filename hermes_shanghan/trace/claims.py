"""結構化方證觀點（ClaimID）：觀點演化的可計算建模。

一個「方證觀點」（如「桂枝湯證的病機為營衛不和」）不是條文本身，而是
歷代解釋者在條文之上歸納出的命題。本模塊把這類命題建成結構化對象，
核心設計原則與全庫協議一致：**原文直述與後世歸納必須分層**——

- 種子表只提供命題文本與「解釋性術語」，不預設證據等級；
- 證據等級由機器逐字檢驗得出：術語逐字見於 A 層條文 → 「原文直述成分」；
  僅見於 C 層注文 → 「後世歸納」；兩者皆有 → 「原文相關+後世發揮」；
- 注家採用時間線按朝代排序，「何人首倡、何人沿用」由 C 層對齊數據浮現；
- 學派立場來自學派註冊表 + 分歧圖譜，不做對錯裁決，多觀點並存。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .. import config
from ..schemas import read_jsonl
from ..textutil import fold_variants
from .ids import dynasty_order

# ---------------------------------------------------------------------------
# 方證觀點種子（命題 + 解釋性術語 + 已知爭議；證據等級一律由數據判定）
# ---------------------------------------------------------------------------
CLAIM_SEEDS: List[Dict] = [
    {
        "claim_id": "CLAIM_GZT_YINGWEI",
        "formula": "桂枝湯",
        "claim": "桂枝湯證的核心病機為營衛不和，桂枝湯功在調和營衛",
        "interpretive_terms": ["營衛不和", "調和營衛", "榮衛不和", "衛強營弱",
                               "營弱衛強", "榮氣和", "衛氣不和", "營衛"],
        "controversies": ["營衛不和是否僅限太陽中風，抑或可擴展至雜病自汗",
                          "「調和營衛」為後世方義歸納，與原文「解肌」表述的關係"],
    },
    {
        "claim_id": "CLAIM_XCHT_SHUJI",
        "formula": "小柴胡湯",
        "claim": "小柴胡湯證屬少陽樞機不利，治以和解少陽",
        "interpretive_terms": ["樞機", "和解", "半表半裏", "半在裏半在外", "樞"],
        "controversies": ["「半表半裏」與原文「半在裏半在外」的異同",
                          "但見一證便是的適用邊界"],
    },
    {
        "claim_id": "CLAIM_DCQT_FUSHI",
        "formula": "大承氣湯",
        "claim": "大承氣湯證為陽明腑實，治以峻下燥屎",
        "interpretive_terms": ["腑實", "燥屎", "胃家實", "痞滿燥實"],
        "controversies": ["「痞滿燥實堅」五字訣為後世歸納，原文無此並稱"],
    },
    {
        "claim_id": "CLAIM_BXXXT_HANRE",
        "formula": "半夏瀉心湯",
        "claim": "半夏瀉心湯證為寒熱錯雜之痞，治以辛開苦降",
        "interpretive_terms": ["寒熱錯雜", "辛開苦降", "痞", "中焦"],
        "controversies": ["「辛開苦降」為金元以降藥性理論歸納"],
    },
    {
        "claim_id": "CLAIM_SNT_HUIYANG",
        "formula": "四逆湯",
        "claim": "四逆湯證為少陰陽衰陰盛，治以回陽救逆",
        "interpretive_terms": ["回陽", "救逆", "亡陽", "陽衰", "陰盛"],
        "controversies": ["「回陽救逆」治法名為後世治法學歸納"],
    },
    {
        "claim_id": "CLAIM_WLS_XUSHUI",
        "formula": "五苓散",
        "claim": "五苓散證為太陽蓄水、膀胱氣化不利",
        "interpretive_terms": ["蓄水", "氣化", "水逆", "膀胱"],
        "controversies": ["「太陽蓄水」為後世六經氣化框架下的歸納",
                          "五苓散主治重心在表裏雙解抑或利水"],
    },
    {
        "claim_id": "CLAIM_BHT_JINGZHENG",
        "formula": "白虎湯",
        "claim": "白虎湯證為陽明經證（表裏俱熱），治以辛寒清氣",
        "interpretive_terms": ["經證", "表裏俱熱", "清氣", "氣分"],
        "controversies": ["陽明「經證/腑證」二分為後世歸納，原文無此對稱",
                          "白虎湯禁例（表不解者不可與）的邊界"],
    },
]


def _grade(term_hits_a: Dict, term_hits_c: Dict) -> str:
    if term_hits_a and term_hits_c:
        return "原文直述成分 + 後世發揮"
    if term_hits_a:
        return "原文直述成分"
    if term_hits_c:
        return "後世歸納"
    return "待考（庫內無逐字術語證據）"


def build_claims(commentary_books_meta: Optional[Dict[str, Dict]] = None,
                 commentator_school: Optional[Dict[str, str]] = None) -> Dict:
    """構建結構化方證觀點庫。全部證據字段由逐字檢驗得出。"""
    clauses = {c["clause_id"]: c for c in read_jsonl(config.CLAUSE_DIR / "clauses.jsonl")}
    formula_rules = read_jsonl(config.RULES_FORMULA_DIR / "formula_pattern_rules.jsonl")
    commentary_rules = read_jsonl(config.RULES_COMMENTARY_DIR / "commentary_rules.jsonl")
    rules_by_formula = {r.get("formula", ""): r for r in formula_rules}
    commentary_books_meta = commentary_books_meta or {}
    commentator_school = commentator_school or {}

    claims = []
    for seed in CLAIM_SEEDS:
        rule = rules_by_formula.get(seed["formula"])
        supporting = list(rule.get("supporting_clauses", [])) if rule else []
        support_set = set(supporting)

        # A 層逐字檢驗：解釋性術語是否見於該方相關條文原文
        term_hits_a: Dict[str, List[str]] = {}
        for cid in sorted(support_set):
            c = clauses.get(cid)
            if not c:
                continue
            folded = fold_variants(c.get("clean_text", ""))
            for term in seed["interpretive_terms"]:
                if fold_variants(term) in folded:
                    term_hits_a.setdefault(term, []).append(cid)

        # C 層檢驗：哪些注家在該方條文的注文中使用了這些術語（按朝代排時間線）
        term_hits_c: Dict[str, List[Dict]] = {}
        timeline: Dict[str, Dict] = {}
        for r in commentary_rules:
            if r.get("clause_id") not in support_set:
                continue
            folded = fold_variants(r.get("commentary_text", ""))
            hit_terms = [t for t in seed["interpretive_terms"]
                         if fold_variants(t) in folded]
            if not hit_terms:
                continue
            commentator = r.get("commentator", "")
            book = r.get("book", "")
            meta = commentary_books_meta.get(book, {})
            dyn = meta.get("dynasty", "")
            key = commentator
            entry = timeline.setdefault(key, {
                "commentator": commentator, "book": book,
                "dynasty": dyn, "dynasty_order": dynasty_order(dyn),
                "school_id": commentator_school.get(commentator, ""),
                "n_passages": 0, "terms_used": [], "clause_ids": [],
                "sample": r.get("commentary_text", "")[:60],
            })
            entry["n_passages"] += 1
            for t in hit_terms:
                if t not in entry["terms_used"]:
                    entry["terms_used"].append(t)
                term_hits_c.setdefault(t, []).append(
                    {"commentator": commentator, "clause_id": r.get("clause_id", "")})
            if r.get("clause_id") not in entry["clause_ids"]:
                entry["clause_ids"].append(r.get("clause_id", ""))
        chronology = sorted(timeline.values(),
                            key=lambda e: (e["dynasty_order"], e["commentator"]))

        # 觀點譜系（A5）：最早可見注家與各術語首現（以在庫注本為限，如實聲明）
        first_proponent = ({k: chronology[0][k] for k in
                            ("commentator", "book", "dynasty", "school_id")}
                           if chronology else {})
        term_first_use = {}
        for entry in chronology:
            for t in entry["terms_used"]:
                if t not in term_first_use:
                    term_first_use[t] = {"commentator": entry["commentator"],
                                         "dynasty": entry["dynasty"]}
        term_first_use = {t: term_first_use[t] for t in sorted(term_first_use)}

        claims.append({
            "claim_id": seed["claim_id"],
            "formula": seed["formula"],
            "claim": seed["claim"],
            "interpretive_terms": seed["interpretive_terms"],
            "core_symptoms": (rule or {}).get("core_symptoms", []),
            "core_pulse": (rule or {}).get("core_pulse", []),
            "classical_evidence": supporting,
            "terms_verbatim_in_original": {
                t: cids for t, cids in sorted(term_hits_a.items())},
            "commentarial_chronology": chronology,
            "first_proponent": first_proponent,
            "first_proponent_note": "「最早可見」以在庫九注本為限；"
                                    "更早的散佚注釋不可考，不作臆斷。",
            "term_first_use": term_first_use,
            "n_commentators_using_terms": len(chronology),
            "school_views": sorted({e["school_id"] for e in chronology if e["school_id"]}),
            "controversies": seed["controversies"],
            "evidence_grade": _grade(term_hits_a, term_hits_c),
            "warning": ("解釋性術語僅見於注文層——該命題屬後世歸納，"
                        "應與原文直述區分。" if not term_hits_a else
                        "部分術語逐字見於原文，但命題的完整表述仍含後世歸納成分。"),
        })

    return {"note": "方證觀點庫：種子只給命題與術語，證據等級/首倡時間線/學派"
                    "立場全部由 A/C 層逐字檢驗與朝代排序得出，多觀點並存不裁決。",
            "n_claims": len(claims), "claims": claims}
