"""Hermes-TCM HTTP 服務（Protocol §15：所有端點返回統一 AnswerEnvelope
或結構化 JSON，禁止裸文本）。

純標準庫 ThreadingHTTPServer。端點：

    GET  /livez                      存活探針（進程活着即 200，不觸數據）
    GET  /readyz                     就緒探針（語料/工具/存儲；核心依賴
                                     缺失時 503 + ok:false——不假就緒）
    GET  /api/tcm/tools?q=&ns=       工具發現（命名空間/按需 discover）
    GET  /api/tcm/resource?uri=      tcm:// 資源讀取
    POST /api/tcm/research           研究 run → AnswerEnvelope
    POST /api/tcm/tool               單工具調用（Broker 中介）
    POST /api/tcm/resume             審批/續跑

安全：HERMES_SERVER_TOKEN 設定時要求 Bearer 鑒權；請求體上限 256KB；
異常只回錯誤類型不回內部細節；默認只綁定 127.0.0.1。
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import parse_qs, urlparse

from .core.principals import PURPOSES_OF_USE, ROLES, Principal
from .integrations.mcp import ResourceResolver
from .integrations.sdk import TCMClient

MAX_BODY_BYTES = 262_144


class _Service:
    """共享服務狀態（單例注入 Handler）。"""

    def __init__(self, store_path: Optional[Path] = None):
        self.client = TCMClient(store_path=store_path)
        self.lock = threading.Lock()

    def close(self):
        self.client.close()


def _principal_from_body(body: Dict) -> Principal:
    role = body.get("role", "researcher")
    purpose = body.get("purpose_of_use", "historical_research")
    if role not in ROLES:
        role = "public"          # 非法角色 fail-closed 收斂到最低權限
    if purpose not in PURPOSES_OF_USE:
        purpose = "patient_education"   # 非法目的收斂到最嚴目的
    return Principal(subject=str(body.get("subject", "api"))[:64],
                     role=role, purpose_of_use=purpose)


class TCMRequestHandler(BaseHTTPRequestHandler):
    service: _Service = None      # serve() 注入

    # ------------------------------------------------------------------
    def _send(self, payload: Dict, status: int = 200) -> None:
        blob = json.dumps(payload, ensure_ascii=False,
                          default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def _authed(self) -> bool:
        token = os.environ.get("HERMES_SERVER_TOKEN", "")
        if not token:
            return True
        got = self.headers.get("Authorization", "")
        return got == f"Bearer {token}"

    def _body(self) -> Optional[Dict]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            self._send({"error": "request_too_large",
                        "max_bytes": MAX_BODY_BYTES}, 413)
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8")
                              or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send({"error": "invalid_json"}, 400)
            return None

    def log_message(self, fmt, *args):     # 安靜（測試/嵌入場景）
        pass

    # ------------------------------------------------------------------
    def do_GET(self):
        try:
            url = urlparse(self.path)
            if url.path == "/livez":
                return self._send({"ok": True})
            if url.path == "/readyz":
                return self._readyz()
            if not self._authed():
                return self._send({"error": "unauthorized"}, 401)
            if url.path == "/api/tcm/tools":
                q = parse_qs(url.query)
                query = (q.get("q") or [""])[0]
                ns = (q.get("ns") or [""])[0]
                reg = self.service.client.registry
                if query or ns:
                    return self._send({"tools": reg.discover(
                        query=query, namespace=ns)})
                return self._send({"namespaces": reg.namespaces(),
                                   "note": "完整定義經 ?q=/?ns= 按需取"})
            if url.path == "/api/tcm/resource":
                uri = (parse_qs(url.query).get("uri") or [""])[0]
                return self._send(self.service.client.read_resource(uri))
            return self._send({"error": "not_found"}, 404)
        except Exception as exc:   # noqa: BLE001 — 不洩露內部細節
            self._send({"error": type(exc).__name__}, 500)

    def do_POST(self):
        try:
            if not self._authed():
                return self._send({"error": "unauthorized"}, 401)
            body = self._body()
            if body is None:
                return
            url = urlparse(self.path)
            if url.path == "/api/tcm/research":
                query = str(body.get("query", "")).strip()
                if not query:
                    return self._send({"error": "query_required"}, 400)
                principal = _principal_from_body(body)
                mode = str(body.get("execution_mode", "single"))
                from .harness.run_spec import EXECUTION_MODES
                if mode not in EXECUTION_MODES:
                    mode = "single"      # 非法模式 fail-closed 到默認
                with self.service.lock:
                    client = TCMClient(
                        store_path=self.service.client.store.path,
                        principal=principal)
                    try:
                        out = client.research(query, execution_mode=mode)
                    finally:
                        client.store.close()
                return self._send(out)
            if url.path == "/api/tcm/tool":
                name = str(body.get("name", ""))
                if not name:
                    return self._send({"error": "name_required"}, 400)
                principal = _principal_from_body(body)
                client = TCMClient(store_path=self.service.client
                                   .store.path, principal=principal)
                try:
                    out = client.call_tool(name,
                                           body.get("arguments") or {})
                finally:
                    client.store.close()
                return self._send(out)
            if url.path == "/api/tcm/resume":
                run_id = str(body.get("run_id", ""))
                if not run_id:
                    return self._send({"error": "run_id_required"}, 400)
                with self.service.lock:
                    out = self.service.client.resume(
                        run_id,
                        approve=str(body.get("approve", "")),
                        reject=str(body.get("reject", "")),
                        approver=str(body.get("approver", ""))[:64],
                        reason=str(body.get("reason", ""))[:256])
                return self._send(out)
            return self._send({"error": "not_found"}, 404)
        except ValueError as exc:
            self._send({"error": "bad_request",
                        "type": type(exc).__name__}, 400)
        except Exception as exc:   # noqa: BLE001
            self._send({"error": type(exc).__name__}, 500)

    # ------------------------------------------------------------------
    def _readyz(self):
        payload, status = readiness_report(self.service.client)
        self._send(payload, status)


def readiness_report(client) -> "tuple[Dict, int]":
    """就緒裁定（/livez 與 /readyz 分離——假健康防護）。

    P0 修復：此前語料缺失仍返回 ok:true + HTTP 200，K8s/負載均衡會
    據此把流量打到不能提供承諾核心功能的實例。核心依賴（語料/工具/
    run 存儲）任一缺失即 503 + ok:false + 缺失組件清單。"""
    try:
        from .tools._shared import searcher
        corpus_ready = searcher() is not None
    except Exception:
        corpus_ready = False
    n_tools = len(client.registry.names())
    store_ok = True
    try:
        client.store.load("__readyz_probe__")      # 只讀探測 DB 可用性
    except Exception:
        store_ok = False
    from .domains.registry import DOMAIN_PACKS
    packs = []
    for p in DOMAIN_PACKS.values():
        entry = {"domain_id": p.domain_id, "status": p.status}
        if p.status == "ready" and p.implementation:
            try:
                impl = p.load_implementation()
                entry["healthy"] = bool(impl and impl.health()["healthy"])
            except Exception:
                entry["healthy"] = False
        packs.append(entry)
    missing = []
    if not corpus_ready:
        missing.append("corpus")
    if n_tools == 0:
        missing.append("tools")
    if not store_ok:
        missing.append("run_store")
    ok = not missing
    payload = {"ok": ok,
               "corpus_available": corpus_ready,
               "n_tools": n_tools,
               "run_store": store_ok,
               "domain_packs": packs,
               "missing": missing,
               "note": ("" if ok
                        else "核心依賴缺失，不可接流量；語料獲取："
                             "python3 -m hermes_shanghan library fetch")}
    return payload, (200 if ok else 503)


def make_server(host: str = "127.0.0.1", port: int = 0,
                store_path: Optional[Path] = None) -> ThreadingHTTPServer:
    """構建服務器（port=0 取臨時端口；調用方負責 serve_forever/shutdown）。"""
    service = _Service(store_path=store_path)
    handler = type("BoundHandler", (TCMRequestHandler,),
                   {"service": service})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd._tcm_service = service       # 供關閉時釋放
    return httpd


def serve(host: str = "127.0.0.1", port: int = 8766,
          store_path: Optional[Path] = None) -> None:
    httpd = make_server(host=host, port=port, store_path=store_path)
    try:
        httpd.serve_forever()
    finally:
        httpd._tcm_service.close()
