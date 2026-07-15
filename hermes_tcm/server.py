"""Hermes-TCM HTTP 服務（Protocol §14/§15）。

**開發/演示服務器**（純標準庫 ThreadingHTTPServer）——生產請走
ASGI + PostgreSQL + worker pool（見 docs/MATURITY 成熟度分級）。

端點：

    GET  /livez                      進程存活
    GET  /readyz                     部署 profile 就緒（不滿足→503）
    GET  /api/tcm/tools?q=&ns=       工具發現（按需 discover）
    GET  /api/tcm/resource?uri=      tcm:// 資源讀取（租戶授權 + 投影）
    POST /api/tcm/research           研究 run → AnswerEnvelope
    POST /api/tcm/tool               單工具調用（Broker 中介）
    POST /api/tcm/resume             審批/續跑（審核人身份強制核驗）

安全（P0-1/P0-3）：

* Principal **只來自服務端認證**（AuthRegistry：token→subject/tenant/
  max_role/allowed_purposes）；請求體 role 只能**降級**，不能提權。
* 401 = 未認證；403 = 已認證但越權（提權/跨租戶/審核資格不足）。
* 未配置任何 token = 匿名開發模式（public 上限，僅本機演示）。
* HERMES_TCM_READYZ_PROFILE=research 時語料未就緒 readyz 返回 503。
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .core.auth import AuthenticatedPrincipal, AuthError, AuthRegistry, AuthzError
from .core.principals import Principal
from .harness.checkpoint import RunAccessDenied
from .integrations.mcp import ResourceResolver
from .integrations.sdk import TCMClient

MAX_BODY_BYTES = 262_144


class _Service:
    """共享服務狀態（單例注入 Handler）。"""

    def __init__(self, store_path: Optional[Path] = None,
                 auth: Optional[AuthRegistry] = None):
        self.client = TCMClient(store_path=store_path)
        self.auth = auth or AuthRegistry.from_env()
        self.lock = threading.Lock()

    def close(self):
        self.client.close()


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
        if self.command != "HEAD":
            self.wfile.write(blob)

    def _authenticate(self) -> Optional[AuthenticatedPrincipal]:
        """→ 認證主體；失敗時發 401 並返回 None。"""
        try:
            return self.service.auth.authenticate(
                self.headers.get("Authorization", ""))
        except AuthError as exc:
            self._send({"error": "unauthenticated", "detail": str(exc)},
                       401)
            return None

    def _principal(self, body: Dict
                   ) -> Tuple[Optional[Principal], Optional[AuthenticatedPrincipal]]:
        """認證 + 解析執行 Principal（body.role 只能降級）。
        提權/越權 → 403。返回 (principal, auth) 或 (None, None)（已發響應）。"""
        auth = self._authenticate()
        if auth is None:
            return None, None
        try:
            principal = auth.resolve(
                requested_role=str(body.get("role", "")),
                requested_purpose=str(body.get("purpose_of_use", "")))
            return principal, auth
        except AuthzError as exc:
            self._send({"error": "forbidden", "detail": str(exc)}, 403)
            return None, None
        except ValueError as exc:
            self._send({"error": "bad_request", "detail": str(exc)}, 400)
            return None, None

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
                return self._send({"ok": True, "status": "alive"})
            if url.path == "/readyz":
                return self._readyz()
            auth = self._authenticate()
            if auth is None:
                return
            if url.path == "/api/tcm/tools":
                q = parse_qs(url.query)
                query = (q.get("q") or [""])[0]
                ns = (q.get("ns") or [""])[0]
                reg = self.service.client.registry.for_role(auth.max_role)
                if query or ns:
                    return self._send({"tools": reg.discover(
                        query=query, namespace=ns)})
                return self._send({"namespaces": reg.namespaces(),
                                   "note": "完整定義經 ?q=/?ns= 按需取"})
            if url.path == "/api/tcm/resource":
                uri = (parse_qs(url.query).get("uri") or [""])[0]
                principal = auth.resolve()
                try:
                    out = ResourceResolver(
                        run_store=self.service.client.store,
                        principal=principal).read(uri)
                except RunAccessDenied as exc:
                    return self._send({"error": "forbidden",
                                       "detail": str(exc)}, 403)
                return self._send(out)
            return self._send({"error": "not_found"}, 404)
        except Exception as exc:   # noqa: BLE001 — 不洩露內部細節
            self._send({"error": type(exc).__name__}, 500)

    def do_POST(self):
        try:
            body = self._body()
            if body is None:
                return
            url = urlparse(self.path)
            if url.path == "/api/tcm/research":
                query = str(body.get("query", "")).strip()
                if not query:
                    return self._send({"error": "query_required"}, 400)
                principal, _ = self._principal(body)
                if principal is None:
                    return
                with self.service.lock:
                    client = TCMClient(
                        store_path=self.service.client.store.path,
                        principal=principal)
                    try:
                        out = client.research(query)
                    finally:
                        client.store.close()
                return self._send(out)
            if url.path == "/api/tcm/tool":
                name = str(body.get("name", ""))
                if not name:
                    return self._send({"error": "name_required"}, 400)
                principal, _ = self._principal(body)
                if principal is None:
                    return
                client = TCMClient(store_path=self.service.client
                                   .store.path, principal=principal)
                try:
                    out = client.call_tool(
                        name, body.get("arguments") or {},
                        approved_operations=None)
                finally:
                    client.store.close()
                return self._send(out)
            if url.path == "/api/tcm/resume":
                run_id = str(body.get("run_id", ""))
                if not run_id:
                    return self._send({"error": "run_id_required"}, 400)
                reviewer, _ = self._principal(body)
                if reviewer is None:
                    return
                with self.service.lock:
                    store = self.service.client.store
                    try:
                        store.authorize(run_id, reviewer, "approve")
                    except RunAccessDenied as exc:
                        return self._send({"error": "forbidden",
                                           "detail": str(exc)}, 403)
                    out = self.service.client.controller.resume(
                        run_id,
                        approve=str(body.get("approve", "")),
                        reject=str(body.get("reject", "")),
                        reason=str(body.get("reason", ""))[:256],
                        reviewer=reviewer)
                return self._send({"run_id": run_id, "status": out["status"],
                                   "envelope": out["state"].get("envelope",
                                                                {})})
            return self._send({"error": "not_found"}, 404)
        except ValueError as exc:
            self._send({"error": "bad_request",
                        "detail": str(exc)[:200]}, 400)
        except Exception as exc:   # noqa: BLE001
            self._send({"error": type(exc).__name__}, 500)

    # ------------------------------------------------------------------
    def _readyz(self):
        """部署 profile 就緒（P1-12：不返回假健康）。研究 profile 下
        語料未就緒返回 503。"""
        from .corpus.fingerprint import corpus_manifest_summary
        reg = self.service.client.registry
        summary = corpus_manifest_summary()
        profile = os.environ.get("HERMES_TCM_READYZ_PROFILE", "any")
        failed = []
        if profile == "research" and not summary["ready"]:
            failed = ["corpus", "index"]
        payload = {"ok": not failed,
                   "profile": profile,
                   "corpus": summary,
                   "n_tools": len(reg.names()),
                   "failed_checks": failed}
        self._send(payload, 200 if not failed else 503)


def make_server(host: str = "127.0.0.1", port: int = 0,
                store_path: Optional[Path] = None,
                auth: Optional[AuthRegistry] = None) -> ThreadingHTTPServer:
    """構建服務器（port=0 取臨時端口；調用方負責 serve_forever/shutdown）。"""
    service = _Service(store_path=store_path, auth=auth)
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
