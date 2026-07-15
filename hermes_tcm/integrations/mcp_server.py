"""真正的 MCP Server（JSON-RPC 2.0 over stdio）——Protocol §9.4 / P0-7。

此前 integrations/mcp.py 只提供 schema 導出與本地 ResourceResolver
（MCP-compatible schemas），不是可互操作的 MCP Server。本模塊補齊
完整生命週期與標準消息：

    initialize            能力協商（protocolVersion + capabilities）
    notifications/initialized
    tools/list            工具清單（annotations：readOnlyHint 等）
    tools/call            調用（經 CapabilityBroker：角色/目的/證據/覆蓋）
    resources/list        資源模板
    resources/read        tcm:// 讀取（租戶授權 + 投影）
    ping                  心跳
    $/cancelRequest       取消（協作式）

純標準庫，stdio 逐行 JSON-RPC（Content-Length framing 可選，此處用
換行分隔的 JSON，便於 stdlib 測試與 MCP Inspector 的 line 模式）。
啟動：`python3 -m hermes_tcm.integrations.mcp_server`（或 hermes-tcm
serve-mcp --transport stdio）。

安全：MCP 客戶端經 stdio 啟動本進程即代表本地信任邊界；跨網絡部署
須用 Streamable HTTP + OAuth 資源保護（規劃層，如實標注）。
"""
from __future__ import annotations

import json
import sys
import threading
from typing import Any, Dict, Optional, TextIO

from ..core.principals import Principal
from ..evidence.ledger import TypedEvidenceLedger
from ..integrations.mcp import (SERVER_INSTRUCTIONS, ResourceResolver,
                                list_resource_templates)
from ..tools.broker import CapabilityBroker
from ..tools.registry import get_tcm_registry

PROTOCOL_VERSION = "2025-06-18"
FALLBACK_PROTOCOL = "2024-11-05"
SERVER_NAME = "hermes-tcm"

# JSON-RPC 錯誤碼
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _server_version() -> str:
    try:
        from .. import __version__
        return __version__
    except Exception:
        return "0"


class MCPServer:
    """單連接 MCP Server（stdio）。principal 由啟動環境決定（本地信任）。"""

    def __init__(self, principal: Optional[Principal] = None,
                 store=None):
        self.registry = get_tcm_registry()
        self.principal = principal or Principal(subject="mcp-local",
                                                role="researcher")
        self.store = store
        self.initialized = False
        self._cancelled = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def handle(self, msg: Dict) -> Optional[Dict]:
        """處理單條 JSON-RPC 消息；通知（無 id）返回 None。"""
        if msg.get("jsonrpc") != "2.0":
            return self._err(msg.get("id"), INVALID_REQUEST,
                             "jsonrpc 必須為 2.0")
        method = msg.get("method")
        mid = msg.get("id")
        params = msg.get("params") or {}
        is_notification = "id" not in msg
        try:
            if method == "initialize":
                return self._reply(mid, self._initialize(params))
            if method == "notifications/initialized":
                self.initialized = True
                return None
            if method == "notifications/cancelled" or \
                    method == "$/cancelRequest":
                rid = params.get("requestId") or params.get("id")
                if rid is not None:
                    with self._lock:
                        self._cancelled.add(rid)
                return None
            if method == "ping":
                return self._reply(mid, {})
            if method == "tools/list":
                return self._reply(mid, self._tools_list())
            if method == "tools/call":
                return self._reply(mid, self._tools_call(params))
            if method == "resources/list":
                return self._reply(mid, {"resources": [],
                                         "resourceTemplates":
                                             list_resource_templates()})
            if method == "resources/templates/list":
                return self._reply(mid, {"resourceTemplates":
                                         list_resource_templates()})
            if method == "resources/read":
                return self._reply(mid, self._resources_read(params))
            if is_notification:
                return None
            return self._err(mid, METHOD_NOT_FOUND,
                             f"未知方法：{method}")
        except _McpError as exc:
            return self._err(mid, exc.code, exc.message)
        except Exception as exc:   # noqa: BLE001
            return self._err(mid, INTERNAL_ERROR,
                             f"{type(exc).__name__}")

    # ------------------------------------------------------------------
    def _initialize(self, params: Dict) -> Dict:
        client_proto = params.get("protocolVersion", "")
        proto = PROTOCOL_VERSION
        if client_proto and client_proto < PROTOCOL_VERSION:
            proto = FALLBACK_PROTOCOL if client_proto <= FALLBACK_PROTOCOL \
                else client_proto
        return {
            "protocolVersion": proto,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
                "prompts": {"listChanged": False},
                "logging": {},
            },
            "serverInfo": {"name": SERVER_NAME, "version": _server_version()},
            "instructions": SERVER_INSTRUCTIONS,
        }

    def _require_init(self):
        if not self.initialized:
            raise _McpError(INVALID_REQUEST,
                            "尚未 initialize（先 initialize + "
                            "notifications/initialized）")

    def _tools_list(self) -> Dict:
        self._require_init()
        reg = self.registry.for_role(self.principal.role)
        return {"tools": [reg.get(n).mcp_spec() for n in reg.names()]}

    def _tools_call(self, params: Dict) -> Dict:
        self._require_init()
        name = params.get("name", "")
        # MCP 名稱用雙下劃線代替命名空間點號——還原
        resolved = name.replace("__", ".", 1) if "__" in name else name
        arguments = params.get("arguments") or {}
        ledger = TypedEvidenceLedger("")
        broker = CapabilityBroker(
            self.registry.for_role(self.principal.role), ledger,
            principal=self.principal)
        result = broker.call(resolved, arguments)
        is_error = isinstance(result, dict) and bool(result.get("error"))
        # MCP content：結構化結果 + 隨行證據摘要
        content = [{"type": "text",
                    "text": json.dumps(result, ensure_ascii=False,
                                       default=str)[:8000]}]
        payload = {"content": content, "isError": is_error}
        evidence = [r.to_dict() for r in ledger.all_records()]
        if evidence:
            payload["_meta"] = {"evidence_count": len(evidence),
                                "coverage": list(broker.coverages)}
        return payload

    def _resources_read(self, params: Dict) -> Dict:
        self._require_init()
        uri = params.get("uri", "")
        resolver = ResourceResolver(run_store=self.store,
                                    principal=self.principal)
        data = resolver.read(uri)
        return {"contents": [{
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(data, ensure_ascii=False, default=str)}]}

    # ------------------------------------------------------------------
    @staticmethod
    def _reply(mid: Any, result: Dict) -> Optional[Dict]:
        if mid is None:
            return None
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    @staticmethod
    def _err(mid: Any, code: int, message: str) -> Optional[Dict]:
        if mid is None:
            return None
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": code, "message": message}}


class _McpError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def serve_stdio(stdin: Optional[TextIO] = None,
                stdout: Optional[TextIO] = None,
                principal: Optional[Principal] = None,
                store=None) -> None:
    """逐行 JSON-RPC over stdio。EOF 結束。"""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    server = MCPServer(principal=principal, store=store)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None,
                    "error": {"code": PARSE_ERROR, "message": "JSON 解析失敗"}}
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()
            continue
        resp = server.handle(msg)
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False,
                                    default=str) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve_stdio()
