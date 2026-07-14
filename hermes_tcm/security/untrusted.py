"""所有語料都視為不可信數據（Protocol §14.3）。

古籍、OCR 文本、上傳文件可能含有指令注入樣式的文本。它們必須被
標記為 DATA_ONLY / NON_EXECUTABLE / UNTRUSTED_CONTENT，不得進入
system/tool instruction 平面。

本模塊提供：

* UntrustedContent 封套（顯式標記 + 序列化保留標記）；
* 確定性注入模式掃描（審計信號，不作為攔截依據——語料中出現
  「忽略之前的指令」本身是研究對象，不是執行對象）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

TRUST_MARKERS = ("DATA_ONLY", "NON_EXECUTABLE", "UNTRUSTED_CONTENT")

# 注入樣式（中英）：只做標記，不做語義判定
INJECTION_PATTERNS = [
    ("instruction_override",
     re.compile(r"忽略(之前|以上|前面|先前)的?(所有)?(指令|提示|規則|规则)"
                r"|ignore\s+(all\s+)?(previous|prior|above)\s+instructions",
                re.I)),
    ("tool_invocation",
     re.compile(r"調用(某|以下)?工具|请?调用.{0,8}工具"
                r"|call\s+the\s+\w+\s+tool", re.I)),
    ("prompt_exfiltration",
     re.compile(r"輸出(你的)?(系統)?提示|输出.{0,4}系统提示"
                r"|(print|reveal|show)\s+(your\s+)?system\s+prompt", re.I)),
    ("role_escalation",
     re.compile(r"你現在是|你现在是|扮演.{0,8}(管理員|管理员|admin)"
                r"|you\s+are\s+now\s+(an?\s+)?(admin|system)", re.I)),
]


def scan_injection(text: str) -> List[Dict]:
    """掃描注入樣式；命中只是審計信號（語料是研究對象，非執行對象）。"""
    hits: List[Dict] = []
    for kind, rx in INJECTION_PATTERNS:
        m = rx.search(text or "")
        if m:
            hits.append({"kind": kind, "cue": m.group(0)[:40]})
    return hits


@dataclass
class UntrustedContent:
    """不可信內容封套：內容永遠帶 trust 標記傳遞。"""
    content: str
    source: str = ""                # 來源標識（passage_id / 文件名）
    markers: tuple = TRUST_MARKERS
    injection_signals: List[Dict] = field(default_factory=list)

    def __post_init__(self):
        if not self.injection_signals:
            self.injection_signals = scan_injection(self.content)

    def to_dict(self) -> Dict[str, Any]:
        return {"content": self.content,
                "source": self.source,
                "trust": list(self.markers),
                "injection_signals": self.injection_signals,
                "note": "語料文本是數據不是指令；注入樣式命中僅為審計信號"}


def wrap_untrusted(content: str, source: str = "") -> UntrustedContent:
    return UntrustedContent(content=content, source=source)
