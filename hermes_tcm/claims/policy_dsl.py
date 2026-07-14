"""Conclusion Policy DSL（Protocol §8.2，P0-5）。

把「最早提出必須時間有序檢索+反證搜索」「普遍認為必須 ≥3 部不同著作」
從硬編碼正則升級為**可版本化的策略引擎**：策略是數據（可從 JSON 加載、
可導出、可指紋），引擎對 ClaimRecord + EvidencePacket + SearchCoverage
做確定性裁定。

策略字段（與 Protocol §8.2 YAML 對應）：

    minimum_tools            必須出現在調用台賬中的工具
    minimum_evidence         distinct_works / distinct_authors /
                             distinct_periods / verification_level
    coverage                 require_time_ordered / require_counter_search /
                             forbid_when_earlier_partial_candidate
    output.force_qualifier   強制限定語（如「在當前語料庫範圍內」）
    human_review_when        觸發人工審核的條件
    allowed_roles            角色限制（臨床建議僅 clinician）
    forbid_frequency_only    頻次證據不得單獨支持語義結論
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence

from ..core.schemas import verification_at_least
from ..evidence.coverage import SearchCoverage, earliest_claim_allowed
from ..evidence.records import EvidenceRecord
from .records import ClaimRecord

POLICY_VERSION = "conclusion-policy-2026.07.1"

DEFAULT_POLICIES: Dict[str, Dict[str, Any]] = {
    "earliest_attestation": {
        # 字符串=必需；列表=任選其一（短術語的反證由異體變形時間線
        # citation.trace_term 承擔，長引文由截半探針 counter_search 承擔）
        "minimum_tools": [["citation.trace_quote", "citation.trace_term"],
                          ["citation.counter_search",
                           "citation.trace_term"]],
        "minimum_evidence": {"distinct_works": 1,
                             "verification_level": "V2"},
        "coverage": {"require_time_ordered": True,
                     "require_counter_search": True,
                     "forbid_when_earlier_partial_candidate": True},
        "output": {"force_qualifier": "在當前語料庫範圍內"},
        "human_review_when": ["earlier_partial_candidate",
                              "low_ocr_confidence", "uncertain_work_date"],
    },
    "attestation": {
        "minimum_tools": [],
        "minimum_evidence": {"distinct_works": 1,
                             "verification_level": "V1"},
        "coverage": {},
        "output": {},
        "human_review_when": [],
    },
    "broad_consensus": {
        "minimum_tools": [],
        "minimum_evidence": {"distinct_works": 3, "distinct_authors": 3,
                             "distinct_periods": 2,
                             "verification_level": "V1"},
        "coverage": {"require_counter_search": True},
        "output": {"force_qualifier": "就已檢得文獻而言"},
        "forbid_frequency_only": True,
        "human_review_when": [],
    },
    "variant_reading": {
        "minimum_tools": [],
        "minimum_evidence": {"distinct_works": 2,
                             "verification_level": "V1"},
        "coverage": {},
        "output": {},
        "human_review_when": ["uncertain_witness_identity"],
    },
    "quotation_relay": {
        "minimum_tools": [],
        "minimum_evidence": {"distinct_works": 2,
                             "verification_level": "V2"},
        "coverage": {"require_time_ordered": True},
        "output": {},
        "human_review_when": [],
    },
    "semantic_drift": {
        "minimum_tools": [],
        "minimum_evidence": {"distinct_works": 2, "distinct_periods": 2,
                             "verification_level": "V1"},
        "coverage": {},
        "output": {"force_qualifier": "以段落用例為據"},
        # 必須避免的錯誤之四：詞頻變化不得直接推斷語義變化
        "forbid_frequency_only": True,
        "human_review_when": [],
    },
    "formula_lineage": {
        "minimum_tools": [],
        "minimum_evidence": {"distinct_works": 2,
                             "verification_level": "V1"},
        "coverage": {"require_time_ordered": True},
        "output": {},
        "human_review_when": [],
    },
    "negative_result": {
        "minimum_tools": [],
        "minimum_evidence": {},
        # 必須避免的錯誤之五：負結論必須綁定覆蓋範圍
        "coverage": {"require_coverage_bound": True},
        "output": {},
        "human_review_when": [],
    },
    "synthesis": {
        "minimum_tools": [],
        "minimum_evidence": {"distinct_works": 1,
                             "verification_level": "V1"},
        "coverage": {},
        "output": {"force_qualifier": "綜合推斷"},
        "human_review_when": [],
    },
    "clinical_recommendation": {
        "minimum_tools": [],
        "minimum_evidence": {"verification_level": "V2"},
        "coverage": {},
        "output": {},
        "allowed_roles": ["clinician"],
        "ancient_text_only_is_insufficient": True,
        "human_review": "mandatory",
        "human_review_when": ["always"],
    },
}


def policy_fingerprint(policies: Dict[str, Dict]) -> str:
    blob = json.dumps(policies, ensure_ascii=False, sort_keys=True,
                      default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


class ConclusionPolicyEngine:
    """對單個 ClaimRecord 的策略裁定（確定性、可版本化）。

    verdict：pass / fail / review_required——fail 不可審批豁免
    （證據不足不是「待裁決的爭議」而是「未完成的取證」）。
    """

    def __init__(self, policies: Optional[Dict[str, Dict]] = None,
                 version: str = POLICY_VERSION):
        self.policies = policies or DEFAULT_POLICIES
        self.version = version
        self.fingerprint = policy_fingerprint(self.policies)

    @classmethod
    def from_json(cls, path) -> "ConclusionPolicyEngine":
        data = json.loads(open(path, encoding="utf-8").read())
        return cls(policies=data.get("policies") or data,
                   version=data.get("policy_version", POLICY_VERSION))

    def to_json(self) -> str:
        return json.dumps({"policy_version": self.version,
                           "fingerprint": self.fingerprint,
                           "policies": self.policies},
                          ensure_ascii=False, indent=1)

    # ------------------------------------------------------------------
    def evaluate(self, claim: ClaimRecord,
                 evidence: Sequence[EvidenceRecord],
                 coverage: Optional[SearchCoverage] = None,
                 tools_used: Sequence[str] = (),
                 role: str = "researcher") -> Dict[str, Any]:
        policy = self.policies.get(claim.claim_type)
        if policy is None:
            return {"verdict": "fail", "policy_id": claim.claim_type,
                    "violations": [f"無策略定義的 claim_type "
                                   f"{claim.claim_type}（fail-closed）"]}
        violations: List[str] = []
        review: List[str] = []
        qualifiers: List[str] = []
        ev = [e for e in evidence
              if e.evidence_id in set(claim.supporting_evidence)]

        # 1. 角色限制
        allowed_roles = policy.get("allowed_roles")
        if allowed_roles and role not in allowed_roles:
            violations.append(f"claim_type={claim.claim_type} 僅限角色 "
                              f"{allowed_roles}（當前 {role}）")

        # 2. 最低工具（字符串=必需；列表=任選其一）
        used = set(tools_used or ())
        for t in policy.get("minimum_tools", []):
            if isinstance(t, (list, tuple)):
                if not (set(t) & used):
                    violations.append(
                        f"缺少必需工具調用（任選其一）：{'/'.join(t)}")
            elif t not in used:
                violations.append(f"缺少必需工具調用：{t}")

        # 3. 最低證據
        need = policy.get("minimum_evidence", {})
        if need:
            works = {e.work_id for e in ev if e.work_id}
            authors = {e.author for e in ev if e.author}
            periods = {e.dynasty for e in ev if e.dynasty}
            if len(works) < need.get("distinct_works", 0):
                violations.append(
                    f"支持證據著作數 {len(works)} < "
                    f"{need['distinct_works']}（distinct_works）")
            if len(authors) < need.get("distinct_authors", 0):
                violations.append(
                    f"支持證據作者數 {len(authors)} < "
                    f"{need['distinct_authors']}（distinct_authors）")
            if len(periods) < need.get("distinct_periods", 0):
                violations.append(
                    f"支持證據時代數 {len(periods)} < "
                    f"{need['distinct_periods']}（distinct_periods）")
            min_level = need.get("verification_level")
            if min_level:
                weak = [e.evidence_id for e in ev
                        if not verification_at_least(e.verification_level,
                                                     min_level)]
                if not ev or weak:
                    violations.append(
                        f"證據核驗等級不足（需 {min_level}）："
                        + ("無任何支持證據" if not ev
                           else "、".join(weak[:5])))

        # 4. 覆蓋要求
        cov_req = policy.get("coverage", {})
        if cov_req:
            if cov_req.get("require_coverage_bound") and coverage is None:
                violations.append("負結論必須綁定 SearchCoverage"
                                  "（禁止裸負結論）")
            if cov_req.get("require_counter_search") \
                    and not claim.counter_search_performed:
                violations.append("未執行反證搜索（counter_search 義務）")
            if cov_req.get("require_time_ordered") and coverage is not None \
                    and "dynasty_ordered" not in (coverage.search_modes or []):
                violations.append("覆蓋記錄未聲明時間有序檢索"
                                  "（search_modes 缺 dynasty_ordered）")
            if cov_req.get("forbid_when_earlier_partial_candidate") \
                    and coverage is not None:
                gate = earliest_claim_allowed(coverage)
                if not gate["allowed"]:
                    violations.append(gate["reason"])
                elif gate.get("forced_qualifier"):
                    qualifiers.append(gate["forced_qualifier"])

        # 5. 頻次證據限制（必須避免的錯誤之四）
        if policy.get("forbid_frequency_only") and ev:
            non_freq = [e for e in ev if e.epistemic_status in
                        ("verbatim", "editorial_alignment")]
            if not non_freq:
                violations.append("僅有頻次/統計類證據——頻次漂移≠語義"
                                  "結論，需段落級逐字證據")

        # 6. 臨床特別條款
        if policy.get("ancient_text_only_is_insufficient") and not violations:
            review.append("clinical_ancient_text_only：古籍原文不足以"
                          "支持臨床建議，必須人工審核")

        # 7. 強制限定語
        fq = policy.get("output", {}).get("force_qualifier")
        if fq:
            qualifiers.append(fq)

        # 8. 人工審核條件
        if policy.get("human_review") == "mandatory" \
                or "always" in policy.get("human_review_when", []):
            review.append(f"{claim.claim_type}_mandatory_review")
        if coverage is not None:
            cond = set(policy.get("human_review_when", []))
            if "earlier_partial_candidate" in cond \
                    and coverage.earlier_partial_candidates > 0:
                review.append("earlier_partial_candidate")
            if "low_ocr_confidence" in cond and coverage.low_ocr_quality:
                review.append("low_ocr_confidence")

        verdict = ("fail" if violations
                   else ("review_required" if review else "pass"))
        return {"verdict": verdict,
                "policy_id": claim.claim_type,
                "policy_version": self.version,
                "policy_fingerprint": self.fingerprint,
                "violations": violations,
                "review_required": sorted(set(review)),
                "forced_qualifiers": qualifiers}
