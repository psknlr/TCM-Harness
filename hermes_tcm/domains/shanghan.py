"""shanghan Domain Pack 的可執行接縫（hermes_tcm 側）。

Protocol 的 DomainPack 接口方向：領域包向通用內核**提供能力**，而不是
內核散落地 import 領域內部模塊。本模塊集中提供 shanghan 包的兩個接縫：

* ``normalize_evidence`` —— 領域工具結果 → EvidenceRecord V2。
  修復 P0：legacy 傷寒工具以 evidence_excerpts / supporting_clauses /
  canonical_support 等形狀攜帶條文證據，而 V2 Broker 原本只識別
  passage_evidence，導致「工具契約聲明 returns_primary_text、
  台賬證據卻計零」——下游 Claim Verifier / Citation Binder 看不到
  傷寒工具已經取得的證據。
* ``link_entities`` —— 確定性實體鏈接（方名 seed + 別名詞表，
  最長匹配優先；不用 LLM 判定實體）。

誠實邊界：條文正文一律回 clause store 取全文重建（不信任結果 JSON
裡可能被截斷的摘錄）；正文不可得的 id 提及不入賬（id_mention_only
不是證據）。
"""
from __future__ import annotations

import json
import re
from typing import Dict, List

from ..core.identity import unit_urn, witness_urn, work_urn
from ..corpus.iiif import PassageLocator
from ..evidence.records import EvidenceRecord, evidence_id_for, quote_hash

DOMAIN_ID = "shanghan"

# 測試釘死的兼容 id 格式（AGENT_CONSTITUTION §五-20）
RE_CLAUSE_ID = re.compile(r"SHL_SONGBEN_(?:AUX_)?\d{4}")

# 每次調用最多入賬的條文記錄數（與 legacy _attach_excerpts 的 12 一致；
# 台賬總量另有 MAX_RECORDS 上限）
MAX_RECORDS_PER_CALL = 12

_DOMAIN_TOOL_PREFIXES = ("formula.", "herb.", "case.", "domain.shanghan.")


def _clause_store():
    try:
        from hermes_shanghan.agent.tools import get_registry
        return get_registry().art.clause_store()
    except Exception:
        return None


def applies_to(tool_name: str, result: Dict) -> bool:
    """本適配器是否負責該工具結果（廉價判定，避免全量掃描）。"""
    if not isinstance(result, dict):
        return False
    if result.get("domain") == DOMAIN_ID:
        return True
    return tool_name.startswith(_DOMAIN_TOOL_PREFIXES)


def normalize_evidence(tool_name: str, result: Dict,
                       corpus_version: str = "") -> List[EvidenceRecord]:
    """shanghan 工具結果中的條文錨點 → EvidenceRecord V2 清單。

    覆蓋全部 legacy 證據形狀（evidence_excerpts / supporting_clauses /
    canonical_support / 嵌套規則中的條文 id）：統一按條文 id 掃描，
    再回 clause store 取全文——正文與 quote_hash 構造期互驗。"""
    if not applies_to(tool_name, result):
        return []
    try:
        blob = json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        return []
    ids = list(dict.fromkeys(RE_CLAUSE_ID.findall(blob)))
    if not ids:
        return []
    store = _clause_store()
    if store is None:
        return []
    records: List[EvidenceRecord] = []
    for cid in ids[:MAX_RECORDS_PER_CALL]:
        clause = store.get(cid)
        text = getattr(clause, "clean_text", "") if clause else ""
        if not text:
            continue        # 正文不可得的 id 提及不算證據（fail-closed）
        book = getattr(clause, "book_title", "") or "傷寒論（宋本）"
        chapter = getattr(clause, "chapter", "") or ""
        qh = quote_hash(text)
        records.append(EvidenceRecord(
            evidence_id=evidence_id_for(cid, 0, len(text), qh),
            corpus_version=corpus_version,
            work_id=work_urn("傷寒論"),
            witness_id=witness_urn(book),
            text_unit_id=unit_urn(book, chapter),
            passage_id=cid,
            locator=PassageLocator(section=chapter),
            verbatim=text,
            quote_hash=qh,
            source_role="primary_text",
            witness_role="base_witness",
            epistemic_status="verbatim",
            # V2：verbatim 重驗 + 身份鏈完整（clause store 是釘死 id 的
            # 策展語料，work/witness 綁定確定）
            verification_level="V2",
            identity_confidence=0.9,
            # dynasty 留空（fail-closed）：無朝代記錄不能贏得首現比較，
            # 避免領域證據以硬編碼年代進入時間有序主張
            work_title=book,
            category="傷寒",
            section=chapter))
    return records


# ---------------------------------------------------------------------------
# 實體鏈接（確定性：詞表 + 最長匹配；不讓 LLM 判定實體）
# ---------------------------------------------------------------------------
def link_entities(query: str) -> List[Dict]:
    """查詢中的方劑實體（117 個 seed + 別名；最長匹配優先，重疊抑制）。"""
    try:
        from hermes_shanghan import lexicon
        from hermes_shanghan.textutil import normalize_query
    except Exception:
        return []
    q = normalize_query(query or "")
    if not q:
        return []
    names = sorted(set(lexicon.FORMULA_SEEDS) | set(lexicon.FORMULA_ALIASES),
                   key=lambda n: (-len(n), n))
    entities: List[Dict] = []
    consumed: List[tuple] = []
    seen = set()
    for name in names:
        if not name:
            continue
        start = q.find(name)
        while start != -1:
            span = (start, start + len(name))
            if not any(s < span[1] and span[0] < e for s, e in consumed):
                consumed.append(span)
                canonical = lexicon.canonical_formula(name)
                if canonical not in seen:
                    seen.add(canonical)
                    entities.append({"type": "formula", "name": canonical,
                                     "surface": name, "domain": DOMAIN_ID})
                break
            start = q.find(name, start + 1)
    entities.sort(key=lambda e: (e["name"],))
    return entities
