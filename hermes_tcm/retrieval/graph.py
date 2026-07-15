"""graph：圖擴展召回（P1 實裝）。

以既有圖結構做**多跳鄰域擴召**（recall expansion）：

    條文 id（SHL_*）   → 條文關係圖 BFS（同方族/鑒別/傳變/禁忌邊）
    段落 id（psg_*）   → 段落文本探針 → 引文傳播網絡（著作級節點）
    文句（其他字符串） → 引文傳播網絡（著作級節點）

證據不變量：擴召結果是**召回信號，不是證據**——節點/邊用於擴大
候選池與檢索優先級，正文必須經 text.read_passage / formula.* 等
取證工具重新取得並過 Broker 台賬。輸出如實標注 evidence_role。
"""
from __future__ import annotations

import re
from typing import Dict, List, Sequence

RE_CLAUSE_ID = re.compile(r"^SHL_SONGBEN_(?:AUX_)?\d{4}$")
RE_PASSAGE_ID = re.compile(r"^psg_[0-9a-f]{12}$")
MAX_HOPS = 3
MAX_NODES = 48


def _expand_clause(seed: str, hops: int, relation_type: str,
                   nodes: Dict[str, Dict], edges: List[Dict]) -> None:
    """條文關係圖 BFS（經 shanghan Domain Pack 接縫，確定性次序）。"""
    from ..domains.shanghan import call_legacy_tool
    frontier = [seed]
    nodes.setdefault(seed, {"id": seed, "kind": "clause", "hop": 0})
    for hop in range(1, hops + 1):
        next_frontier: List[str] = []
        for cid in frontier:
            if len(nodes) >= MAX_NODES:
                return
            args = {"ref": cid}
            if relation_type:
                args["relation_type"] = relation_type
            out = call_legacy_tool("shanghan_relations", args)
            if not isinstance(out, dict) or out.get("error"):
                continue
            for e in out.get("edges", []):
                other = e.get("other_clause_id", "")
                if not other:
                    continue
                edges.append({"source": cid, "target": other,
                              "relation_type": e.get("relation_type", ""),
                              "hop": hop})
                if other not in nodes and len(nodes) < MAX_NODES:
                    nodes[other] = {"id": other, "kind": "clause",
                                    "hop": hop,
                                    "excerpt": e.get("other_text", "")}
                    next_frontier.append(other)
        frontier = sorted(next_frontier)
        if not frontier:
            return


def _expand_quote(seed: str, probe: str, nodes: Dict[str, Dict],
                  edges: List[Dict], max_scan: int) -> None:
    """引文傳播網絡擴召（著作級節點；時間先後 + 逐字相似度邊）。"""
    from ..tools.citation_tools import t_build_citation_network
    out = t_build_citation_network(quote=probe, max_scan=max_scan)
    if not isinstance(out, dict) or out.get("error") \
            or out.get("available", True) is False:
        return
    for n in out.get("nodes", []):
        key = f"work:{n}"
        if key not in nodes and len(nodes) < MAX_NODES:
            nodes[key] = {"id": key, "kind": "work", "title": n,
                          "hop": 1, "seed": seed}
    for e in out.get("edges", []):
        edges.append({"source": f"work:{e.get('source')}",
                      "target": f"work:{e.get('target')}",
                      "relation_type": "citation_relay",
                      "similarity": e.get("similarity"),
                      "hop": 1})


def expand_graph(seed_ids: Sequence[str], hops: int = 1,
                 relation_type: str = "", max_scan: int = 300,
                 **kwargs) -> Dict:
    """seed（條文 id / 段落 id / 文句）→ 多跳鄰域召回擴展。"""
    seeds = [str(x).strip() for x in (seed_ids or []) if str(x).strip()][:6]
    if not seeds:
        return {"error": "至少提供 1 個 seed（條文 id/段落 id/文句）"}
    hops = max(1, min(int(hops or 1), MAX_HOPS))
    nodes: Dict[str, Dict] = {}
    edges: List[Dict] = []
    skipped: List[Dict] = []
    for seed in seeds:
        if RE_CLAUSE_ID.match(seed):
            _expand_clause(seed, hops, relation_type, nodes, edges)
        elif RE_PASSAGE_ID.match(seed):
            from ..tools._shared import searcher
            s = searcher()
            p = s.index.get(seed) if s is not None else None
            if p is None:
                skipped.append({"seed": seed,
                                "reason": "passage_not_found_or_corpus_"
                                          "unavailable"})
                continue
            probe = "".join(p.flat_text.split())[:12]
            nodes.setdefault(seed, {"id": seed, "kind": "passage",
                                    "hop": 0})
            _expand_quote(seed, probe, nodes, edges, max_scan)
        elif len("".join(seed.split())) >= 4:
            nodes.setdefault(f"quote:{seed[:16]}",
                             {"id": f"quote:{seed[:16]}", "kind": "quote",
                              "hop": 0})
            _expand_quote(seed, seed, nodes, edges, max_scan)
        else:
            skipped.append({"seed": seed, "reason": "unrecognized_seed"})
    expanded = sorted(k for k, v in nodes.items() if v.get("hop", 0) > 0)
    return {"tool": "graph.expand_neighborhood", "available": True,
            "seeds": seeds, "hops": hops,
            "n_nodes": len(nodes), "n_edges": len(edges),
            "nodes": [nodes[k] for k in sorted(nodes)],
            "edges": edges[:MAX_NODES * 2],
            "expanded_ids": expanded,
            "skipped": skipped,
            "evidence_role": "recall_signal",
            "note": "圖擴召是召回信號，不是證據：擴展節點的正文必須經 "
                    "text.read_passage / 領域工具重新取證並過 Broker 台賬"}
