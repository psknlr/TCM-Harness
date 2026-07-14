"""目的限制與寫入審批策略（Protocol §14.2、§14.4）。

默認只讀；寫入按操作分級審批。學術古籍研究接口與患者處方接口
**不共享**同一釋放策略（必須避免的錯誤之七）。
"""
from __future__ import annotations

from typing import Dict, FrozenSet, Tuple

# purpose_of_use → 禁止的輸出能力（capability 標籤由釋放閘門檢查）
PURPOSE_POLICY: Dict[str, Dict] = {
    "historical_research": {
        "forbidden_capabilities": frozenset(),
        "note": "研究用途：可見古代劑量原文，不得表述為現代可執行處方"},
    "teaching": {
        "forbidden_capabilities": frozenset({"modern_prescription"}),
        "note": "教學用途：不輸出現代可執行處方"},
    "textual_criticism": {
        "forbidden_capabilities": frozenset(),
        "note": "校勘用途"},
    "publication": {
        "forbidden_capabilities": frozenset(),
        "note": "發表用途：導出包須帶版本化引用"},
    "clinical_reference": {
        "forbidden_capabilities": frozenset(),
        "note": "臨床參考：僅 clinician 角色可用；仍須人工審核"},
    "patient_education": {
        "forbidden_capabilities": frozenset(
            {"modern_prescription", "dosage_conversion",
             "formula_recommendation", "diagnosis"}),
        "note": "患者教育：禁止處方/劑量換算/方劑推薦/診斷輸出"},
    "corpus_maintenance": {
        "forbidden_capabilities": frozenset(),
        "note": "語料維護：寫操作仍須逐級審批"},
}

# purpose → 允許的角色（空=全部角色）
PURPOSE_ROLE_RESTRICTION: Dict[str, FrozenSet[str]] = {
    "clinical_reference": frozenset({"clinician"}),
    "corpus_maintenance": frozenset({"corpus_admin", "system_admin"}),
}


def purpose_allows(purpose: str, capability: str, role: str = "") -> Tuple[bool, str]:
    """(是否允許, 理由)。未知目的 fail-closed。"""
    policy = PURPOSE_POLICY.get(purpose)
    if policy is None:
        return False, f"未知使用目的 {purpose!r}（fail-closed）"
    restrict = PURPOSE_ROLE_RESTRICTION.get(purpose)
    if restrict is not None and role and role not in restrict:
        return False, (f"目的 {purpose} 僅限角色 {sorted(restrict)}"
                       f"（當前 {role}）")
    if capability in policy["forbidden_capabilities"]:
        return False, f"目的 {purpose} 禁止能力 {capability}：{policy['note']}"
    return True, policy["note"]


# ---------------------------------------------------------------------------
# 寫操作審批分級（Protocol §14.4：默認只讀，寫入需要審批）
# ---------------------------------------------------------------------------
WRITE_APPROVAL_LEVELS: Dict[str, str] = {
    "read":                      "auto",           # 檢索和閱讀：自動
    "private_annotation":        "auto_or_prompt",  # 新建私人批注
    "public_metadata_edit":      "single_approval",  # 修改公共元數據
    "work_witness_merge":        "dual_approval",    # 合併 Work/Witness
    "collation_publication":     "expert_approval",  # 發布校勘結論
    "corpus_delete_or_overwrite": "forbidden",       # 刪除語料/覆蓋原始文件
}


def write_approval_required(operation: str) -> str:
    """未知寫操作 fail-closed 為 forbidden。"""
    return WRITE_APPROVAL_LEVELS.get(operation, "forbidden")
