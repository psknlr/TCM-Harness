"""Claim Compiler：從證據包編譯結構化主張（Protocol §10 claim_compile 節點）。

確定性編譯：不讓 LLM 先寫 prose 再倒推引用，而是由任務類型 + 證據包
直接產出 ClaimRecord 草稿（claim_text 是模板化陳述，Synthesizer 之後
只能基於已驗證主張改寫表達，不得增添新事實）。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from ..evidence.coverage import SearchCoverage, negative_statement
from ..evidence.packets import EvidencePacket
from ..evidence.records import EvidenceRecord
from .records import ClaimRecord, claim_id_for


class ClaimCompiler:
    """task_type + EvidencePacket → ClaimRecord[]（draft 狀態）。"""

    def compile(self, task_type: str, packet: EvidencePacket,
                topic: str = "",
                counter_search_performed: bool = False) -> List[ClaimRecord]:
        recs = [r for r in packet.records if r.is_primary_text_returned]
        cov = packet.coverage
        if task_type == "earliest_attestation":
            return self._earliest(topic, recs, cov, counter_search_performed)
        if task_type == "broad_consensus":
            return self._consensus(topic, recs, cov, counter_search_performed)
        if task_type == "witness_comparison":
            return self._variants(topic, recs, cov)
        if task_type == "negative_result":
            # 反證評論員命中時應編譯 attestation（有原文=證明存在），
            # 只有真的零命中才是負結論——不能無條件斷言「未見」
            return (self._attestations(topic, recs, cov) if recs
                    else self._negative(topic, cov))
        # 默認：每條正文證據一條 attestation 主張
        return self._attestations(topic, recs, cov)

    # ------------------------------------------------------------------
    def _bind(self, claim: ClaimRecord, cov: Optional[SearchCoverage]
              ) -> ClaimRecord:
        if cov is not None:
            claim.scope_id = cov.coverage_id
        return claim

    def _earliest(self, topic: str, recs: Sequence[EvidenceRecord],
                  cov: Optional[SearchCoverage],
                  counter_done: bool) -> List[ClaimRecord]:
        if not recs:
            return self._negative(topic, cov)
        # 顯式時間排序（不信任登記順序）：朝代序 + 檢索排名；
        # 無朝代著作 UNRANKED 排最後，永遠不能贏得首現
        from hermes_shanghan.classics.model import UNRANKED, dynasty_rank
        first = min(recs, key=lambda r: (dynasty_rank(r.dynasty),
                                         r.retrieval_rank))
        if dynasty_rank(first.dynasty) >= UNRANKED:
            # 全部候選無朝代——無時間信息可據，不得斷言首現，
            # 降級為普通 attestation 主張（守住「UNRANKED 不能贏首現」）
            return self._attestations(topic, recs, cov)
        text = (f"「{topic}」在本庫時間有序檢索中最早見於"
                f"《{first.work_title}》（{first.dynasty or '年代未詳'}）")
        claim = ClaimRecord(
            claim_id=claim_id_for(text, "earliest_attestation"),
            claim_text=text,
            claim_type="earliest_attestation",
            epistemic_status="bounded_inference",
            supporting_evidence=[first.evidence_id],
            counter_search_performed=counter_done,
            notes="在庫首現≠歷史首現（bounded inference）")
        # 覆蓋綁定優先取產生該證據的檢索覆蓋（語義最準）
        if first.coverage_id:
            claim.scope_id = first.coverage_id
            return [claim]
        return [self._bind(claim, cov)]

    def _consensus(self, topic: str, recs: Sequence[EvidenceRecord],
                   cov: Optional[SearchCoverage],
                   counter_done: bool) -> List[ClaimRecord]:
        works = sorted({r.work_title for r in recs if r.work_title})
        text = (f"「{topic}」見於 {len(works)} 部著作的討論"
                f"（{'、'.join(works[:5])}{'等' if len(works) > 5 else ''}）")
        claim = ClaimRecord(
            claim_id=claim_id_for(text, "broad_consensus"),
            claim_text=text,
            claim_type="broad_consensus",
            epistemic_status="synthesis",
            supporting_evidence=[r.evidence_id for r in recs],
            counter_search_performed=counter_done)
        return [self._bind(claim, cov)]

    def _variants(self, topic: str, recs: Sequence[EvidenceRecord],
                  cov: Optional[SearchCoverage]) -> List[ClaimRecord]:
        out: List[ClaimRecord] = []
        by_witness: Dict[str, List[EvidenceRecord]] = {}
        for r in recs:
            by_witness.setdefault(r.witness_id, []).append(r)
        if len(by_witness) < 2:
            return self._attestations(topic, recs, cov)
        text = (f"「{topic}」在 {len(by_witness)} 個傳本中存在對照段落")
        claim = ClaimRecord(
            claim_id=claim_id_for(text, "variant_reading"),
            claim_text=text,
            claim_type="variant_reading",
            epistemic_status="editorial_alignment",
            supporting_evidence=[r.evidence_id for r in recs])
        out.append(self._bind(claim, cov))
        return out

    def _negative(self, topic: str,
                  cov: Optional[SearchCoverage]) -> List[ClaimRecord]:
        if cov is None:
            # 禁止裸負結論：無覆蓋記錄時產出的主張必然被策略引擎 fail
            stmt = ""
        else:
            stmt = negative_statement(cov).get("statement", "")
        fallback = "檢索未命中（覆蓋範圍未定義，本主張不可發布）"
        text = f"「{topic}」{stmt or fallback}"
        claim = ClaimRecord(
            claim_id=claim_id_for(text, "negative_result"),
            claim_text=text,
            claim_type="negative_result",
            epistemic_status="bounded_inference",
            supporting_evidence=[])
        return [self._bind(claim, cov)]

    def _attestations(self, topic: str, recs: Sequence[EvidenceRecord],
                      cov: Optional[SearchCoverage]) -> List[ClaimRecord]:
        out: List[ClaimRecord] = []
        for r in recs:
            text = (f"《{r.work_title}》{r.section or ''}載："
                    f"「{r.verbatim[:40]}…」" if len(r.verbatim) > 40
                    else f"《{r.work_title}》{r.section or ''}載：「{r.verbatim}」")
            claim = ClaimRecord(
                claim_id=claim_id_for(text, "attestation"),
                claim_text=text,
                claim_type="attestation",
                epistemic_status="verbatim",
                supporting_evidence=[r.evidence_id])
            out.append(self._bind(claim, cov))
        return out
