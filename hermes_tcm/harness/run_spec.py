"""RunSpec V2（Protocol §10.2）。"""
from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from ..core.principals import Principal

TASK_TYPES = ("earliest_attestation", "term_genealogy", "witness_comparison",
              "formula_lineage", "broad_consensus", "case_study",
              "general_search", "negative_probe",
              # 領域任務（Task Type × Domain Pack 正交路由）
              "formula_pattern", "herb_profile")

# 執行模式：single=typed DAG 單代理；council=隔離合議多專家
# （同一 RunStore / Evidence Ledger / Release Gate）
EXECUTION_MODES = ("single", "council")

OUTPUT_GENRES = ("research_brief", "answer", "collation_report",
                 "export_bundle")

COMPLETENESS = ("quick", "systematic", "exhaustive")

EVIDENCE_POLICIES = ("strict_claim_binding",)
COUNTEREVIDENCE_POLICIES = ("mandatory", "risk_based", "off")
HUMAN_REVIEW_POLICIES = ("risk_based", "always", "never")


def new_run_id(query: str) -> str:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    digest = hashlib.sha256(
        f"{query}{time.time_ns()}".encode()).hexdigest()[:6]
    return f"run_{stamp}_{digest}"


@dataclass
class CorpusScope:
    collections: List[str] = field(default_factory=lambda: ["all_classics"])
    categories: List[str] = field(default_factory=list)
    dynasties: List[str] = field(default_factory=list)
    works: List[str] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BudgetSpec:
    max_tool_calls: int = 80
    max_subagents: int = 8
    max_wall_ms: int = 600_000
    max_input_tokens: int = 250_000
    max_cost: float = 5.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ModelPolicy:
    planner: str = "deterministic"       # deterministic | high_reasoning
    retrieval_workers: str = "economy"
    verifier: str = "independent"
    temperature: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RunSpecV2:
    run_id: str
    query: str
    principal: Principal
    purpose_of_use: str = "historical_research"
    task_type: str = "general_search"
    execution_mode: str = "single"
    output_genre: str = "research_brief"
    corpus_scope: CorpusScope = field(default_factory=CorpusScope)
    completeness_requirement: str = "systematic"
    evidence_policy: str = "strict_claim_binding"
    counterevidence_policy: str = "mandatory"
    human_review_policy: str = "risk_based"
    model_policy: ModelPolicy = field(default_factory=ModelPolicy)
    budget: BudgetSpec = field(default_factory=BudgetSpec)
    environment_fingerprint: Dict[str, str] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    def __post_init__(self):
        if self.task_type not in TASK_TYPES:
            raise ValueError(f"非法 task_type {self.task_type!r}")
        if self.execution_mode not in EXECUTION_MODES:
            raise ValueError(f"非法 execution_mode {self.execution_mode!r}")
        if self.output_genre not in OUTPUT_GENRES:
            raise ValueError(f"非法 output_genre {self.output_genre!r}")
        if self.completeness_requirement not in COMPLETENESS:
            raise ValueError(
                f"非法 completeness {self.completeness_requirement!r}")
        if self.counterevidence_policy not in COUNTEREVIDENCE_POLICIES:
            raise ValueError(
                f"非法 counterevidence_policy "
                f"{self.counterevidence_policy!r}")
        if self.human_review_policy not in HUMAN_REVIEW_POLICIES:
            raise ValueError(
                f"非法 human_review_policy {self.human_review_policy!r}")
        # purpose 以 principal 為準（spec 級字段是快照，兩處必須一致）
        if self.purpose_of_use != self.principal.purpose_of_use:
            self.purpose_of_use = self.principal.purpose_of_use

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "query": self.query,
            "principal": self.principal.to_dict(),
            "purpose_of_use": self.purpose_of_use,
            "task_type": self.task_type,
            "execution_mode": self.execution_mode,
            "output_genre": self.output_genre,
            "corpus_scope": self.corpus_scope.to_dict(),
            "completeness_requirement": self.completeness_requirement,
            "evidence_policy": self.evidence_policy,
            "counterevidence_policy": self.counterevidence_policy,
            "human_review_policy": self.human_review_policy,
            "model_policy": self.model_policy.to_dict(),
            "budget": self.budget.to_dict(),
            "environment_fingerprint": dict(self.environment_fingerprint),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RunSpecV2":
        return cls(
            run_id=d["run_id"],
            query=d["query"],
            principal=Principal.from_dict(d.get("principal", {})),
            purpose_of_use=d.get("purpose_of_use", "historical_research"),
            task_type=d.get("task_type", "general_search"),
            execution_mode=d.get("execution_mode", "single"),
            output_genre=d.get("output_genre", "research_brief"),
            corpus_scope=CorpusScope(**(d.get("corpus_scope") or {})),
            completeness_requirement=d.get("completeness_requirement",
                                           "systematic"),
            evidence_policy=d.get("evidence_policy", "strict_claim_binding"),
            counterevidence_policy=d.get("counterevidence_policy",
                                         "mandatory"),
            human_review_policy=d.get("human_review_policy", "risk_based"),
            model_policy=ModelPolicy(**(d.get("model_policy") or {})),
            budget=BudgetSpec(**(d.get("budget") or {})),
            environment_fingerprint=d.get("environment_fingerprint", {}),
            created_at=d.get("created_at", ""))


def environment_fingerprint() -> Dict[str, str]:
    """環境指紋 V2：語料/工具/策略/技能/代碼/模型——replay 對比前提。"""
    import platform

    from hermes_shanghan.agent.harness.state import spec_versions

    from ..claims.policy_dsl import ConclusionPolicyEngine
    from ..tools.registry import TOOLS_V2_VERSION

    legacy = spec_versions()
    engine = ConclusionPolicyEngine()
    skills_fp = ""
    try:
        from ..skills import skills_fingerprint
        skills_fp = skills_fingerprint()
    except Exception:
        skills_fp = ""
    return {
        "corpus": legacy.get("corpus_version", ""),
        "tools": f"v2-{TOOLS_V2_VERSION}+legacy-"
                 f"{legacy.get('tool_spec_version', '')}",
        "policies": f"{engine.version}@{engine.fingerprint}",
        "skills": skills_fp,
        "code": legacy.get("code_tree_fingerprint", ""),
        "models": legacy.get("backend", "") or "deterministic",
        "python": platform.python_version(),
    }
