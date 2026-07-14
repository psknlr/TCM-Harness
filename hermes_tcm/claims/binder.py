"""Citation Binder（Protocol §17 claims/binder.py，§10 citation_bind 節點）。

把已核驗主張綁定到證據引用：主張文本後附〔ev_…〕標記（沿襲
〔psg_…〕可點擊約定），並生成帶 resource URI 的引用清單。
failed 主張不綁定、不出現。
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from ..envelope import evidence_entry
from ..evidence.ledger import TypedEvidenceLedger
from .records import ClaimRecord


def bind_citations(draft: str, claims: Sequence[ClaimRecord],
                   ledger: TypedEvidenceLedger
                   ) -> Tuple[str, List[Dict]]:
    """(draft, claims, ledger) → (bound_answer, citations)。

    只綁定台賬內證據（台賬外 id 不會出現在 citations——它們在
    claim_verify 已把主張判 failed）。"""
    citations: List[Dict] = []
    for c in claims:
        if c.status == "failed":
            continue
        for eid in c.supporting_evidence:
            rec = ledger.get(eid)
            if rec is not None:
                citations.append({"claim_id": c.claim_id,
                                  **evidence_entry(rec.to_dict())})
    bound = draft
    for c in claims:
        if c.status == "failed" or not c.supporting_evidence:
            continue
        tag = "〔" + "、".join(c.supporting_evidence[:3]) + "〕"
        if c.claim_text in bound:
            bound = bound.replace(c.claim_text, c.claim_text + tag, 1)
    return bound, citations
