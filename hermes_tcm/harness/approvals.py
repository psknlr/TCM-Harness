"""審批類型學（Protocol §10 / §14.4）。

普通 approve 只裁決**學術/臨床審核項**（adjudication）。證據失敗
不是「待裁決的爭議」而是「未完成的取證」：citation failure 永遠
不可被批准為正確（核心不變量 4）。
"""
from __future__ import annotations

import time
from typing import Dict, FrozenSet, List, Tuple

HUMAN_REVIEW_TRIGGERS: Dict[str, str] = {
    "clinical_recommendation_mandatory_review":
        "臨床建議：古籍原文不足以支持，必須專家人工審核",
    "earlier_partial_candidate":
        "存在更早部分匹配候選——首見結論須人工核驗",
    "low_ocr_confidence": "OCR 置信度不足——需影像人工核查",
    "uncertain_work_date": "著作年代存疑——年代排序結論需人工裁決",
    "identity_needs_review": "同名異書身份未裁決",
    "semantic_support_review": "主張與證據的語義支持需人工確認",
    "citation_failure":
        "引用未能全部核驗——須補錄證據/刪除無據結論後重跑，"
        "普通批准不能豁免",
}

ADJUDICATION_TRIGGERS: FrozenSet[str] = frozenset({
    "clinical_recommendation_mandatory_review",
    "earlier_partial_candidate",
    "low_ocr_confidence",
    "uncertain_work_date",
    "identity_needs_review",
    "semantic_support_review",
})

NON_APPROVABLE_TRIGGERS: FrozenSet[str] = frozenset({"citation_failure"})


def approval_allowed(trigger: str) -> Tuple[bool, str]:
    if trigger in NON_APPROVABLE_TRIGGERS:
        return False, ("證據失敗不可經普通批准豁免——"
                       "需補證據後重跑（無證據鏈，不成回答）")
    if trigger in ADJUDICATION_TRIGGERS:
        return True, HUMAN_REVIEW_TRIGGERS.get(trigger, "")
    return False, f"未知審批觸發鍵 {trigger!r}（fail-closed）"


def build_approval_request(run_id: str, trigger: str,
                           action_digest: str = "",
                           evidence_digest: str = "") -> Dict:
    return {"approval_id": f"{run_id}:{trigger}",
            "run_id": run_id,
            "node_id": "human_review",
            "trigger": trigger,
            "reason": HUMAN_REVIEW_TRIGGERS.get(trigger, ""),
            "action_digest": action_digest,
            "evidence_digest": evidence_digest,
            "requested_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "required_role": "human_reviewer",
            "approvable": approval_allowed(trigger)[0],
            "status": "pending"}
