"""Claim Verifier：逐主張核驗（Protocol §10 claim_verify 節點）。

四項核驗（與 ClaimRecord.verification 對應）：

    attribution      支持證據必須存在於台賬且身份鏈完整
    quotation        逐字重驗（quote_hash + 可選回庫切片）
    semantic_support 主張文本與證據的確定性支持檢查
    coverage         覆蓋範圍與策略引擎裁定

citation failure（quotation/attribution 不通過）不可被人工「批准為
正確」——這是核心不變量 4。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from ..evidence.coverage import SearchCoverage
from ..evidence.ledger import TypedEvidenceLedger
from ..evidence.packets import verify_packet
from .policy_dsl import ConclusionPolicyEngine
from .records import ClaimRecord


class ClaimVerifier:
    def __init__(self, ledger: TypedEvidenceLedger,
                 policy_engine: Optional[ConclusionPolicyEngine] = None,
                 passage_index=None):
        self.ledger = ledger
        self.engine = policy_engine or ConclusionPolicyEngine()
        self.passage_index = passage_index

    def verify(self, claim: ClaimRecord,
               coverage: Optional[SearchCoverage] = None,
               tools_used: Sequence[str] = (),
               role: str = "researcher") -> ClaimRecord:
        """就地核驗並回填 claim.verification / status / forced_qualifiers。"""
        result: Dict[str, Any] = {}

        # 1. attribution：支持證據必須在台賬（台賬外證據=偽造）
        missing = [eid for eid in claim.supporting_evidence
                   if self.ledger.get(eid) is None]
        ev = [self.ledger.get(eid) for eid in claim.supporting_evidence
              if self.ledger.get(eid) is not None]
        no_identity = [e.evidence_id for e in ev
                       if not (e.work_id and e.witness_id)]
        result["attribution"] = ("fail" if (missing or no_identity)
                                 else "pass")
        if missing:
            result["attribution_missing"] = missing
        if no_identity:
            result["attribution_no_identity"] = no_identity

        # 2. quotation：逐字重驗
        q = verify_packet(ev, self.passage_index)
        result["quotation"] = "pass" if q["ok"] else "fail"
        if not q["ok"]:
            result["quotation_failures"] = q["failures"]

        # 3. semantic_support：確定性下界檢查——正文型主張的證據摘錄
        #    必須出現在主張文本中（模板編譯保證），否則標 review
        if claim.claim_type == "attestation" and ev:
            # 空 work_title 的 '' in text 恆為真——必須顯式排除，否則
            # 語義支持核驗被空字段旁路
            supported = any(
                e.verbatim and (e.verbatim[:20] in claim.claim_text
                                or (e.work_title
                                    and e.work_title in claim.claim_text))
                for e in ev)
            result["semantic_support"] = "pass" if supported else "review"
        else:
            result["semantic_support"] = "pass" if (ev or claim.claim_type
                                                    == "negative_result") \
                else "review"

        # 3b. contradiction：登記在案的反對證據必須進人工複核
        contra = [self.ledger.get(eid)
                  for eid in claim.contradicting_evidence]
        result["contradiction"] = ("review"
                                   if any(e is not None for e in contra)
                                   else "pass")

        # 4. coverage + 策略引擎
        policy = self.engine.evaluate(claim, ev, coverage=coverage,
                                      tools_used=tools_used, role=role)
        result["coverage"] = ("pass" if policy["verdict"] != "fail"
                              else "fail")
        result["policy"] = policy

        claim.verification = result
        claim.policy_id = policy.get("policy_id", "")
        claim.policy_version = policy.get("policy_version", "")
        claim.forced_qualifiers = sorted(
            set(claim.forced_qualifiers)
            | set(policy.get("forced_qualifiers", [])))

        if result["attribution"] == "fail" or result["quotation"] == "fail" \
                or policy["verdict"] == "fail":
            claim.status = "failed"
        elif policy["verdict"] == "review_required" \
                or result["semantic_support"] == "review" \
                or result["contradiction"] == "review":
            claim.status = "needs_review"
        else:
            claim.status = "verified"
        return claim

    def verify_all(self, claims: List[ClaimRecord],
                   coverage: Optional[SearchCoverage] = None,
                   tools_used: Sequence[str] = (),
                   role: str = "researcher",
                   coverage_lookup: Optional[Dict[str, SearchCoverage]]
                   = None) -> Dict[str, Any]:
        """coverage_lookup：coverage_id → SearchCoverage；每主張優先用
        自己 scope_id 綁定的覆蓋（反證覆蓋與主檢索覆蓋不可混用）。"""
        for c in claims:
            cov = coverage
            if coverage_lookup and c.scope_id in coverage_lookup:
                cov = coverage_lookup[c.scope_id]
            self.verify(c, coverage=cov, tools_used=tools_used,
                        role=role)
        return {
            "n_claims": len(claims),
            "n_verified": sum(1 for c in claims if c.status == "verified"),
            "n_failed": sum(1 for c in claims if c.status == "failed"),
            "n_needs_review": sum(1 for c in claims
                                  if c.status == "needs_review"),
            "failed_claim_ids": [c.claim_id for c in claims
                                 if c.status == "failed"],
        }
