"""OTel trace exporter（Protocol §10.3）。

把 RunStore 的事件溯源（events / node_attempts / tool_calls）轉譯為
OpenTelemetry OTLP JSON 兼容結構（resourceSpans）——純標準庫，
可直接被外部 OTel collector 的 OTLP/HTTP JSON 端點消費。

id 規則：traceId = sha256(run_id)[:32]，spanId = sha256(kind|key)[:16]
——確定性、可重放。時間戳取事件記錄時間（秒級，如實 *1e9 為 ns）。
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, List

from .checkpoint import RunStore


def _trace_id(run_id: str) -> str:
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32]


def _span_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _ns(ts: str) -> int:
    try:
        return int(time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))
                   * 1_000_000_000)
    except (ValueError, OverflowError):
        return 0


def _attr(key: str, value: Any) -> Dict:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    return {"key": key, "value": {"stringValue": str(value)}}


def export_otlp(store: RunStore, run_id: str) -> Dict:
    """run → OTLP JSON（resourceSpans）。未知 run 返回 error。"""
    row = store.load(run_id)
    if row is None:
        return {"error": f"未知 run：{run_id}"}
    trace_id = _trace_id(run_id)
    root_span = _span_id("run", run_id)
    spans: List[Dict] = [{
        "traceId": trace_id, "spanId": root_span, "parentSpanId": "",
        "name": f"run:{run_id}", "kind": 1,
        "startTimeUnixNano": str(_ns(row["spec"].get("created_at", ""))),
        "endTimeUnixNano": str(_ns(row["spec"].get("created_at", ""))),
        "attributes": [_attr("run.status", row["status"]),
                       _attr("run.task_type",
                             row["spec"].get("task_type", ""))],
    }]
    with store._lock:
        attempts = store._conn.execute(
            "SELECT node_id, attempt, status, started_at, ended_at, error"
            " FROM node_attempts WHERE run_id=? ORDER BY started_at",
            (run_id,)).fetchall()
        tools = store._conn.execute(
            "SELECT tool_call_id, tool, node_id, ok, error, ms, at"
            " FROM tool_calls WHERE run_id=?", (run_id,)).fetchall()
    for node_id, attempt, status, started, ended, error in attempts:
        spans.append({
            "traceId": trace_id,
            "spanId": _span_id("node", run_id, node_id, str(attempt)),
            "parentSpanId": root_span,
            "name": f"node:{node_id}", "kind": 1,
            "startTimeUnixNano": str(_ns(started or "")),
            "endTimeUnixNano": str(_ns(ended or started or "")),
            "attributes": [_attr("node.status", status),
                           _attr("node.attempt", attempt)]
            + ([_attr("node.error", error)] if error else []),
            "status": {"code": 2 if status == "failed" else 1},
        })
    for tool_call_id, tool, node_id, ok, error, ms, at in tools:
        start = _ns(at or "")
        spans.append({
            "traceId": trace_id,
            "spanId": _span_id("tool", run_id, tool_call_id),
            "parentSpanId": _span_id("node", run_id, node_id, "1"),
            "name": f"tool:{tool}", "kind": 3,
            "startTimeUnixNano": str(start),
            "endTimeUnixNano": str(start + int(ms or 0) * 1_000_000),
            "attributes": [_attr("tool.ok", bool(ok)),
                           _attr("tool.node", node_id)]
            + ([_attr("tool.error", error)] if error else []),
            "status": {"code": 1 if ok else 2},
        })
    return {"resourceSpans": [{
        "resource": {"attributes": [
            _attr("service.name", "hermes-tcm"),
            _attr("tcm.run_id", run_id)]},
        "scopeSpans": [{"scope": {"name": "hermes_tcm.harness"},
                        "spans": spans}],
    }]}
