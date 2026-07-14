"""ResearchOrchestrator：多專家研究編排（Protocol §11.2 隔離合議）。

流程：

    共享取證（Broker 台賬）
    → 按角色切分**獨立** Evidence Packet（不同專家不讀彼此結論）
    → 各專家形成 claims
    → 匿名交叉審查（衝突檢測）
    → Independent Verifier 逐主張核驗
    → Synthesizer 只基於已驗證結果綜合

單代理路徑（typed DAG）由 harness.controller 提供；本編排器是
多專家模式（等價於舊 council 的 V2 形態）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..claims.policy_dsl import ConclusionPolicyEngine
from ..core.principals import Principal
from ..evidence.coverage import SearchCoverage
from ..evidence.ledger import TypedEvidenceLedger
from ..evidence.packets import build_packet
from ..harness.budget import RunBudgetV2
from ..tools.broker import CapabilityBroker
from ..tools.registry import get_tcm_registry
from .specialists import (SPECIALIST_ROLES, cross_review,
                          dispatch_specialists)
from .synthesizer import Synthesizer
from .verifier import IndependentVerifier

# task_type → 參與專家
_TASK_SPECIALISTS = {
    "earliest_attestation": ["chronology_specialist",
                             "counterevidence_critic"],
    "term_genealogy": ["concept_historian", "chronology_specialist",
                       "counterevidence_critic"],
    "witness_comparison": ["catalog_resolver", "collation_specialist"],
    "formula_lineage": ["formula_herb_specialist",
                        "chronology_specialist"],
    "broad_consensus": ["passage_retriever", "counterevidence_critic"],
    "general_search": ["passage_retriever"],
}


class ResearchOrchestrator:
    def __init__(self, registry=None, principal: Optional[Principal] = None,
                 engine: Optional[ConclusionPolicyEngine] = None,
                 corpus_version: str = ""):
        self.registry = registry or get_tcm_registry()
        self.principal = principal or Principal(subject="orchestrator",
                                                role="researcher")
        self.engine = engine or ConclusionPolicyEngine()
        self.corpus_version = corpus_version

    def run(self, topic: str, task_type: str = "general_search",
            budget: Optional[RunBudgetV2] = None) -> Dict:
        budget = budget or RunBudgetV2()
        ledger = TypedEvidenceLedger(self.corpus_version)
        broker = CapabilityBroker(
            self.registry.for_role(self.principal.role), ledger,
            principal=self.principal, budget=budget,
            corpus_version=self.corpus_version)

        # 1. 共享取證（每個角色的檢索走自己的工具範圍）
        roles = _TASK_SPECIALISTS.get(task_type, ["passage_retriever"])
        retrieval = {
            "chronology_specialist": ("citation.trace_quote",
                                      {"quote": topic}),
            "concept_historian": ("concept.drift", {"term": topic}),
            "counterevidence_critic": ("citation.counter_search",
                                       {"quote": topic}),
            "collation_specialist": ("collation.align_witnesses",
                                     {"work": topic, "query": topic}),
            "catalog_resolver": ("catalog.resolve_work", {"title": topic}),
            "formula_herb_specialist": ("formula.trace_lineage",
                                        {"formula": topic}),
            "passage_retriever": ("text.search_passages",
                                  {"query": topic, "order": "dynasty"}),
        }
        packets = {}
        for role in roles:
            tool, args = retrieval[role]
            marker = len(ledger)
            broker.call(tool, args, node_id=f"specialist:{role}")
            # 獨立包：該角色本次調用新增的證據（不含他人取證）
            role_records = ledger.node_records(f"specialist:{role}")
            coverage = None
            if broker.coverages:
                coverage = sorted(broker.coverages.values(),
                                  key=lambda c: c.coverage_id)[-1]
            packets[role] = build_packet(
                f"{topic}#{role}", role_records, coverage=coverage,
                corpus_version=self.corpus_version)
            del marker

        # 2. 各專家獨立形成 claims（不讀彼此結論）
        reports = dispatch_specialists(roles, packets, task_type, topic,
                                       budget=budget)

        # 3. 匿名交叉審查
        conflicts = cross_review(reports)

        # 4. 獨立核驗（權威）
        all_claims = [c for rep in reports for c in rep.claims]
        coverage = None
        if broker.coverages:
            coverage = sorted(broker.coverages.values(),
                              key=lambda c: c.coverage_id)[0]
        verifier = IndependentVerifier(ledger, self.engine)
        summary = verifier.verify(
            all_claims, coverage=coverage,
            tools_used=[e["tool"] for e in broker.audit_tail(100)
                        if e.get("ok")],
            role=self.principal.role)

        # 5. 綜合（只基於已驗證結果）
        synthesis = Synthesizer().compose(all_claims, conflicts)
        return {"answer": synthesis["answer"],
                "task_type": task_type,
                "topic": topic,
                "specialists": [r.to_dict() for r in reports],
                "conflicts": conflicts,
                "verification": summary,
                "synthesis_note": synthesis["note"],
                "budget": budget.snapshot(),
                "n_evidence": len(ledger),
                "guardrail_events": broker.guardrail_events}
