"""Replay（Protocol §4 交付「strict/evidence/policy replay」）。

    strict    重跑同一 RunSpec，對比最終回答指紋——僅在環境指紋一致
              且 deterministic 後端下構成回歸信號
    evidence  只重驗證據台賬（逐字重驗 quote_hash/座標），不重跑檢索
    policy    只重跑策略引擎（新版策略對舊主張的裁定差異）

指紋不一致時如實標 comparable=False——「當前代碼+當前語料重跑一遍」
不等於可復現 replay。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

from ..claims.policy_dsl import ConclusionPolicyEngine
from ..claims.records import ClaimRecord
from ..evidence.ledger import TypedEvidenceLedger, mint_broker_token
from ..evidence.packets import verify_packet
from .checkpoint import RunStore
from .run_spec import RunSpecV2, environment_fingerprint


def _digest(obj: Any) -> str:
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def replay_strict(store: RunStore, controller, run_id: str) -> Dict:
    old = store.load(run_id)
    if old is None:
        return {"error": f"未知 run：{run_id}"}
    spec = RunSpecV2.from_dict(old["spec"])
    current = environment_fingerprint()
    mismatches = {}
    for k, v in current.items():
        recorded = spec.environment_fingerprint.get(k, "")
        if recorded and recorded != v:
            mismatches[k] = {"recorded": recorded, "current": v}
    new = controller.start(spec.query, principal=spec.principal,
                           task_type=spec.task_type)
    old_answer = old.get("state", {}).get("final_answer", "")
    new_answer = new.get("state", {}).get("final_answer", "")
    comparable = (not mismatches
                  and spec.model_policy.planner == "deterministic")
    return {"mode": "strict",
            "original_run": run_id,
            "replay_run": new["run_id"],
            "original_digest": _digest(old_answer),
            "replay_digest": _digest(new_answer),
            "deterministic_match": _digest(old_answer) == _digest(new_answer),
            "comparable": comparable,
            "fingerprint_mismatches": mismatches,
            "note": "comparable=False 時的差異不構成回歸信號"}


def replay_evidence(store: RunStore, run_id: str,
                    passage_index=None) -> Dict:
    """只重驗台賬：每條證據 hash 自洽 +（庫可用時）回庫切片對照。"""
    old = store.load(run_id)
    if old is None:
        return {"error": f"未知 run：{run_id}"}
    ledger_d = old.get("state", {}).get("ledger")
    if not ledger_d:
        return {"mode": "evidence", "run_id": run_id,
                "n_records": 0, "verification": {"ok": True, "note":
                                                 "台賬為空"}}
    ledger = TypedEvidenceLedger.from_dict(
        ledger_d, mint_broker_token("capability_broker"))
    verification = verify_packet(ledger.all_records(), passage_index)
    return {"mode": "evidence", "run_id": run_id,
            "n_records": len(ledger),
            "verification": verification,
            "reverified_against_library": passage_index is not None}


def replay_policy(store: RunStore, run_id: str,
                  engine: ConclusionPolicyEngine = None) -> Dict:
    """新策略版本對舊主張重新裁定：逐主張列出裁定變化。"""
    old = store.load(run_id)
    if old is None:
        return {"error": f"未知 run：{run_id}"}
    engine = engine or ConclusionPolicyEngine()
    state = old.get("state", {})
    ledger_d = state.get("ledger") or {"nodes": {}}
    ledger = TypedEvidenceLedger.from_dict(
        ledger_d, mint_broker_token("capability_broker"))
    changes = []
    for cd in state.get("claims", []):
        claim = ClaimRecord.from_dict(cd)
        old_verdict = (cd.get("verification") or {}) \
            .get("policy", {}).get("verdict", "")
        ev = [ledger.get(e) for e in claim.supporting_evidence
              if ledger.get(e) is not None]
        new_verdict = engine.evaluate(
            claim, ev, coverage=None,
            tools_used=[], role="researcher")["verdict"]
        if new_verdict != old_verdict:
            changes.append({"claim_id": claim.claim_id,
                            "old": old_verdict, "new": new_verdict})
    return {"mode": "policy", "run_id": run_id,
            "policy_version": engine.version,
            "policy_fingerprint": engine.fingerprint,
            "n_claims": len(state.get("claims", [])),
            "n_changed": len(changes), "changes": changes,
            "note": "policy replay 不重跑檢索：覆蓋/工具條件按缺省評估，"
                    "僅比較證據/角色類條款的裁定漂移"}
