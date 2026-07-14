"""專業子代理：按研究操作設定角色（Protocol §11.1–11.3）。

| 角色                     | 職責                          | 工具範圍 |
| Catalog Resolver        | 書名/作者/年代/同名異書消歧      | catalog  |
| Passage Retriever       | 精確/異體/結構化檢索            | text     |
| Collation Specialist    | 傳本對齊/異文分類               | collation|
| Chronology Specialist   | 首見/傳播/轉引/年代邊界          | citation |
| Concept Historian       | 術語語義/概念漂移               | concept  |
| Formula/Herb Specialist | 方劑/藥物/劑量譜系              | formula/herb |
| Counterevidence Critic  | 主動反例/早期候選/衝突材料       | citation/text |
| Claim Verifier          | 逐主張核驗                     | （無工具，只讀包）|
| Synthesizer             | 只基於已驗證 Evidence Packet 綜合| （無工具）|

獨立性要求（§11.2）：每個專家接收獨立 Evidence Packet，不讀取彼此
結論——各自形成 claims 後由 Synthesizer 看到各方結構化結果（防止
「多智能體合議」變成後一個複製前一個）。

並行安全（§11.3）：PARALLEL_SAFETY 顯式聲明哪些操作可並行、哪些必須
串行（同一台賬寫入/同一身份裁決/同一校勘條目/高風險臨床發布）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from ..claims.compiler import ClaimCompiler
from ..claims.records import ClaimRecord
from ..evidence.packets import EvidencePacket

SPECIALIST_ROLES: Dict[str, Dict] = {
    "catalog_resolver": {
        "duty": "書名、作者、年代、版本、同名異書消歧",
        "tool_scope": ["catalog"]},
    "passage_retriever": {
        "duty": "精確、異體、模糊和結構化檢索",
        "tool_scope": ["text"]},
    "collation_specialist": {
        "duty": "傳本對齊、異文分類和校勘",
        "tool_scope": ["collation"]},
    "chronology_specialist": {
        "duty": "首見、傳播、轉引和年代邊界",
        "tool_scope": ["citation"]},
    "concept_historian": {
        "duty": "術語語義、上下文和概念漂移",
        "tool_scope": ["concept"]},
    "formula_herb_specialist": {
        "duty": "方劑、藥物、劑量和異名譜系",
        "tool_scope": ["formula", "herb"]},
    "counterevidence_critic": {
        "duty": "主動搜索反例、早期候選和衝突材料",
        "tool_scope": ["citation", "text"]},
    "claim_verifier": {
        "duty": "逐主張核驗引用、歸屬和語義支持",
        "tool_scope": []},
    "synthesizer": {
        "duty": "只基於已驗證 Evidence Packet 綜合",
        "tool_scope": []},
}

# 並行安全表（Protocol §11.3）
PARALLEL_SAFETY = {
    "parallel_ok": (
        "per_dynasty_retrieval", "per_category_retrieval",
        "witness_alignment", "support_and_counter_search",
        "independent_interpretation_review"),
    "serial_only": (
        "evidence_ledger_write",       # 同一台賬寫入（Broker 序列化）
        "work_identity_adjudication",  # 同一文獻身份最終裁決
        "collation_entry_edit",        # 同一校勘條目編輯
        "clinical_release"),           # 高風險臨床結論發布
}


@dataclass
class SpecialistReport:
    """專家結構化輸出：claims + 所依據的 packet id（不含推理過程——
    子代理在隔離上下文內工作，返回壓縮結果）。"""
    role: str
    packet_id: str
    claims: List[ClaimRecord] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> Dict:
        return {"role": self.role, "packet_id": self.packet_id,
                "claims": [c.to_dict() for c in self.claims],
                "notes": self.notes}


class SpecialistAgent:
    """確定性專家：對自己的獨立 Evidence Packet 形成 claims。

    當前實現為模板化編譯（deterministic planner）；LLM 專家可注入
    但工具/預算/證據範圍仍受 Harness 控制。"""

    def __init__(self, role: str):
        if role not in SPECIALIST_ROLES:
            raise ValueError(f"未知專家角色 {role!r}"
                             f"（可用：{sorted(SPECIALIST_ROLES)}）")
        self.role = role
        self.tool_scope = SPECIALIST_ROLES[role]["tool_scope"]

    def analyze(self, packet: EvidencePacket, task_type: str,
                topic: str) -> SpecialistReport:
        compiler = ClaimCompiler()
        role_task = {
            "chronology_specialist": "earliest_attestation",
            "collation_specialist": "witness_comparison",
            "counterevidence_critic": "negative_result",
        }.get(self.role, task_type)
        claims = compiler.compile(role_task, packet, topic=topic)
        return SpecialistReport(role=self.role,
                                packet_id=packet.packet_id,
                                claims=claims,
                                notes=SPECIALIST_ROLES[self.role]["duty"])


def dispatch_specialists(roles: Sequence[str],
                         packets: Dict[str, EvidencePacket],
                         task_type: str, topic: str,
                         budget=None) -> List[SpecialistReport]:
    """派發專家：每個角色一個**獨立** packet（不共享、不互讀）。

    packets 鍵為角色名；缺包的角色如實跳過（不偷看他人證據）。
    budget.reserve_subagent 控制專家數量上限。"""
    reports: List[SpecialistReport] = []
    for role in roles:
        packet = packets.get(role)
        if packet is None:
            continue
        if budget is not None and not budget.reserve_subagent(role):
            break
        reports.append(SpecialistAgent(role).analyze(packet, task_type,
                                                     topic))
    return reports


def cross_review(reports: List[SpecialistReport]) -> List[Dict]:
    """匿名交叉審查：專家間主張衝突檢測（同 claim_type 不同結論）。

    輸出衝突清單供 Synthesizer/human_review 使用；審查是匿名的
    （只看主張結構，不看角色身份），防止權威偏倚。"""
    conflicts: List[Dict] = []
    by_type: Dict[str, List] = {}
    for i, rep in enumerate(reports):
        for c in rep.claims:
            by_type.setdefault(c.claim_type, []).append((i, c))
    for claim_type, entries in by_type.items():
        if len(entries) < 2:
            continue
        texts = {c.claim_text for _, c in entries}
        if len(texts) > 1:
            conflicts.append({
                "claim_type": claim_type,
                "n_reviewers": len(entries),
                "divergent_texts": sorted(texts)[:4],
                "note": "匿名交叉審查發現分歧——進入 human_review"})
    return conflicts
