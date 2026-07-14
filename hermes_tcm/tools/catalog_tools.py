"""catalog.*：書目與文獻身份工具（Protocol §9.2）。"""
from __future__ import annotations

from typing import Dict

from .contracts import EvidenceContract, ToolContractV2
from ._shared import unavailable, work_registry


def t_resolve_work(title: str) -> Dict:
    reg = work_registry()
    if reg is None:
        return unavailable("catalog.resolve_work")
    res = reg.resolve_work(title)
    return {"tool": "catalog.resolve_work", "available": True,
            "resolution": res.to_dict(),
            "note": "同名異書不自動歸併：needs_human_adjudication=true 時"
                    "必須指定作者/朝代或走人工裁決"}


def t_get_work(work_id: str) -> Dict:
    reg = work_registry()
    if reg is None:
        return unavailable("catalog.get_work")
    w = reg.works.get(work_id)
    if w is None:
        return {"error": f"未知 work_id：{work_id}",
                "hint": "先用 catalog.resolve_work 解析書名"}
    return {"tool": "catalog.get_work", "available": True,
            "work": w.to_dict(),
            "witnesses": [reg.witnesses[wid].to_dict()
                          for wid in w.witness_ids
                          if wid in reg.witnesses]}


def t_list_witnesses(work_id: str = "", title: str = "") -> Dict:
    reg = work_registry()
    if reg is None:
        return unavailable("catalog.list_witnesses")
    if not work_id and title:
        res = reg.resolve_work(title)
        if not res.resolved_work_id:
            return {"error": f"無法解析書名：{title}",
                    "candidates": res.candidates}
        work_id = res.resolved_work_id
    w = reg.works.get(work_id)
    if w is None:
        return {"error": f"未知 work_id：{work_id}"}
    return {"tool": "catalog.list_witnesses", "available": True,
            "work_id": work_id, "canonical_title": w.canonical_title,
            "n_witnesses": len(w.witness_ids),
            "witnesses": [reg.witnesses[wid].to_dict()
                          for wid in w.witness_ids if wid in reg.witnesses]}


def t_resolve_title_alias(alias: str) -> Dict:
    reg = work_registry()
    if reg is None:
        return unavailable("catalog.resolve_title_alias")
    res = reg.resolve_work(alias)
    return {"tool": "catalog.resolve_title_alias", "available": True,
            "alias": alias, "resolution": res.to_dict()}


def t_list_categories() -> Dict:
    reg = work_registry()
    if reg is None:
        return unavailable("catalog.list_categories")
    cats: Dict[str, int] = {}
    for w in reg.works.values():
        cats[w.genre or "（無分類）"] = cats.get(w.genre or "（無分類）", 0) + 1
    return {"tool": "catalog.list_categories", "available": True,
            "categories": dict(sorted(cats.items(),
                                      key=lambda kv: (-kv[1], kv[0]))),
            **reg.stats()}


def t_resolve_person(name: str) -> Dict:
    reg = work_registry()
    if reg is None:
        return unavailable("catalog.resolve_person")
    name = (name or "").strip()
    if not name:
        return {"error": "須提供人名"}
    works = [w.to_dict() for w in reg.works.values()
             if any(name in a for a in w.attributed_authors)]
    return {"tool": "catalog.resolve_person", "available": True,
            "person": name, "n_works": len(works),
            "works": works[:20],
            "note": "按編目作者字段匹配；同名人物消歧屬人工裁決範圍"}


def register(reg) -> None:
    meta_ec = EvidenceContract(returns_primary_text=False,
                               evidence_role="metadata_only",
                               minimum_locator=["work_id"])
    reg.add(ToolContractV2(
        name="catalog.resolve_work",
        description="書名 → Work 身份解析（含別名/折疊匹配/同名異書消歧候選）。"
                    "書名相同不等於同一著作。",
        input_schema={"type": "object", "properties": {
            "title": {"type": "string", "description": "書名/別名/傳本標題"}},
            "required": ["title"]},
        func=t_resolve_work,
        use_when=["需要把用戶提到的書名定位到唯一著作身份",
                  "檢索前確定 work_id"],
        do_not_use_when=["已持有 work_id（直接用 catalog.get_work）"],
        evidence_contract=meta_ec,
        failure_modes=["corpus_unavailable", "ambiguous_work_identity"]))
    reg.add(ToolContractV2(
        name="catalog.get_work",
        description="按 work_id 取著作全息：權威記錄 + 全部傳本（Witness）。",
        input_schema={"type": "object", "properties": {
            "work_id": {"type": "string"}}, "required": ["work_id"]},
        func=t_get_work,
        use_when=["已解析 work_id 後取著作詳情"],
        evidence_contract=meta_ec,
        failure_modes=["corpus_unavailable", "unknown_work_id"]))
    reg.add(ToolContractV2(
        name="catalog.list_witnesses",
        description="列出某著作的全部傳本（Witness）：傳本標記/版本/年代/"
                    "source_type（現代整理本與古代傳本嚴格分開）。",
        input_schema={"type": "object", "properties": {
            "work_id": {"type": "string"},
            "title": {"type": "string", "description": "或直接給書名"}},
            "required": []},
        func=t_list_witnesses,
        use_when=["傳本比較/校勘前列出可用傳本"],
        evidence_contract=meta_ec,
        failure_modes=["corpus_unavailable", "unknown_work_id"]))
    reg.add(ToolContractV2(
        name="catalog.resolve_title_alias",
        description="別名/異題 → 權威著作記錄。",
        input_schema={"type": "object", "properties": {
            "alias": {"type": "string"}}, "required": ["alias"]},
        func=t_resolve_title_alias,
        use_when=["用戶用非常見題名提及著作時"],
        evidence_contract=meta_ec,
        failure_modes=["corpus_unavailable"]))
    reg.add(ToolContractV2(
        name="catalog.list_categories",
        description="全庫分類統計 + 身份鏈概況（Work/Witness/待裁決計數）。",
        input_schema={"type": "object", "properties": {}},
        func=t_list_categories,
        use_when=["定義檢索範圍前了解庫的分類構成"],
        evidence_contract=meta_ec,
        failure_modes=["corpus_unavailable"]))
    reg.add(ToolContractV2(
        name="catalog.resolve_person",
        description="人名 → 署名著作清單（按編目作者字段）。",
        input_schema={"type": "object", "properties": {
            "name": {"type": "string"}}, "required": ["name"]},
        func=t_resolve_person,
        use_when=["按醫家檢索其著作"],
        evidence_contract=meta_ec,
        failure_modes=["corpus_unavailable"]))
