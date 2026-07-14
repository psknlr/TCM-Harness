"""通用證據模型的五個正交維度（Protocol §6.1）。

單一字母層級（A/B/C/D/E/P）在《傷寒論》專題內清晰，但擴展到全部古籍
後把「文獻類型 / 與基準文本的關係 / 證據強度 / 推斷程度 / 任務中的
證據角色」混在了一起。本模塊把它拆成五個正交維度，並保留 A/B/C/D/E/P
作為**兼容視圖**（不再是主模型）。
"""
from __future__ import annotations

from typing import Dict, Tuple

# ---------------------------------------------------------------------------
# 維度一：source_role —— 文獻在證據中的來源類型
# ---------------------------------------------------------------------------
SOURCE_ROLES: Tuple[str, ...] = (
    "primary_text",        # 一手文本（本任務的基準原文）
    "commentary",          # 注釋/疏解
    "case_record",         # 醫案
    "formula_book",        # 方書
    "materia_medica",      # 本草
    "compilation",         # 類書/叢書/後世彙編
    "later_synthesis",     # 後世歸納性著作
)

# ---------------------------------------------------------------------------
# 維度二：witness_role —— 傳本與基準文本的關係
# ---------------------------------------------------------------------------
WITNESS_ROLES: Tuple[str, ...] = (
    "base_witness",        # 底本
    "variant_witness",     # 異文傳本
    "quotation_witness",   # 他書引錄（轉引見證）
    "modern_edition",      # 現代整理本
)

# ---------------------------------------------------------------------------
# 維度三：epistemic_status —— 認識論狀態（推斷程度）
# ---------------------------------------------------------------------------
EPISTEMIC_STATUSES: Tuple[str, ...] = (
    "verbatim",             # 逐字摘錄（座標+hash 可重驗）
    "editorial_alignment",  # 整理性對齊（如條文-注文對齊）
    "source_assertion",     # 文獻自身的斷言（某書聲稱……）
    "synthesis",            # 跨源綜合
    "model_hypothesis",     # 模型推理假設
    "bounded_inference",    # 有界推斷（如「在庫首現」）
)

# ---------------------------------------------------------------------------
# 維度四：verification_level —— 核驗等級
# ---------------------------------------------------------------------------
VERIFICATION_LEVELS: Tuple[str, ...] = (
    "V0",   # 僅元數據（書目命中，正文未回）
    "V1",   # 逐字重驗通過（verbatim + 座標 + quote_hash）
    "V2",   # V1 + 文獻身份歸屬核驗（work/witness 鏈完整）
    "V3",   # V2 + 語義支持核驗（主張確被段落支持）
    "V4",   # V3 + 專家人工裁決
)

VERIFICATION_ORDER: Dict[str, int] = {v: i for i, v in
                                      enumerate(VERIFICATION_LEVELS)}


def verification_at_least(level: str, minimum: str) -> bool:
    """核驗等級偏序比較；未知等級一律視為不滿足（fail-closed）。"""
    lo = VERIFICATION_ORDER.get(minimum)
    got = VERIFICATION_ORDER.get(level)
    if lo is None or got is None:
        return False
    return got >= lo


# ---------------------------------------------------------------------------
# 維度五：claim_risk —— 結論風險類型
# ---------------------------------------------------------------------------
CLAIM_RISKS: Tuple[str, ...] = (
    "descriptive",     # 描述性（某書某段寫了什麼）
    "chronological",   # 年代性（首見/傳播/先後）
    "consensus",       # 共識性（普遍認為/多數注家）
    "causal",          # 因果性（甲導致乙/甲源出乙）
    "clinical",        # 臨床性（涉及診療/劑量）
)

# ---------------------------------------------------------------------------
# A/B/C/D/E/P 兼容視圖（Protocol §6.1：原層級保留為映射，取消固定語義）
# ---------------------------------------------------------------------------
# legacy layer → (source_role, witness_role, epistemic_status)
LEGACY_LAYER_MAP: Dict[str, Dict[str, str]] = {
    "A": {"source_role": "primary_text", "witness_role": "base_witness",
          "epistemic_status": "verbatim"},
    "B": {"source_role": "primary_text", "witness_role": "variant_witness",
          "epistemic_status": "editorial_alignment"},
    "C": {"source_role": "commentary", "witness_role": "base_witness",
          "epistemic_status": "source_assertion"},
    "D": {"source_role": "later_synthesis", "witness_role": "base_witness",
          "epistemic_status": "synthesis"},
    "E": {"source_role": "later_synthesis", "witness_role": "base_witness",
          "epistemic_status": "model_hypothesis"},
    # P 取消固定語義：泛指段落證據，來源類型由編目分類另行判定
    "P": {"source_role": "compilation", "witness_role": "base_witness",
          "epistemic_status": "verbatim"},
}


def legacy_layer_to_roles(layer: str) -> Dict[str, str]:
    """A/B/C/D/E/P → 正交維度（未知層級 fail-closed 為最弱組合）。"""
    return dict(LEGACY_LAYER_MAP.get(
        (layer or "").strip().upper(),
        {"source_role": "compilation", "witness_role": "base_witness",
         "epistemic_status": "source_assertion"}))


def roles_to_legacy_layer(source_role: str, witness_role: str,
                          epistemic_status: str) -> str:
    """正交維度 → 最接近的 legacy 層（兼容視圖，供舊 UI/報表使用）。"""
    if epistemic_status == "model_hypothesis":
        return "E"
    if witness_role == "variant_witness":
        return "B"
    if source_role == "commentary":
        return "C"
    if source_role in ("later_synthesis",) or epistemic_status == "synthesis":
        return "D"
    if source_role == "primary_text" and witness_role == "base_witness" \
            and epistemic_status == "verbatim":
        return "A"
    return "P"


# 語料分類 → source_role 的確定性映射（笈成分類詞表；未知分類歸
# compilation，如實不猜）
CATEGORY_SOURCE_ROLE: Dict[str, str] = {
    "醫經": "primary_text",
    "傷寒": "primary_text",
    "金匱": "primary_text",
    "方書": "formula_book",
    "本草": "materia_medica",
    "醫案": "case_record",
    "醫話": "case_record",
    "注釋": "commentary",
    "綜合": "compilation",
    "類書": "compilation",
}


def category_to_source_role(category: str) -> str:
    cat = (category or "").strip()
    if cat in CATEGORY_SOURCE_ROLE:
        return CATEGORY_SOURCE_ROLE[cat]
    for key, role in CATEGORY_SOURCE_ROLE.items():
        if key in cat:
            return role
    return "compilation"
