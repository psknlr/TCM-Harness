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
from .base import DomainPackInterface

DOMAIN_ID = "shanghan"

# 測試釘死的兼容 id 格式（AGENT_CONSTITUTION §五-20）
RE_CLAUSE_ID = re.compile(r"SHL_SONGBEN_(?:AUX_)?\d{4}")

# 每次調用最多入賬的條文記錄數（與 legacy _attach_excerpts 的 12 一致；
# 台賬總量另有 MAX_RECORDS 上限）
MAX_RECORDS_PER_CALL = 12

_DOMAIN_TOOL_PREFIXES = ("formula.", "herb.", "case.", "domain.shanghan.")


def _legacy_registry():
    from hermes_shanghan.agent.tools import get_registry
    return get_registry()


def call_legacy_tool(name: str, arguments: Dict) -> Dict:
    """shanghan legacy 工具委托入口（Capability-Broker 管道在 legacy
    側照常執行：默認拒絕/校驗/緩存/超時/審計）。本函數是內核調用
    legacy 工具面的唯一縫隙——其它模塊不得直接 import legacy 註冊表。"""
    out = _legacy_registry().call(name, dict(arguments or {}))
    return out if isinstance(out, dict) else {"error": "非法工具輸出"}


def _clause_store():
    try:
        return _legacy_registry().art.clause_store()
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


# ---------------------------------------------------------------------------
# DomainPack 接口實現（P0-3：shanghan 是第一個標準插件）
# ---------------------------------------------------------------------------
_INTENT_CUES = ("傷寒", "伤寒", "六經", "六经", "經方", "经方",
                "方證", "方证", "太陽病", "太阳病", "少陽", "少阳",
                "陽明", "阳明", "太陰", "太阴", "少陰", "少阴", "厥陰",
                "厥阴")


class ShanghanDomainPack(DomainPackInterface):
    """shanghan Domain Pack（DomainPackInterface 的第一個完整實現）。

    內核經 registry 的 implementation 接縫加載本類；本模塊（連同
    hermes_tcm/platform.py）是內核觸達 hermes_shanghan 的僅有兩個
    允許模塊（tests/test_dependency_inversion.py 強制）。"""

    domain_id = DOMAIN_ID

    def metadata(self) -> Dict:
        from .registry import get_domain_pack
        pack = get_domain_pack(self.domain_id)
        return pack.to_dict() if pack else {"domain_id": self.domain_id}

    def health(self) -> Dict:
        checks = []
        try:
            store = _clause_store()
            probe_ok = bool(store and store.get("SHL_SONGBEN_0012"))
            checks.append({"check": "clause_store", "ok": probe_ok,
                           "note": "clause store 可用（探針條文命中）"
                           if probe_ok else "clause store 不可用"})
        except Exception as exc:
            checks.append({"check": "clause_store", "ok": False,
                           "note": f"{type(exc).__name__}"})
        try:
            n_tools = len(_legacy_registry().names())
            checks.append({"check": "legacy_tools", "ok": n_tools > 0,
                           "note": f"{n_tools} 個 legacy 工具"})
        except Exception as exc:
            checks.append({"check": "legacy_tools", "ok": False,
                           "note": f"{type(exc).__name__}"})
        healthy = all(c["ok"] for c in checks)
        return {"domain_id": self.domain_id, "healthy": healthy,
                "status": "ready" if healthy else "degraded",
                "checks": checks}

    def register_tools(self, registry) -> None:
        """formula.* / herb.* / case.* 工具面（實現在 tools/domain_tools，
        所有權在本包——內核經本方法註冊，不直接耦合實現模塊）。"""
        from ..tools import domain_tools
        domain_tools.register(registry)

    def detect_intent(self, query: str) -> Dict:
        q = query or ""
        cues = [c for c in _INTENT_CUES if c in q]
        entities = link_entities(q)
        score = 1.0 if entities else (0.6 if cues else 0.0)
        return {"domain_id": self.domain_id, "score": score,
                "cues": cues[:6]}

    def extract_entities(self, query: str) -> List[Dict]:
        return link_entities(query)

    def build_plan(self, task_type: str,
                   entities: List[Dict] = ()) -> List[Dict]:
        plans = {
            "formula_pattern": [
                {"step": "resolve", "tool": "formula.resolve"},
                {"step": "library_corroborate",
                 "tool": "text.search_passages"}],
            "herb_profile": [
                {"step": "resolve", "tool": "herb.resolve"},
                {"step": "trace", "tool": "herb.trace_name"}],
            "case_study": [
                {"step": "cases", "tool": "case.search"},
                {"step": "library_corroborate",
                 "tool": "text.search_passages"}],
        }
        return [dict(s) for s in plans.get(task_type, [])]

    def normalize_evidence(self, tool_name: str, result: Dict,
                           corpus_version: str = "") -> List[EvidenceRecord]:
        return normalize_evidence(tool_name, result, corpus_version)

    def claim_policies(self) -> List[str]:
        return []       # 當前沿用通用策略引擎（領域策略屬擴展位）

    def specialists(self) -> List[str]:
        return ["formula_herb_specialist"]

    def evaluation_suites(self) -> List[str]:
        return ["tests/test_harness.py", "tests/test_evidence_integrity.py",
                "tests/test_audit_fixes.py"]

    def call_legacy_tool(self, name: str, arguments: Dict) -> Dict:
        return call_legacy_tool(name, arguments)
