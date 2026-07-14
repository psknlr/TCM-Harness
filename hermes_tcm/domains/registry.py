"""Domain Pack 註冊表。

DomainPack 聲明：領域規則（分類/基準文本/證據層映射）、領域工具投影、
就緒狀態。未就緒的領域**如實標注**（不冒充可用）。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional


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
              "注家圖譜、劑量計量層全部沿用"),
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
