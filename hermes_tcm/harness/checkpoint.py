"""Durable run state：SQLite WAL 事件存儲（Protocol §10.3）。

研究型任務不再只依靠 JSON 文件和文件鎖：開發版用 SQLite WAL
（生產版換 PostgreSQL 時表結構同構）。核心表：

    runs            run 頭記錄 + 狀態版本（compare-and-swap）
    node_attempts   節點嘗試（幂等鍵去重）
    events          事件溯源（append-only）
    tool_calls      工具調用台賬
    evidence_records / claim_records / coverage_records
    approval_requests
    leases          節點租約（心跳續租）

實現要點：

* CAS：`update runs set state_version=?+1 where state_version=?`——
  並發寫入失敗即拋 StaleStateError；
* lease：節點執行前取租約（過期可接管）；
* at-least-once：節點重試安全（工具只讀冪等）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    state_version INTEGER NOT NULL DEFAULT 0,
    spec_json TEXT NOT NULL,
    state_json TEXT NOT NULL DEFAULT '{}',
    owner_subject TEXT NOT NULL DEFAULT '',
    tenant_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS node_attempts (
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    output_json TEXT,
    error TEXT,
    PRIMARY KEY (run_id, node_id, attempt)
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_calls (
    run_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    node_id TEXT NOT NULL,
    ok INTEGER NOT NULL,
    error TEXT,
    ms INTEGER,
    at TEXT NOT NULL,
    PRIMARY KEY (run_id, tool_call_id)
);
CREATE TABLE IF NOT EXISTS evidence_records (
    run_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY (run_id, evidence_id)
);
CREATE TABLE IF NOT EXISTS claim_records (
    run_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY (run_id, claim_id)
);
CREATE TABLE IF NOT EXISTS coverage_records (
    run_id TEXT NOT NULL,
    coverage_id TEXT NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY (run_id, coverage_id)
);
CREATE TABLE IF NOT EXISTS approval_requests (
    run_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    trigger_key TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (run_id, approval_id)
);
CREATE TABLE IF NOT EXISTS leases (
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    holder TEXT NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY (run_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_run ON tool_calls(run_id);
"""


class RunAccessDenied(RuntimeError):
    """跨租戶/非屬主訪問 run（→ 403）。"""


class StaleStateError(RuntimeError):
    """CAS 失敗：另一寫者已推進狀態版本。"""


