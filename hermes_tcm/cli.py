"""hermes-tcm CLI（Protocol §17 / P2）：把新內核做成第一等產品入口，
不再只是舊項目的內部模塊。

    hermes-tcm research "<query>"          研究 run → AnswerEnvelope
    hermes-tcm tool <name> [--args JSON]   單工具調用
    hermes-tcm discover [--q ..] [--ns ..] 工具發現
    hermes-tcm resource <uri>              tcm:// 資源讀取
    hermes-tcm corpus status|version       jicheng 全庫狀態/指紋
    hermes-tcm corpus fetch                下載笈成全庫（委托 shanghan）
    hermes-tcm serve [--host --port]       HTTP 服務
    hermes-tcm serve-mcp [--transport stdio]  MCP Server
    hermes-tcm eval                        六層評測 + P0 門檻概覽
    hermes-tcm replay <run_id> [--mode ..] 重放（strict/evidence/policy）

離線可跑；語料未就緒時檢索類命令如實返回 corpus_unavailable。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from . import DISTRIBUTION_VERSION, __version__


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _client(role: str = "researcher"):
    from .core.principals import Principal
    from .integrations.sdk import TCMClient
    return TCMClient(principal=Principal(subject="cli", role=role))


def cmd_research(args) -> int:
    client = _client(args.role)
    try:
        out = client.research(args.query, task_type=args.task_type or None)
    finally:
        client.close()
    _print(out)
    return 0


def cmd_tool(args) -> int:
    arguments = json.loads(args.args) if args.args else {}
    client = _client(args.role)
    try:
        out = client.call_tool(args.name, arguments)
    finally:
        client.close()
    _print(out)
    return 0 if not (out.get("result") or {}).get("error") else 1


def cmd_discover(args) -> int:
    from .tools.registry import get_tcm_registry
    reg = get_tcm_registry()
    if args.q or args.ns:
        _print(reg.discover(query=args.q or "", namespace=args.ns or ""))
    else:
        _print(reg.namespaces())
    return 0


def cmd_resource(args) -> int:
    client = _client(args.role)
    try:
        _print(client.read_resource(args.uri))
    finally:
        client.close()
    return 0


def cmd_corpus(args) -> int:
    from .corpus.fingerprint import corpus_manifest_summary
    if args.corpus_cmd == "fetch":
        from hermes_shanghan.corpus import library
        root = library.fetch()
        _print({"fetched": str(root),
                "corpus": corpus_manifest_summary()})
        return 0
    _print(corpus_manifest_summary())      # status / version
    return 0


def cmd_serve(args) -> int:
    from .server import serve
    print(f"hermes-tcm HTTP 服務 http://{args.host}:{args.port}/  "
          "（開發服務器；生產請走 ASGI+PostgreSQL）", file=sys.stderr)
    serve(host=args.host, port=args.port)
    return 0


def cmd_serve_mcp(args) -> int:
    if args.transport != "stdio":
        _print({"error": f"transport {args.transport} 未就緒",
                "note": "Streamable HTTP + OAuth 資源保護屬規劃層；"
                        "當前僅 stdio。"})
        return 1
    from .integrations.mcp_server import serve_stdio
    serve_stdio()
    return 0


def cmd_eval(args) -> int:
    from .evals.layers import run_all_layers
    from .evals.p0_gates import P0_GATES
    from .tools._shared import work_registry
    reg = None
    try:
        reg = work_registry()
    except Exception:
        reg = None
    _print({"layers": run_all_layers(work_registry=reg),
            "p0_gates": sorted(P0_GATES)})
    return 0


def cmd_replay(args) -> int:
    from .harness.checkpoint import RunStore
    from .harness.controller import ResearchRunController
    from .harness.replay import (replay_evidence, replay_policy,
                                 replay_strict)
    from hermes_shanghan import config
    store = RunStore(config.DATA_DIR / "tcm_runs" / "runs.db")
    try:
        if args.mode == "evidence":
            _print(replay_evidence(store, args.run_id))
        elif args.mode == "policy":
            _print(replay_policy(store, args.run_id))
        else:
            ctrl = ResearchRunController(store)
            _print(replay_strict(store, ctrl, args.run_id))
    finally:
        store.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hermes-tcm",
        description="全中醫古籍證據與研究操作系統（Hermes-TCM 內核）")
    p.add_argument("--version", action="version",
                   version=f"hermes-tcm kernel {__version__} "
                           f"(distribution {DISTRIBUTION_VERSION})")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("research", help="研究 run → AnswerEnvelope")
    sp.add_argument("query")
    sp.add_argument("--role", default="researcher")
    sp.add_argument("--task-type", dest="task_type", default="")
    sp.set_defaults(func=cmd_research)

    sp = sub.add_parser("tool", help="單工具調用")
    sp.add_argument("name")
    sp.add_argument("--args", default="", help="JSON 參數")
    sp.add_argument("--role", default="researcher")
    sp.set_defaults(func=cmd_tool)

    sp = sub.add_parser("discover", help="工具發現")
    sp.add_argument("--q", default="")
    sp.add_argument("--ns", default="")
    sp.set_defaults(func=cmd_discover)

    sp = sub.add_parser("resource", help="tcm:// 資源讀取")
    sp.add_argument("uri")
    sp.add_argument("--role", default="researcher")
    sp.set_defaults(func=cmd_resource)

    sp = sub.add_parser("corpus", help="笈成全庫狀態/下載")
    sp.add_argument("corpus_cmd", choices=["status", "version", "fetch"],
                    nargs="?", default="status")
    sp.set_defaults(func=cmd_corpus)

    sp = sub.add_parser("serve", help="HTTP 服務（開發）")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8766)
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("serve-mcp", help="MCP Server")
    sp.add_argument("--transport", default="stdio",
                    choices=["stdio", "streamable-http"])
    sp.set_defaults(func=cmd_serve_mcp)

    sp = sub.add_parser("eval", help="六層評測 + P0 門檻概覽")
    sp.set_defaults(func=cmd_eval)

    sp = sub.add_parser("replay", help="重放")
    sp.add_argument("run_id")
    sp.add_argument("--mode", default="strict",
                    choices=["strict", "evidence", "policy"])
    sp.set_defaults(func=cmd_replay)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
