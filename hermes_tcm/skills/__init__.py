"""Skills：漸進披露的操作流程包（Protocol §12.2）。

開始只暴露名稱、描述和路徑；真正選擇該 Skill 後才加載完整
SKILL.md（progressive disclosure，控制上下文佔用）。

SKILL.md 格式：YAML front-matter（name/description/use_when/
task_types）+ Markdown 正文（操作步驟）。純標準庫解析（front-matter
是受限子集：`key: value` 與 `key:` 後多行 `- item` 列表與 `>` 折行）。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List, Optional

SKILLS_DIR = Path(__file__).resolve().parent


def _parse_front_matter(text: str) -> Dict:
    """受限 YAML front-matter 解析（--- 分隔）。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {"meta": {}, "body": text}
    meta: Dict = {}
    i = 1
    key = None
    folded = False
    buf: List[str] = []
    while i < len(lines):
        line = lines[i]
        if line.strip() == "---":
            i += 1
            break
        if folded and (line.startswith("  ") or not line.strip()):
            buf.append(line.strip())
            i += 1
            continue
        if folded:
            meta[key] = " ".join(x for x in buf if x)
            folded = False
            buf = []
        if line.startswith("  - ") and key:
            meta.setdefault(key, [])
            if isinstance(meta[key], list):
                meta[key].append(line.strip()[2:].strip())
            i += 1
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if val == ">":
                folded = True
                buf = []
            elif val == "":
                meta[key] = []
            else:
                meta[key] = val
        i += 1
    if folded and key:
        meta[key] = " ".join(x for x in buf if x)
    return {"meta": meta, "body": "\n".join(lines[i:])}


def list_skills() -> List[Dict]:
    """頂層可發現面：名稱+描述+路徑（不含正文——按需加載）。"""
    out: List[Dict] = []
    for d in sorted(SKILLS_DIR.iterdir()):
        f = d / "SKILL.md"
        if not (d.is_dir() and f.exists()):
            continue
        parsed = _parse_front_matter(f.read_text(encoding="utf-8"))
        meta = parsed["meta"]
        out.append({"name": meta.get("name", d.name),
                    "description": meta.get("description", ""),
                    "task_types": meta.get("task_types", []),
                    "path": str(f.relative_to(SKILLS_DIR.parent.parent))})
    return out


def load_skill(name: str) -> Optional[Dict]:
    """完整加載（progressive disclosure 的第二步）。"""
    for d in sorted(SKILLS_DIR.iterdir()):
        f = d / "SKILL.md"
        if not (d.is_dir() and f.exists()):
            continue
        parsed = _parse_front_matter(f.read_text(encoding="utf-8"))
        if parsed["meta"].get("name", d.name) == name or d.name == name:
            return {"name": parsed["meta"].get("name", d.name),
                    "description": parsed["meta"].get("description", ""),
                    "task_types": parsed["meta"].get("task_types", []),
                    "body": parsed["body"].strip()}
    return None


def skill_for_task(task_type: str) -> Optional[Dict]:
    """task_type → 匹配技能（只在任務匹配時加載全文）。"""
    for entry in list_skills():
        if task_type in (entry.get("task_types") or []):
            return load_skill(entry["name"])
    return None


def skills_fingerprint() -> str:
    """全部 SKILL.md 內容聚合哈希（環境指紋組件）。"""
    h = hashlib.sha256()
    for d in sorted(SKILLS_DIR.iterdir()):
        f = d / "SKILL.md"
        if d.is_dir() and f.exists():
            h.update(d.name.encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:12]