class LeaseHeldError(RuntimeError):
    """節點租約被他人持有且未過期。"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class RunStore:
    """SQLite WAL 持久化（單文件；線程安全經連接鎖）。"""

    LEASE_TTL_S = 120

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path),
                                     check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()
        self._lock = threading.Lock()

    def _migrate(self) -> None:
        """向後兼容遷移：舊庫（無 owner/tenant 列）補列。"""
        cols = {r[1] for r in
                self._conn.execute("PRAGMA table_info(runs)").fetchall()}
        if "owner_subject" not in cols:
            self._conn.execute(
                "ALTER TABLE runs ADD COLUMN owner_subject TEXT "
                "NOT NULL DEFAULT ''")
        if "tenant_id" not in cols:
            self._conn.execute(
                "ALTER TABLE runs ADD COLUMN tenant_id TEXT "
                "NOT NULL DEFAULT ''")
        # 索引在列存在後建（新舊庫皆安全）
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_tenant "
                           "ON runs(tenant_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_owner "
                           "ON runs(owner_subject)")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # runs（CAS 狀態）
    # ------------------------------------------------------------------
    def create_run(self, run_id: str, spec: Dict,
                   owner_subject: str = "", tenant_id: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs (run_id, status, spec_json, state_json,"
                " owner_subject, tenant_id, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (run_id, "queued", json.dumps(spec, ensure_ascii=False),
                 "{}", owner_subject, tenant_id, _now(), _now()))
            self._conn.commit()

    def run_acl(self, run_id: str) -> Optional[Dict]:
        """run 的屬主/租戶（授權判定用）；未知 run 返回 None。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT owner_subject, tenant_id FROM runs WHERE run_id=?",
                (run_id,)).fetchone()
        if row is None:
            return None
        return {"owner_subject": row[0], "tenant_id": row[1]}

    def authorize(self, run_id: str, principal, action: str = "read"):
        """跨租戶/非屬主訪問拒絕（P0-3）。system_admin 例外（同租戶內）。

        principal：hermes_tcm.core.principals.Principal。
        返回 run acl（授權通過）或拋 RunAccessDenied（403）。
        未知 run 返回 None（調用方按 404 處理）。"""
        acl = self.run_acl(run_id)
        if acl is None:
            return None
        # 未標記屬主的舊 run（遷移前）：僅同名 subject 或匿名可讀
        owner = acl["owner_subject"]
        tenant = acl["tenant_id"]
        p_tenant = getattr(principal, "tenant_id", "") or ""
        p_subject = getattr(principal, "subject", "") or ""
        p_role = getattr(principal, "role", "") or ""
        if tenant and p_tenant and tenant != p_tenant:
            raise RunAccessDenied(
                f"跨租戶訪問被拒：run 屬 {tenant}，主體屬 {p_tenant}")
        if p_role == "system_admin":
            return acl
        if owner and p_subject and owner != p_subject:
            raise RunAccessDenied(
                f"非屬主訪問被拒：run 屬 {owner}，主體 {p_subject}"
                f"（action={action}）")
        return acl

    def load(self, run_id: str) -> Optional[Dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT status, state_version, spec_json, state_json"
                " FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        return {"run_id": run_id, "status": row[0],
                "state_version": row[1],
                "spec": json.loads(row[2]),
                "state": json.loads(row[3])}

    def save_state(self, run_id: str, status: str, state: Dict,
                   expected_version: int) -> int:
        """CAS 寫入；版本不符拋 StaleStateError。返回新版本。"""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE runs SET status=?, state_json=?,"
                " state_version=state_version+1, updated_at=?"
                " WHERE run_id=? AND state_version=?",
                (status, json.dumps(state, ensure_ascii=False), _now(),
                 run_id, expected_version))
            self._conn.commit()
            if cur.rowcount != 1:
                raise StaleStateError(
                    f"run {run_id} 狀態版本 {expected_version} 已過期"
                    "——另一寫者已推進（compare-and-swap 拒絕覆蓋）")
            return expected_version + 1

    def list_runs(self, limit: int = 30) -> List[Dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT run_id, status, created_at FROM runs"
                " ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{"run_id": r[0], "status": r[1], "created_at": r[2]}
                for r in rows]

    # ------------------------------------------------------------------
    # events（append-only 事件溯源）
    # ------------------------------------------------------------------
    def append_event(self, run_id: str, event_type: str,
                     payload: Dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (run_id, event_type, payload_json, at)"
                " VALUES (?,?,?,?)",
                (run_id, event_type,
                 json.dumps(payload, ensure_ascii=False, default=str),
                 _now()))
            self._conn.commit()

    def events(self, run_id: str) -> List[Dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, event_type, payload_json, at FROM events"
                " WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()
        return [{"seq": r[0], "event_type": r[1],
                 "payload": json.loads(r[2]), "at": r[3]} for r in rows]

    # ------------------------------------------------------------------
    # node attempts + lease
    # ------------------------------------------------------------------
    def acquire_lease(self, run_id: str, node_id: str, holder: str) -> None:
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT holder, expires_at FROM leases"
                " WHERE run_id=? AND node_id=?",
                (run_id, node_id)).fetchone()
            if row is not None and row[1] > now and row[0] != holder:
                raise LeaseHeldError(
                    f"節點 {node_id} 租約被 {row[0]} 持有（未過期）")
            self._conn.execute(
                "INSERT OR REPLACE INTO leases (run_id, node_id, holder,"
                " expires_at) VALUES (?,?,?,?)",
                (run_id, node_id, holder, now + self.LEASE_TTL_S))
            self._conn.commit()

    def release_lease(self, run_id: str, node_id: str, holder: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM leases WHERE run_id=? AND node_id=?"
                " AND holder=?", (run_id, node_id, holder))
            self._conn.commit()

    def record_attempt(self, run_id: str, node_id: str, attempt: int,
                       status: str, output: Optional[Dict] = None,
                       error: str = "",
                       idempotency_key: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO node_attempts (run_id, node_id,"
                " attempt, idempotency_key, status, started_at, ended_at,"
                " output_json, error) VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, node_id, attempt, idempotency_key, status,
                 _now(), _now(),
                 json.dumps(output, ensure_ascii=False, default=str)
                 if output is not None else None,
                 error or None))
            self._conn.commit()

    def completed_attempt(self, run_id: str, node_id: str,
                          idempotency_key: str = "") -> Optional[Dict]:
        """幂等回放：同 key 的成功嘗試直接復用輸出。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT output_json FROM node_attempts"
                " WHERE run_id=? AND node_id=? AND status='ok'"
                + (" AND idempotency_key=?" if idempotency_key else "")
                + " ORDER BY attempt DESC LIMIT 1",
                (run_id, node_id, idempotency_key) if idempotency_key
                else (run_id, node_id)).fetchone()
        if row is None or row[0] is None:
            return None
        return json.loads(row[0])

    # ------------------------------------------------------------------
    # 台賬鏡像（審計視圖；權威在 TypedEvidenceLedger）
    # ------------------------------------------------------------------
    def record_tool_call(self, run_id: str, entry: Dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO tool_calls (run_id, tool_call_id,"
                " tool, node_id, ok, error, ms, at) VALUES (?,?,?,?,?,?,?,?)",
                (run_id, entry.get("tool_call_id", entry.get("span_id", "")),
                 entry.get("tool", ""), entry.get("node_id", ""),
                 1 if entry.get("ok") else 0, entry.get("error"),
                 entry.get("ms"), entry.get("at", _now())))
            self._conn.commit()

    def record_evidence(self, run_id: str, node_id: str,
                        record: Dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO evidence_records (run_id,"
                " evidence_id, node_id, record_json) VALUES (?,?,?,?)",
                (run_id, record.get("evidence_id", ""), node_id,
                 json.dumps(record, ensure_ascii=False, default=str)))
            self._conn.commit()

    def record_claim(self, run_id: str, record: Dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO claim_records (run_id, claim_id,"
                " record_json) VALUES (?,?,?)",
                (run_id, record.get("claim_id", ""),
                 json.dumps(record, ensure_ascii=False, default=str)))
            self._conn.commit()

    def record_coverage(self, run_id: str, record: Dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO coverage_records (run_id,"
                " coverage_id, record_json) VALUES (?,?,?)",
                (run_id, record.get("coverage_id", ""),
                 json.dumps(record, ensure_ascii=False, default=str)))
            self._conn.commit()

    def record_approval(self, run_id: str, approval: Dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO approval_requests (run_id,"
                " approval_id, trigger_key, status, payload_json)"
                " VALUES (?,?,?,?,?)",
                (run_id, approval.get("approval_id", ""),
                 approval.get("trigger", ""),
                 approval.get("status", "pending"),
                 json.dumps(approval, ensure_ascii=False, default=str)))
            self._conn.commit()

    def approvals(self, run_id: str) -> List[Dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload_json FROM approval_requests WHERE run_id=?",
                (run_id,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    # ------------------------------------------------------------------
    # dead-letter queue（Protocol §10.3）：重試耗盡的失敗節點
    # ------------------------------------------------------------------
    def dead_letters(self, run_id: str = "") -> List[Dict]:
        """失敗節點清單（最後一次嘗試 status=failed 的節點）。"""
        sql = ("SELECT run_id, node_id, MAX(attempt), status, error,"
               " ended_at FROM node_attempts"
               + (" WHERE run_id=?" if run_id else "")
               + " GROUP BY run_id, node_id")
        with self._lock:
            rows = self._conn.execute(
                sql, (run_id,) if run_id else ()).fetchall()
        return [{"run_id": r[0], "node_id": r[1], "attempts": r[2],
                 "error": r[4], "at": r[5]}
                for r in rows if r[3] == "failed"]

    def requeue_node(self, run_id: str, node_id: str) -> bool:
        """DLQ 重投：清除該節點狀態，下次 execute 重跑（CAS 寫入）。
        終態 run 不可重投（先走 resume 語義）。"""
        row = self.load(run_id)
        if row is None or row["status"] in ("completed", "blocked",
                                            "rejected", "cancelled"):
            return False
        state = row["state"]
        removed = state.get("nodes", {}).pop(node_id, None)
        state.get("node_outputs", {}).pop(node_id, None)
        if removed is None:
            return False
        self.save_state(run_id, "queued", state, row["state_version"])
        self.append_event(run_id, "node_requeued", {"node": node_id})
        return True
