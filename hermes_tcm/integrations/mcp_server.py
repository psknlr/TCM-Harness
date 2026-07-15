"""統一 V2 MCP Server（stdio JSON-RPC 2.0，純標準庫）。

P1 修復：此前唯一完整的 stdio MCP server 位於 legacy 包（舊
ToolRegistry + shanghan:// 資源 + in-memory tasks）。本模塊直接建立在
V2 主棧之上：

    TCMClient / CapabilityBroker   工具調用（角色/目的/校驗/證據台賬）
    ToolNamespaceRegistry          tools/list（V2 契約，含 annotations）
    ResourceResolver               tcm:// 資源（resources/*）
    ResearchRunController+RunStore tasks/*（durable：SQLite 持久，
                                   服務重啟後 status/result/cancel 仍可用；
                                   cancel 走 request_cancel，在節點邊界
                                   真正停止 run，不是只丟棄結果）

版本協商：客戶端請求的版本在支持列表內則回顯；不支持時回應**最新**
支持版本（修復 legacy「未知版本回退最舊」的倒掛）。

註冊示例：
    claude mcp add hermes-tcm -- python3 -m hermes_tcm.integrations.mcp_server
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.principals import PURPOSES_OF_USE, ROLES, Principal
from .mcp import (RESOURCE_TEMPLATES, SERVER_INSTRUCTIONS,
                  ResourceResolver, list_resource_templates)

# 新在前：不支持客戶端版本時回應最新支持版本（MCP 協商語義）
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
SERVER_INFO = {"name": "hermes-tcm", "version": "2.0.0"}

MAX_TASKS_LISTED = 30

# 合成工具（工具面之外的運行時入口；名稱保持 MCP 安全字符）
_RESEARCH_TOOL = {
    "name": "tcm__research",
    "description": "同步研究 run：typed DAG 全程（取證→主張→核驗→"
                   "發布閘門），返回 AnswerEnvelope。長任務用 tasks/submit。",
    "inputSchema": {"type": "object", "properties": {
        "query": {"type": "string", "minLength": 2},
        "task_type": {"type": "string"},
        "execution_mode": {"type": "string",
                           "description": "single|council"}},
        "required": ["query"]},
    "annotations": {"readOnlyHint": True, "idempotentHint": False,
                    "destructiveHint": False, "openWorldHint": False},
}


def _result(id_: Any, result: Dict) -> Dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _error(id_: Any, code: int, message: str) -> Dict:
    return {"jsonrpc": "2.0", "id": id_,
            "error": {"code": code, "message": message}}


def _content(payload: Any) -> Dict:
    return {"content": [{"type": "text",
                         "text": json.dumps(payload, ensure_ascii=False,
                                            default=str)}]}


class TCMMCPServer:
    """可測試的請求處理核心（stdio 循環見 serve()）。"""

    def __init__(self, store_path: Optional[Path] = None,
                 role: str = "researcher",
                 purpose: str = "historical_research"):
        from .sdk import TCMClient
        if role not in ROLES:
            role = "public"                      # fail-closed 收斂
        if purpose not in PURPOSES_OF_USE:
            purpose = "patient_education"
        self.client = TCMClient(
            store_path=store_path,
            principal=Principal(subject="mcp", role=role,
                                purpose_of_use=purpose))
        self.resolver = ResourceResolver(run_store=self.client.store)
        self.protocol_version = SUPPORTED_PROTOCOL_VERSIONS[0]
        # 提交線程登記（僅活性觀察；狀態真身在 RunStore——durable）
        self._workers: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def close(self) -> None:
        self.client.close()

    # ------------------------------------------------------------------
    def handle(self, req: Dict) -> Optional[Dict]:
        """單請求處理；notification（無 id）返回 None。"""
        id_ = req.get("id")
        method = req.get("method", "")
        params = req.get("params") or {}
        is_notification = "id" not in req
        try:
            out = self._dispatch(method, params)
        except KeyError as exc:
            return None if is_notification \
                else _error(id_, -32602, str(exc))
        except Exception as exc:   # noqa: BLE001 — 不洩露內部細節
            return None if is_notification \
                else _error(id_, -32000, type(exc).__name__)
        if out is None and not is_notification:
            return _error(id_, -32601, f"method not found: {method}")
        return None if is_notification else _result(id_, out)

    # ------------------------------------------------------------------
    def _dispatch(self, method: str, params: Dict) -> Optional[Dict]:
        if method == "initialize":
            return self._initialize(params)
        if method in ("notifications/initialized", "initialized"):
            return {}
        if method == "ping":
            return {}
        if method == "tools/list":
            return self._tools_list()
        if method == "tools/call":
            return self._tools_call(params)
        if method == "resources/list":
            return self._resources_list()
        if method == "resources/templates/list":
            return {"resourceTemplates": list_resource_templates()}
        if method == "resources/read":
            return self._resources_read(params)
        if method == "tasks/submit":
            return self._tasks_submit(params)
        if method in ("tasks/status", "tasks/get"):
            return self._tasks_status(params)
        if method == "tasks/result":
            return self._tasks_result(params)
        if method == "tasks/cancel":
            return self._tasks_cancel(params)
        if method == "tasks/list":
            return self._tasks_list()
        return None

    # ------------------------------------------------------------------
    def _initialize(self, params: Dict) -> Dict:
        requested = str(params.get("protocolVersion", ""))
        self.protocol_version = requested \
            if requested in SUPPORTED_PROTOCOL_VERSIONS \
            else SUPPORTED_PROTOCOL_VERSIONS[0]
        return {"protocolVersion": self.protocol_version,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False,
                                  "listChanged": False},
                    "experimental": {
                        "tasks": {"durable": True,
                                  "store": "sqlite_run_store",
                                  "cancel": "node_boundary"}}},
                "serverInfo": dict(SERVER_INFO),
                "instructions": SERVER_INSTRUCTIONS}

    def _tools_list(self) -> Dict:
        reg = self.client.registry.for_role(self.client.principal.role)
        tools = [reg.get(n).mcp_spec() for n in reg.names()]
        tools.append(dict(_RESEARCH_TOOL))
        return {"tools": tools}

    def _tools_call(self, params: Dict) -> Dict:
        name = str(params.get("name", ""))
        arguments = params.get("arguments") or {}
        if not name:
            raise KeyError("missing tool name")
        if name == "tcm__research":
            query = str(arguments.get("query", "")).strip()
            if not query:
                raise KeyError("missing query")
            kwargs = {}
            if arguments.get("task_type"):
                kwargs["task_type"] = str(arguments["task_type"])
            out = self.client.research(
                query,
                execution_mode=str(arguments.get("execution_mode",
                                                 "single")),
                **kwargs)
            return _content(out)
        tool_name = name.replace("__", ".")
        out = self.client.call_tool(tool_name, arguments)
        payload = {"result": out["result"],
                   "evidence": out["evidence"],
                   "guardrail_events": out["guardrail_events"]}
        wrapped = _content(payload)
        if isinstance(out["result"], dict) and out["result"].get("error"):
            wrapped["isError"] = True
        return wrapped

    # ------------------------------------------------------------------
    def _resources_list(self) -> Dict:
        resources = [{"uri": "tcm://policies/current",
                      "name": "結論策略（當前版本全集）",
                      "mimeType": "application/json"}]
        try:
            from ..skills import list_skills
            for s in list_skills():
                resources.append({"uri": f"tcm://skills/{s['name']}",
                                  "name": f"技能：{s['name']}",
                                  "description": s.get("description", ""),
                                  "mimeType": "text/markdown"})
        except Exception:
            pass
        for row in self.client.store.list_runs(limit=10):
            resources.append({"uri": f"tcm://runs/{row['run_id']}",
                              "name": f"run {row['run_id']}"
                                      f"（{row['status']}）",
                              "mimeType": "application/json"})
        return {"resources": resources}

    def _resources_read(self, params: Dict) -> Dict:
        uri = str(params.get("uri", ""))
        out = self.resolver.read(uri)
        if out.get("error"):
            raise KeyError(out["error"])
        return {"contents": [{"uri": uri,
                              "mimeType": "application/json",
                              "text": json.dumps(out, ensure_ascii=False,
                                                 default=str)}]}

    # ------------------------------------------------------------------
    # tasks/*：durable 長任務（RunStore 持久；重啟可續查）
    # ------------------------------------------------------------------
    def _tasks_submit(self, params: Dict) -> Dict:
        query = str(params.get("query", "")).strip()
        if not query:
            raise KeyError("missing query")
        mode = str(params.get("execution_mode", "single"))
        from ..harness.run_spec import EXECUTION_MODES, new_run_id
        if mode not in EXECUTION_MODES:
            mode = "single"
        kwargs: Dict[str, Any] = {"run_id": new_run_id(query)}
        if params.get("task_type"):
            kwargs["task_type"] = str(params["task_type"])
        run_id = kwargs["run_id"]

        def _work():
            try:
                self.client.research(query, execution_mode=mode, **kwargs)
            except Exception:
                pass        # 狀態真身在 RunStore；失敗 run 記為 failed

        th = threading.Thread(target=_work, daemon=True,
                              name=f"mcp-task-{run_id}")
        with self._lock:
            self._workers[run_id] = th
        th.start()
        return {"task_id": run_id, "status": "submitted",
                "execution_mode": mode,
                "durability": "sqlite_run_store（服務重啟後 tasks/status"
                              "、tasks/result、tasks/cancel 仍可用）"}

    def _task_row(self, params: Dict) -> Dict:
        task_id = str(params.get("task_id", ""))
        row = self.client.store.load(task_id)
        if row is None:
            with self._lock:
                th = self._workers.get(task_id)
            if th is not None and th.is_alive():
                return {"run_id": task_id, "status": "queued",
                        "state": {}, "spec": {}}
            raise KeyError(f"unknown task: {task_id}")
        return row

    def _tasks_status(self, params: Dict) -> Dict:
        row = self._task_row(params)
        envelope = (row.get("state") or {}).get("envelope") or {}
        return {"task_id": row["run_id"], "status": row["status"],
                "decision": (envelope.get("release") or {})
                .get("decision", ""),
                "execution_mode": (row.get("spec") or {})
                .get("execution_mode", "single")}

    def _tasks_result(self, params: Dict) -> Dict:
        row = self._task_row(params)
        if row["status"] in ("queued", "running"):
            raise KeyError(f"task not finished: status={row['status']}")
        envelope = (row.get("state") or {}).get("envelope")
        if envelope is None:
            raise KeyError(f"task {row['run_id']} 無 envelope"
                           f"（status={row['status']}）")
        return _content({"run_id": row["run_id"],
                         "status": row["status"], "envelope": envelope})

    def _tasks_cancel(self, params: Dict) -> Dict:
        task_id = str(params.get("task_id", ""))
        ok = self.client.controller.request_cancel(task_id)
        return {"task_id": task_id, "cancelled": ok,
                "note": ("取消旗標已置位：run 在下一個節點邊界真正停止"
                         "（durable，重啟後仍生效）" if ok
                         else "任務不存在或已終態，取消無效果")}

    def _tasks_list(self) -> Dict:
        return {"tasks": self.client.store.list_runs(
            limit=MAX_TASKS_LISTED)}


# ---------------------------------------------------------------------------
def serve(store_path: Optional[Path] = None) -> None:
    """stdio 循環：newline-delimited JSON-RPC。"""
    env_store = os.environ.get("HERMES_TCM_STORE", "")
    server = TCMMCPServer(
        store_path=store_path or (Path(env_store) if env_store else None),
        role=os.environ.get("HERMES_TCM_MCP_ROLE", "researcher"),
        purpose=os.environ.get("HERMES_TCM_MCP_PURPOSE",
                               "historical_research"))
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                sys.stdout.write(json.dumps(
                    _error(None, -32700, "parse error"),
                    ensure_ascii=False) + "\n")
                sys.stdout.flush()
                continue
            resp = server.handle(req)
            if resp is not None:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False,
                                            default=str) + "\n")
                sys.stdout.flush()
    finally:
        server.close()


if __name__ == "__main__":
    serve()
