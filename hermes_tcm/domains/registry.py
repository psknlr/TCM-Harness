"""Domain Pack 註冊表（唯一主源）。

DomainPack 聲明：領域規則（分類/基準文本/證據層映射）、領域工具投影、
就緒狀態，以及**可執行接縫**（evidence_normalizer / entity_linker——
import 路徑惰性解析，向通用內核提供能力而不是被內核散落 import）。
未就緒的領域**如實標注**（不冒充可用）。

與 hermes_shanghan.domains（legacy 插件表）的關係：本表是 V2 主源；
legacy 表繼續服務舊入口。兩表交集領域的狀態由
``legacy_consistency_problems()`` 鉗制（tests 釘死），防止規則漂移。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from importlib import import_module
from typing import Any, Callable, Dict, List, Optional


def _resolve(path: str) -> Optional[Any]:
    """'module:attr' 惰性解析；空路徑返回 None（如實聲明未提供）。"""
    if not path:
        return None
    mod, _, attr = path.partition(":")
    return getattr(import_module(mod), attr)


@dataclass
class DomainPack:
    domain_id: str
    title: str
    status: str                      # ready | planned
    base_works: List[str] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)
    tool_projections: Dict[str, str] = field(default_factory=dict)
    legacy_package: str = ""
    notes: str = ""
    # —— 可執行接縫（import 路徑；空 = 未提供，如實聲明）——
    # evidence_normalizer: callable(tool_name, result, corpus_version)
    #     -> List[EvidenceRecord]，把領域工具結果轉為 V2 證據記錄
    evidence_normalizer: str = ""
    # entity_linker: callable(query) -> List[{"type","name","domain"}]
    entity_linker: str = ""

    def load_evidence_normalizer(self) -> Optional[Callable]:
        return _resolve(self.evidence_normalizer)

    def load_entity_linker(self) -> Optional[Callable]:
        return _resolve(self.entity_linker)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


DOMAIN_PACKS: Dict[str, DomainPack] = {
    "shanghan": DomainPack(
        domain_id="shanghan",
        title="傷寒論",
        status="ready",
        base_works=["傷寒論"],
        categories=["傷寒"],
        tool_projections={
            # 領域工具 → legacy 實現（hermes_shanghan 是第一個 Domain Pack）
            "domain.shanghan.search": "shanghan_search",
            "domain.shanghan.get_clause": "shanghan_get_clause",
            "domain.shanghan.formula_differential": "shanghan_differential",
            "domain.shanghan.variants": "shanghan_variants",
            "domain.shanghan.divergence": "shanghan_divergence_atlas",
            "domain.shanghan.dose": "shanghan_dose",
            "domain.shanghan.trace": "shanghan_trace",
        },
        legacy_package="hermes_shanghan",
        notes="第一個高質量 Domain Pack：A/B/C/D/E 證據層、條文規則庫、"
              "注家圖譜、劑量計量層全部沿用",
        evidence_normalizer="hermes_tcm.domains.shanghan:normalize_evidence",
        entity_linker="hermes_tcm.domains.shanghan:link_entities"),
    "jingui": DomainPack(
        domain_id="jingui", title="金匱要略", status="planned",
        base_works=["金匱要略"], categories=["金匱"],
        notes="語料已入 corpus_raw/jingui；規則挖掘流水線未跑"),
    "neijing": DomainPack(
        domain_id="neijing", title="內經", status="planned",
        base_works=["黃帝內經素問", "靈樞經"], categories=["醫經"]),
    "bencao": DomainPack(
        domain_id="bencao", title="本草", status="planned",
        categories=["本草"]),
    "formulae": DomainPack(
        domain_id="formulae", title="方書", status="planned",
        categories=["方書"]),
    "medical_cases": DomainPack(
        domain_id="medical_cases", title="醫案", status="planned",
        categories=["醫案"]),
    "warm_disease": DomainPack(
        domain_id="warm_disease", title="溫病", status="planned",
        categories=["瘟疫", "溫病"]),
    "acupuncture": DomainPack(
        domain_id="acupuncture", title="針灸", status="planned",
        categories=["針灸"]),
}


def get_domain_pack(domain_id: str) -> Optional[DomainPack]:
    return DOMAIN_PACKS.get(domain_id)


def list_domain_packs() -> List[Dict]:
    return [p.to_dict() for p in DOMAIN_PACKS.values()]


def ready_domain_packs() -> List[DomainPack]:
    return [p for p in DOMAIN_PACKS.values() if p.status == "ready"]


def normalize_domain_evidence(tool_name: str, result: Dict,
                              corpus_version: str = "") -> List:
    """統一證據適配入口（Broker 在 passage_evidence 之後調用）。

    逐個 ready Domain Pack 的 normalizer 嘗試轉換；單個適配器故障
    只影響自己（證據寧可少計不可偽計），不阻斷調用管道。"""
    records: List = []
    for pack in DOMAIN_PACKS.values():
        if pack.status != "ready" or not pack.evidence_normalizer:
            continue
        try:
            fn = pack.load_evidence_normalizer()
            if fn is not None:
                records.extend(fn(tool_name, result, corpus_version) or [])
        except Exception:
            continue
    return records


def link_domain_entities(query: str) -> List[Dict]:
    """全部 ready Domain Pack 的實體鏈接（確定性，按 domain_id 有序）。"""
    entities: List[Dict] = []
    for did in sorted(DOMAIN_PACKS):
        pack = DOMAIN_PACKS[did]
        if pack.status != "ready" or not pack.entity_linker:
            continue
        try:
            fn = pack.load_entity_linker()
            if fn is not None:
                entities.extend(fn(query) or [])
        except Exception:
            continue
    return entities


# ---------------------------------------------------------------------------
# 與 legacy 註冊表（hermes_shanghan.domains）的防漂移鉗制
# ---------------------------------------------------------------------------
# V2 status → legacy status 的等價詞（兩表詞彙不同是歷史事實，等價表
# 顯式聲明，不靠字符串巧合）
_LEGACY_STATUS_EQUIV = {"ready": "active", "planned": "planned"}

# legacy 'classics'（全庫插件）在 V2 對應通用命名空間 text.*/catalog.*
# 平台層而非 Domain Pack——顯式豁免，不是漂移
_LEGACY_PLATFORM_PLUGINS = frozenset({"classics"})


def legacy_consistency_problems() -> List[str]:
    """兩套註冊表交集領域的狀態一致性檢查（tests 釘死為空清單）。"""
    try:
        from hermes_shanghan.domains import DOMAINS as LEGACY
    except Exception as exc:      # legacy 表加載失敗本身就是問題
        return [f"legacy 註冊表不可加載：{type(exc).__name__}"]
    problems: List[str] = []
    for did, pack in DOMAIN_PACKS.items():
        legacy = LEGACY.get(did)
        if legacy is None:
            continue              # V2 先聲明的規劃領域（bencao 等）
        want = _LEGACY_STATUS_EQUIV.get(pack.status)
        if want != legacy.status:
            problems.append(
                f"領域 {did} 狀態漂移：V2={pack.status} "
                f"≠ legacy={legacy.status}")
    for did in LEGACY:
        if did in _LEGACY_PLATFORM_PLUGINS or did in DOMAIN_PACKS:
            continue
        problems.append(f"legacy 領域 {did} 未在 V2 註冊表聲明")
    return problems


def unified_domain_view() -> List[Dict]:
    """兩套註冊表的合併視圖（單一可觀察面；V2 為主源）。"""
    try:
        from hermes_shanghan.domains import DOMAINS as LEGACY
    except Exception:
        LEGACY = {}
    rows: List[Dict] = []
    for did in sorted(set(DOMAIN_PACKS) | set(LEGACY)):
        pack = DOMAIN_PACKS.get(did)
        legacy = LEGACY.get(did)
        rows.append({
            "domain_id": did,
            "title": (pack.title if pack else
                      getattr(legacy, "name", "")),
            "v2_status": pack.status if pack else "",
            "legacy_status": getattr(legacy, "status", ""),
            "platform_plugin": did in _LEGACY_PLATFORM_PLUGINS,
            "has_evidence_normalizer":
                bool(pack and pack.evidence_normalizer),
            "has_entity_linker": bool(pack and pack.entity_linker)})
    return rows


def call_domain_tool(name: str, arguments: Dict) -> Dict:
    """domain.<pack>.<op> → legacy 工具委托（僅 ready 領域）。"""
    parts = name.split(".")
    if len(parts) != 3 or parts[0] != "domain":
        return {"error": f"非法領域工具名：{name}"}
    pack = DOMAIN_PACKS.get(parts[1])
    if pack is None:
        return {"error": f"未知領域：{parts[1]}",
                "available": sorted(DOMAIN_PACKS)}
    if pack.status != "ready":
        return {"error": f"領域 {parts[1]} 未就緒（status={pack.status}）",
                "note": pack.notes}
    legacy = pack.tool_projections.get(name)
    if legacy is None:
        return {"error": f"領域 {parts[1]} 無此工具投影：{name}",
                "available": sorted(pack.tool_projections)}
    from hermes_shanghan.agent.tools import get_registry
    out = get_registry().call(legacy, arguments)
    if isinstance(out, dict):
        out = dict(out)
        out["tool"] = name
        out["domain"] = parts[1]
    return out
