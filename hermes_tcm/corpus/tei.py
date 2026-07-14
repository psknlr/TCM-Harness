"""TEI P5 critical apparatus 導出（Protocol §5.3）。

用 app/lem/rdg + witness 表達不同傳本的異文：

    <app xml:id="app_0012_03">
      <lem wit="#zhaokaimei">脈浮緩</lem>
      <rdg wit="#guiguben">脈浮而緩</rdg>
    </app>

純標準庫實現（xml.sax.saxutils 轉義）；輸入是校勘條目結構，
輸出是可嵌入 TEI 文檔的 apparatus 片段與最小完整文檔。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List
from xml.sax.saxutils import escape, quoteattr


@dataclass
class Reading:
    witness_id: str      # urn:tcm:witness:... 或本地 xml:id
    text: str
    is_lemma: bool = False


@dataclass
class ApparatusEntry:
    app_id: str
    readings: List[Reading] = field(default_factory=list)
    location: str = ""     # 對齊定位說明（passage/char 座標）

    def __post_init__(self):
        lemmas = [r for r in self.readings if r.is_lemma]
        if len(lemmas) > 1:
            raise ValueError(f"apparatus {self.app_id} 有多個 lemma"
                             "（底本讀法只能有一個）")


def _wit_ref(witness_id: str) -> str:
    """witness URN → TEI wit 引用（xml:id 形式）。"""
    return "#" + witness_id.replace(":", "_").replace("/", "_")


def apparatus_xml(entry: ApparatusEntry) -> str:
    parts = [f"<app xml:id={quoteattr(entry.app_id)}"
             + (f" loc={quoteattr(entry.location)}" if entry.location else "")
             + ">"]
    for r in entry.readings:
        tag = "lem" if r.is_lemma else "rdg"
        parts.append(f"  <{tag} wit={quoteattr(_wit_ref(r.witness_id))}>"
                     f"{escape(r.text)}</{tag}>")
    parts.append("</app>")
    return "\n".join(parts)


def witness_list_xml(witnesses: List[Dict]) -> str:
    """listWit 片段：witness_id + 描述。"""
    parts = ["<listWit>"]
    for w in witnesses:
        xml_id = _wit_ref(w["witness_id"]).lstrip("#")
        parts.append(f"  <witness xml:id={quoteattr(xml_id)}>"
                     f"{escape(w.get('title', ''))}"
                     + (f"（{escape(w['edition_statement'])}）"
                        if w.get("edition_statement") else "")
                     + "</witness>")
    parts.append("</listWit>")
    return "\n".join(parts)


def export_tei_document(title: str, witnesses: List[Dict],
                        entries: List[ApparatusEntry]) -> str:
    """最小完整 TEI 文檔：teiHeader（listWit）+ body（apparatus 序列）。"""
    apps = "\n".join(apparatus_xml(e) for e in entries)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>{escape(title)}</title></titleStmt>
      <sourceDesc>
{_indent(witness_list_xml(witnesses), 8)}
      </sourceDesc>
    </fileDesc>
  </teiHeader>
  <text>
    <body>
      <div type="apparatus">
{_indent(apps, 8)}
      </div>
    </body>
  </text>
</TEI>
"""


def _indent(block: str, n: int) -> str:
    pad = " " * n
    return "\n".join(pad + line for line in block.splitlines())
