"""Typed Run Graph（Protocol §10，P0-6）：把粗粒度 execute 拆開。

    intake → task_classify → scope_contract → plan_compile
    → catalog_resolution → retrieval_fanout
    → identity_and_attribution_check → counterevidence_search
    → claim_compile → claim_verify → synthesis → citation_bind
    → safety_and_policy → human_review → release

每個節點帶完整契約（Protocol §10.1）：輸入/輸出 schema（字段名清單，
純標準庫的輕量表達）、依賴、工具範圍、證據要求、預算、超時、重試、
回退、緩存鍵、冪等鍵、放行條件、取消邊界。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

NODE_TYPES = ("intake", "classify", "scope", "plan", "resolve", "retrieve",
              "check", "counter", "compile", "verify", "synthesize", "bind",
              "safety", "review", "release")

FALLBACKS = ("fail", "skip", "degrade")


@dataclass
class NodeContract:
    node_id: str
    node_type: str
    input_schema: List[str] = field(default_factory=list)   # 必需輸入字段
    output_schema: List[str] = field(default_factory=list)  # 承諾輸出字段
    dependencies: List[str] = field(default_factory=list)
    tool_scope: List[str] = field(default_factory=list)     # 允許的命名空間
    evidence_requirement: str = ""
    budget_tool_calls: int = 0            # 0=不在本節點調工具
    timeout_ms: int = 60_000
    retry_policy: int = 0
    fallback_policy: str = "fail"
    cache_key_fields: List[str] = field(default_factory=list)
    idempotency_key_fields: List[str] = field(default_factory=list)
    release_condition: str = ""
    cancellation_boundary: bool = True    # 節點邊界可取消
    # 每次執行都重跑（裁定類節點）：paused-at-release 的 run 被無參數
    # resume 時，若跳過已 ok 的 release 節點，for-else 會把狀態翻成
    # completed——繞過人工審核。裁定必須基於當前 approved 集合重新推導。
    always_rerun: bool = False

    def __post_init__(self):
        if self.node_type not in NODE_TYPES:
            raise ValueError(f"非法 node_type {self.node_type!r}")
        if self.fallback_policy not in FALLBACKS:
            raise ValueError(f"非法 fallback_policy {self.fallback_policy!r}")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


RESEARCH_GRAPH: List[NodeContract] = [
    NodeContract(
        node_id="intake", node_type="intake",
        input_schema=["query", "principal"],
        output_schema=["sanitized_query", "injection_signals",
                       "triage_decision"],
        evidence_requirement="輸入標記為 UNTRUSTED；紅旗/意圖守衛結論",
        release_condition="未被安全攔截（攔截則直達 release）"),
    NodeContract(
        node_id="task_classify", node_type="classify",
        dependencies=["intake"],
        input_schema=["sanitized_query"],
        output_schema=["task_type", "topic", "query_forms"],
        cache_key_fields=["sanitized_query"],
        release_condition="task_type ∈ TASK_TYPES"),
    NodeContract(
        node_id="scope_contract", node_type="scope",
        dependencies=["task_classify"],
        input_schema=["task_type"],
        output_schema=["corpus_scope", "corpus_version"],
        release_condition="檢索範圍已凍結（版本+分類+排除項）"),
    NodeContract(
        node_id="plan_compile", node_type="plan",
        dependencies=["scope_contract"],
        input_schema=["task_type", "topic"],
        output_schema=["plan_steps", "skill_used"],
        release_condition="計劃步驟均落在允許的工具範圍內"),
    NodeContract(
        node_id="catalog_resolution", node_type="resolve",
        dependencies=["plan_compile"],
        input_schema=["topic"],
        output_schema=["resolved_works", "identity_flags"],
        tool_scope=["catalog"],
        budget_tool_calls=6,
        retry_policy=1,
        fallback_policy="degrade",
        release_condition="涉及著作已解析或如實標記未解析"),
    NodeContract(
        node_id="retrieval_fanout", node_type="retrieve",
        dependencies=["catalog_resolution"],
        input_schema=["query_forms", "task_type"],
        output_schema=["evidence_ids", "coverage_ids"],
        tool_scope=["text", "citation", "concept", "collation", "formula",
                    "herb", "case"],
        budget_tool_calls=24,
        retry_policy=1,
        fallback_policy="degrade",
        evidence_requirement="每次檢索必須產生 SearchCoverage",
        release_condition="取證完成且覆蓋記錄齊全"),
    NodeContract(
        node_id="identity_and_attribution_check", node_type="check",
        dependencies=["retrieval_fanout"],
        input_schema=["evidence_ids"],
        output_schema=["identity_report"],
        evidence_requirement="每條證據的 work/witness 身份鏈完整性",
        release_condition="同名異書衝突已標記 needs_review"),
    NodeContract(
        node_id="counterevidence_search", node_type="counter",
        dependencies=["identity_and_attribution_check"],
        input_schema=["task_type", "query_forms"],
        output_schema=["counter_obligations", "counter_results"],
        tool_scope=["citation", "text"],
        budget_tool_calls=12,
        fallback_policy="degrade",
        evidence_requirement="高風險主張的反證義務逐項執行",
        release_condition="counterevidence_policy=mandatory 時義務全部執行"),
    NodeContract(
        node_id="claim_compile", node_type="compile",
        dependencies=["counterevidence_search"],
        input_schema=["task_type", "topic", "evidence_ids"],
        output_schema=["claim_ids"],
        release_condition="主張均綁定證據 id 與覆蓋記錄"),
    NodeContract(
        node_id="claim_verify", node_type="verify",
        dependencies=["claim_compile"],
        input_schema=["claim_ids"],
        output_schema=["verification_summary"],
        evidence_requirement="attribution/quotation/semantic/coverage 四項",
        release_condition="無 failed 主張進入下游"),
    NodeContract(
        node_id="synthesis", node_type="synthesize",
        dependencies=["claim_verify"],
        input_schema=["claim_ids"],
        output_schema=["draft_answer"],
        release_condition="表達只基於 verified/needs_review 主張，"
                          "不新增事實"),
    NodeContract(
        node_id="citation_bind", node_type="bind",
        dependencies=["synthesis"],
        input_schema=["draft_answer", "claim_ids"],
        output_schema=["bound_answer", "citations"],
        release_condition="每個事實性主張可定位到 ClaimRecord"),
    NodeContract(
        node_id="safety_and_policy", node_type="safety",
        dependencies=["citation_bind"],
        input_schema=["bound_answer", "principal"],
        output_schema=["safety_report"],
        release_condition="角色×目的限制檢查通過"),
    NodeContract(
        node_id="human_review", node_type="review",
        dependencies=["safety_and_policy"],
        input_schema=["claim_ids", "safety_report"],
        output_schema=["review_queue"],
        release_condition="needs_review 主張生成審批請求",
        always_rerun=True),
    NodeContract(
        node_id="release", node_type="release",
        dependencies=["human_review"],
        input_schema=["bound_answer", "claim_ids", "review_queue"],
        output_schema=["envelope", "decision"],
        release_condition="五態裁定；blocked/failed_closed 不可人工放行",
        always_rerun=True),
]


def validate_graph(graph: List[NodeContract] = None) -> List[str]:
    """圖靜態校驗：依賴存在、無環、輸出字段可覆蓋下游輸入。"""
    graph = graph or RESEARCH_GRAPH
    ids = [n.node_id for n in graph]
    problems: List[str] = []
    if len(set(ids)) != len(ids):
        problems.append("節點 id 重複")
    known = set(ids)
    for n in graph:
        for d in n.dependencies:
            if d not in known:
                problems.append(f"{n.node_id} 依賴未知節點 {d}")
    # 拓撲環檢測
    order: List[str] = []
    remaining = {n.node_id: set(n.dependencies) for n in graph}
    while remaining:
        ready = [k for k, deps in remaining.items()
                 if not deps - set(order)]
        if not ready:
            problems.append(f"依賴環：{sorted(remaining)}")
            break
        for k in sorted(ready):
            order.append(k)
            del remaining[k]
    return problems
