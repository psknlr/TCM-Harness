"""工具規格導出（Protocol §17 integrations/{openai,anthropic}）。

Claude Code、Codex 和 Python SDK 使用同一工具語義（Phase 3 退出
條件）：三種格式從同一 ToolContractV2 導出，內容哈希入指紋。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, Optional

from ..tools.registry import get_tcm_registry


def export_openai_tools() -> list:
    reg = get_tcm_registry()
    return [reg.get(n).openai_spec() for n in reg.names()]


def export_anthropic_tools() -> list:
    reg = get_tcm_registry()
    return [reg.get(n).anthropic_spec() for n in reg.names()]


def export_mcp_tools() -> list:
    reg = get_tcm_registry()
    return [reg.get(n).mcp_spec() for n in reg.names()]


def export_all(path: Optional[Path] = None) -> Dict:
    """全量規格包（含契約與指紋）；path 給定時落盤 JSON。"""
    reg = get_tcm_registry()
    payload = reg.export()
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    payload["spec_fingerprint"] = hashlib.sha256(
        blob.encode("utf-8")).hexdigest()[:12]
    if path is not None:
        Path(path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1),
            encoding="utf-8")
    return payload
