"""審批類型學（Protocol §10 / §14.4）。

普通 approve 只裁決**學術/臨床審核項**（adjudication）。證據失敗
不是「待裁決的爭議」而是「未完成的取證」：citation failure 永遠
不可被批准為正確（核心不變量 4）。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

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


# 具備人工審核資格的角色（Protocol §14.1）。臨床裁決另需 clinician。
REVIEWER_CAPABLE_ROLES: FrozenSet[str] = frozenset(
    {"editor", "clinician", "corpus_admin", "system_admin"})

# 特定 trigger 需要的最低審核角色（未列出者用默認 editor+）
TRIGGER_REQUIRED_ROLE: Dict[str, str] = {
    "clinical_recommendation_mandatory_review": "clinician",
    "identity_needs_review": "editor",
}

APPROVAL_TTL_S = 7 * 24 * 3600      # 審批請求默認有效期 7 天


def required_reviewer_role(trigger: str) -> str:
    return TRIGGER_REQUIRED_ROLE.get(trigger, "editor")


def build_approval_request(run_id: str, trigger: str,
                           action_digest: str = "",
                           evidence_digest: str = "",
                           policy_version: str = "",
                           tenant_id: str = "",
                           ttl_s: int = APPROVAL_TTL_S,
                           now_ts: Optional[float] = None) -> Dict:
    now = now_ts if now_ts is not None else time.time()
    return {"approval_id": f"{run_id}:{trigger}",
            "run_id": run_id,
            "node_id": "human_review",
            "trigger": trigger,
            "reason": HUMAN_REVIEW_TRIGGERS.get(trigger, ""),
            "action_digest": action_digest,
            "evidence_digest": evidence_digest,
            "policy_version": policy_version,
            "tenant_id": tenant_id,
            "requested_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "expires_at": now + ttl_s,
            "nonce": uuid.uuid4().hex,
            "required_role": required_reviewer_role(trigger),
            "approvable": approval_allowed(trigger)[0],
            "status": "pending"}


def verify_approval(request: Dict, trigger: str,
                    reviewer: Any = None,
                    current_action_digest: str = "",
                    now_ts: Optional[float] = None) -> Tuple[bool, str]:
    """審批不可偽造（P0-2）：逐項核驗後才允許消解該 trigger。

    reviewer：hermes_tcm.core.principals.Principal（服務端認證主體）；
    None 表示內部/測試調用——此時仍核驗 digest/過期/單次/可審批性，
    但不做角色/租戶檢查（由調用邊界另行保證）。返回 (ok, reason)。"""
    now = now_ts if now_ts is not None else time.time()
    # 1. 可審批性（citation_failure 等不可審批項一票否決）
    ok, why = approval_allowed(trigger)
    if not ok:
        return False, why
    # 2. 請求必須存在且待決（單次使用：已 approved/rejected 不可再批）
    if request is None:
        return False, f"無此審批請求：{trigger}（未進入人工審核隊列）"
    if request.get("status") != "pending":
        return False, (f"審批請求非待決狀態（{request.get('status')}）"
                       "——單次使用，不可重複批准")
    # 3. 有效期
    exp = request.get("expires_at")
    if exp is not None and now > exp:
        return False, "審批請求已過期——須重新發起審核"
    # 4. 審批對象一致性：回答在請求後被修改則審批對象過期
    if current_action_digest and request.get("action_digest") \
            and current_action_digest != request["action_digest"]:
        return False, "回答已變更（action_digest 不符）——審批對象過期"
    # 5. 審核人角色/租戶（提供 reviewer 時強制）
    if reviewer is not None:
        role = getattr(reviewer, "role", "")
        tenant = getattr(reviewer, "tenant_id", "")
        need = request.get("required_role", "editor")
        from ..core.auth import ROLE_RANK
        if role not in REVIEWER_CAPABLE_ROLES:
            return False, (f"審核人角色 {role} 不具備人工審核資格"
                           f"（需 {sorted(REVIEWER_CAPABLE_ROLES)}）")
        if ROLE_RANK.get(role, -1) < ROLE_RANK.get(need, 99):
            return False, f"審核角色不足：需 {need}，實 {role}"
        req_tenant = request.get("tenant_id", "")
        if req_tenant and tenant and req_tenant != tenant:
            return False, (f"跨租戶審批被拒：請求屬 {req_tenant}，"
                           f"審核人屬 {tenant}")
    return True, "approved"
