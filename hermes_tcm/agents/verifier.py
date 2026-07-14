"""Independent Verifier（Protocol §10.2 model_policy.verifier=independent）。

獨立核驗者：不使用產生主張的專家的任何中間結論，只基於台賬證據 +
策略引擎重新裁定每條主張。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from ..claims.policy_dsl import ConclusionPolicyEngine
from ..claims.records import ClaimRecord
from ..claims.verifier import ClaimVerifier
from ..evidence.coverage import SearchCoverage
from ..evidence.ledger import TypedEvidenceLedger


class IndependentVerifier:
    def __init__(self, ledger: TypedEvidenceLedger,
                 engine: Optional[ConclusionPolicyEngine] = None,
                 passage_index=None):
        self._verifier = ClaimVerifier(ledger, engine, passage_index)

    def verify(self, claims: Sequence[ClaimRecord],
               coverage: Optional[SearchCoverage] = None,
               tools_used: Sequence[str] = (),
               role: str = "researcher") -> Dict:
        """獨立複核有最終權威：專家自報結論一律作廢，以本結果為準。"""
        summary = self._verifier.verify_all(list(claims), coverage=coverage,
                                            tools_used=tools_used, role=role)
        summary["authority"] = "harness_independent_audit"
        return summary
