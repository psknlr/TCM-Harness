"""強類型證據台賬：只有 Capability Broker 可寫（Protocol §6.2，P0-4）。

`RunState.evidence_ledger: Dict[str, List[str]]` 的弱類型時代結束：
台賬持有 EvidenceRecord[]，寫入需要 Broker 持有的能力令牌——模塊外
拿不到令牌，直接 append 即拋 LedgerWriteViolation。

不變量（寫入期逐條執行）：

1. 只登記 registered_by == "capability_broker" 的記錄；
2. 每條記錄必須綁定 tool_call_id / span_id / corpus_version；
3. verbatim 與 quote_hash 構造期已互驗（EvidenceRecord.__post_init__）；
4. 台賬導出（to_dict）可完整重建（from_dict），供 checkpoint/resume。
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Iterator, List, Optional

from .records import EvidenceRecord


class LedgerWriteViolation(RuntimeError):
    """非 Broker 寫入 / 記錄缺少強制綁定字段。"""


class _BrokerToken:
    """能力令牌：僅 broker 模塊經 mint_broker_token() 獲取。"""
    __slots__ = ("owner",)

    def __init__(self, owner: str):
        self.owner = owner


_MINTED: Dict[int, _BrokerToken] = {}
_MINT_LOCK = threading.Lock()


def mint_broker_token(owner: str = "capability_broker") -> _BrokerToken:
    """鑄造寫入令牌。約定調用方只有 hermes_tcm.tools.broker（測試中的
    對抗用例會驗證：偽造對象過不了 isinstance + 鑄造登記雙重檢查）。"""
    token = _BrokerToken(owner)
    with _MINT_LOCK:
        _MINTED[id(token)] = token
    return token


def _valid_token(token: Any) -> bool:
    return isinstance(token, _BrokerToken) and id(token) in _MINTED


class TypedEvidenceLedger:
    """按節點分組的 EvidenceRecord 台賬。"""

    MAX_RECORDS = 400

    def __init__(self, corpus_version: str = ""):
        self.corpus_version = corpus_version
        self._by_node: Dict[str, List[EvidenceRecord]] = {}
        self._by_id: Dict[str, EvidenceRecord] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def register(self, node_id: str, record: EvidenceRecord,
                 token: Any) -> bool:
        """Broker 唯一寫入口。返回 False 表示去重丟棄（同 id 已在賬）。"""
        if not _valid_token(token):
            raise LedgerWriteViolation(
                "台賬寫入違例：非 Capability Broker 持令牌寫入——"
                "模型輸出不能自我登記為證據")
        if record.registered_by != "capability_broker":
            raise LedgerWriteViolation(
                "台賬寫入違例：registered_by 必須為 capability_broker")
        if not (record.tool_call_id and record.span_id):
            raise LedgerWriteViolation(
                "台賬寫入違例：記錄未綁定 tool_call_id/span_id")
        if self.corpus_version and \
                record.corpus_version != self.corpus_version:
            raise LedgerWriteViolation(
                f"台賬寫入違例：記錄語料版本 {record.corpus_version!r} "
                f"≠ 本 run 凍結版本 {self.corpus_version!r}")
        with self._lock:
            if record.evidence_id in self._by_id:
                return False
            if sum(len(v) for v in self._by_node.values()) \
                    >= self.MAX_RECORDS:
                return False
            self._by_node.setdefault(node_id, []).append(record)
            self._by_id[record.evidence_id] = record
            return True

    # ------------------------------------------------------------------
    # 只讀面（任何人可讀）
    # ------------------------------------------------------------------
    def get(self, evidence_id: str) -> Optional[EvidenceRecord]:
        return self._by_id.get(evidence_id)

    def node_records(self, node_id: str) -> List[EvidenceRecord]:
        return list(self._by_node.get(node_id, []))

    def all_records(self) -> List[EvidenceRecord]:
        return list(self._by_id.values())

    def __iter__(self) -> Iterator[EvidenceRecord]:
        return iter(self.all_records())

    def __len__(self) -> int:
        return len(self._by_id)

    def primary_text_ids(self) -> List[str]:
        """發布允許集：只有「正文確實返回」的證據可被引用。"""
        return sorted(r.evidence_id for r in self._by_id.values()
                      if r.is_primary_text_returned)

    def citable_passage_ids(self) -> List[str]:
        return sorted({r.passage_id for r in self._by_id.values()
                       if r.passage_id and r.is_primary_text_returned})

    # ------------------------------------------------------------------
    def verify_integrity(self) -> List[Dict]:
        """完整性審計（發布前）：逐條檢查 Broker 綁定字段。
        違例即返回問題清單（寧可炸也不放行偽證據）。"""
        problems: List[Dict] = []
        for node_id, recs in self._by_node.items():
            for r in recs:
                if r.registered_by != "capability_broker" \
                        or not r.tool_call_id or not r.span_id \
                        or (self.corpus_version
                            and r.corpus_version != self.corpus_version):
                    problems.append({"node": node_id,
                                     "evidence_id": r.evidence_id,
                                     "reason": "broker_binding_missing"})
        return problems

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {"corpus_version": self.corpus_version,
                "nodes": {n: [r.to_dict() for r in recs]
                          for n, recs in self._by_node.items()}}

    @classmethod
    def from_dict(cls, d: Dict[str, Any], token: Any) -> "TypedEvidenceLedger":
        """checkpoint 恢復：重建也必須持令牌（resume 屬 Broker 職權）。"""
        led = cls(corpus_version=d.get("corpus_version", ""))
        for node_id, recs in (d.get("nodes") or {}).items():
            for rd in recs:
                led.register(node_id, EvidenceRecord.from_dict(rd), token)
        return led
