"""graph.*：圖譜工具（Protocol §9.1 命名空間）。

引文網絡（全庫層）與條文關係圖（shanghan 領域層）的圖投影入口——
底層分別委托 citation.build_citation_network 與 shanghan_relations，
統一為 nodes/edges 圖負載。
"""
from __future__ import annotations

from typing import Dict

from .contracts import EvidenceContract, ToolContractV2


def t_citation_network(quote: str, max_scan: int = 300) -> Dict:
    from .citation_tools import t_build_citation_network
    out = t_build_citation_network(quote=quote, max_scan=max_scan)
    if isinstance(out, dict):
        out = dict(out)
        out["tool"] = "graph.citation_network"
    return out


def t_clause_relations(ref: str, relation_type: str = "") -> Dict:
    """條文關係圖鄰接（domain=shanghan）：nodes/edges 統一圖負載。"""
    from hermes_shanghan.agent.tools import get_registry
    args: Dict = {"ref": ref}
    if relation_type:
        args["relation_type"] = relation_type
    out = get_registry().call("shanghan_relations", args)
    if not isinstance(out, dict) or out.get("error"):
        return {**(out or {}), "tool": "graph.clause_relations"}
    center = out.get("clause_id", "")
    edges = [{"source": center, "target": e.get("other_clause_id", ""),
              "relation_type": e.get("relation_type", ""),
              "description": e.get("description", "")}
             for e in out.get("edges", [])]
    nodes = sorted({center} | {e["target"] for e in edges} - {""})
    return {"tool": "graph.clause_relations", "available": True,
            "domain": "shanghan",
            "center": center,
            "n_nodes": len(nodes), "n_edges": len(edges),
            "nodes": nodes, "edges": edges}


def register(reg) -> None:
    ec = EvidenceContract(returns_primary_text=False,
                          evidence_role="metadata_only",
                          minimum_locator=["work_id"])
    reg.add(ToolContractV2(
        name="graph.citation_network",
        description="某文句的傳播網絡圖（節點=著作，邊=時間先後+逐字"
                    "相似度）——citation.build_citation_network 的圖投影。",
        input_schema={"type": "object", "properties": {
            "quote": {"type": "string"},
            "max_scan": {"type": "integer", "default": 300}},
            "required": ["quote"]},
        func=t_citation_network,
        use_when=["以圖結構分析文句傳播"],
        evidence_contract=ec,
        failure_modes=["corpus_unavailable"]))
    reg.add(ToolContractV2(
        name="graph.clause_relations",
        description="條文關係圖鄰接（同方族/鑒別/誤治傳變/禁忌/傳變/"
                    "次序；domain=shanghan）。",
        input_schema={"type": "object", "properties": {
            "ref": {"type": "string"},
            "relation_type": {"type": "string"}},
            "required": ["ref"]},
        func=t_clause_relations,
        use_when=["多跳條文關係遍歷/傳變鏈追蹤"],
        evidence_contract=ec,
        failure_modes=["clause_not_found"]))
