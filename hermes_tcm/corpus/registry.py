"""WorkRegistry：從笈成編目確定性構建 Work—Witness—Edition—Item 身份鏈。

歸組規則（確定性，不用 LLM 判斷同名異書——必須避免的錯誤之三）：

1. 折疊書名（work_base_title：去傳本後綴 + 異體字折疊）為初始分組鍵。
2. 同組內 author/dynasty 沖突 → **不歸併**：拆分為帶消歧鍵的獨立
   Work，identity_status=needs_review，衝突字段全量記錄。
3. 傳本後綴（宋本/桂本/條文版/…）只區分 Witness，不拆分 Work。
4. 每次歸組輸出 IdentityResolution：匹配依據 / 衝突字段 / 置信度 /
   是否需人工裁決——自動歸併必須可審計（Protocol §5.1 原則 4）。
5. work_id 由 sha256 slug 生成，與展示標題解耦，一經發布不隨書名變化。

authority overrides：`curated_works` 允許人工權威記錄覆蓋自動歸組
（identity_status=curated 的 Work 不再被自動規則改動）。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

from hermes_shanghan.classics.model import work_base_title
from hermes_shanghan.corpus import library as _lib

from ..core.identity import (DigitalItem, IdentityResolution, WitnessRecord,
                             WorkRecord, classify_source_type,
                             detect_recension, edition_urn, item_urn,
                             merge_conflicts, witness_urn, work_urn)


def _unit_conflicts(a: Dict, b: Dict) -> List[Dict]:
    """歸併衝突檢測（傳本感知）：author 衝突恆為信號；dynasty 衝突僅在
    雙方都無傳本後綴時計入——同一著作不同刊本的刊刻朝代不同是常態
    （宋本/明刊本），不是同名異書信號。"""
    out = merge_conflicts(a, b)
    if detect_recension(a.get("title", "")) \
            or detect_recension(b.get("title", "")):
        out = [c for c in out if c["field"] != "dynasty"]
    return out


class WorkRegistry:
    """身份鏈註冊表：Library 編目 → Work/Witness/Edition/Item。

    純內存派生視圖（與 PassageIndex 同一哲學）：同一編目輸入永遠產出
    同一批 URN——確定性、可重放。
    """

    def __init__(self, lib: Optional[_lib.Library] = None,
                 curated_works: Optional[List[Dict]] = None):
        self.lib = lib or _lib.Library()
        self.works: Dict[str, WorkRecord] = {}
        self.witnesses: Dict[str, WitnessRecord] = {}
        self.items: Dict[str, DigitalItem] = {}
        self.resolutions: List[IdentityResolution] = []
        self._witness_by_unit: Dict[str, str] = {}
        self._works_by_base: Dict[str, List[str]] = defaultdict(list)
        self._curated = {w["canonical_title"]: w for w in (curated_works or [])}
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        units = getattr(self.lib, "units", []) or []
        groups: Dict[str, List[Dict]] = defaultdict(list)
        for u in units:
            groups[work_base_title(u["title"])].append(u)

        for base, members in sorted(groups.items()):
            self._build_group(base, members)

    def _build_group(self, base: str, members: List[Dict]) -> None:
        """一個折疊書名組 → 一個或多個 Work（衝突即拆分，不猜）。"""
        # 按 (author, dynasty) 特徵桶分桶；空字段桶掛靠到首個非空桶
        # 僅當**無衝突**（同名+雙空字段本身不是歸併證據，保守掛靠並降置信）
        buckets: List[List[Dict]] = []
        for u in members:
            placed = False
            for b in buckets:
                if not _unit_conflicts(b[0], u):
                    b.append(u)
                    placed = True
                    break
            if placed:
                continue
            buckets.append([u])

        multi = len(buckets) > 1
        for i, bucket in enumerate(sorted(
                buckets, key=lambda b: (b[0].get("author") or "",
                                        b[0].get("dynasty") or ""))):
            head = bucket[0]
            disambiguator = ""
            if multi:
                disambiguator = (f"{head.get('author') or '佚名'}"
                                 f"|{head.get('dynasty') or '未詳'}")
            curated = self._curated.get(base)
            if curated and not multi:
                wid = curated.get("work_id") or work_urn(base)
                status = "curated"
            else:
                wid = work_urn(base, disambiguator)
                status = "needs_review" if multi else "auto_grouped"
            authors = sorted({u.get("author", "") for u in bucket} - {""})
            dynasties = sorted({u.get("dynasty", "") for u in bucket} - {""})
            work = WorkRecord(
                work_id=wid,
                canonical_title=base + (f"（{disambiguator}）" if multi else ""),
                title_aliases=sorted({u["title"] for u in bucket}),
                attributed_authors=authors,
                work_period=dynasties[0] if len(dynasties) == 1 else "",
                genre=head.get("category", ""),
                identity_status=status,
                notes=("同名異書拆分：author/dynasty 衝突不自動歸併"
                       if multi else ""))
            self.works[wid] = work
            self._works_by_base[base].append(wid)

            conflicts: List[Dict] = []
            for other in bucket[1:]:
                conflicts.extend(_unit_conflicts(head, other))
            confidence = 0.95 if status == "curated" else \
                (0.6 if multi else (0.85 if authors or dynasties else 0.7))
            self.resolutions.append(IdentityResolution(
                query=base,
                resolved_work_id=wid,
                matched_on=["folded_base_title"]
                + (["author", "dynasty"] if (authors or dynasties) else []),
                conflicting_fields=conflicts,
                confidence=confidence,
                needs_human_adjudication=multi,
                candidates=[{"unit_id": u["id"], "title": u["title"],
                             "author": u.get("author", ""),
                             "dynasty": u.get("dynasty", "")} for u in bucket],
                note=("同名組存在 author/dynasty 衝突，已按特徵桶拆分，"
                      "需人工裁決" if multi else "")))

            for u in bucket:
                self._add_witness(work, u)

    def _add_witness(self, work: WorkRecord, unit: Dict) -> None:
        wid = witness_urn(unit["id"])
        edition = unit.get("edition", "") or ""
        witness = WitnessRecord(
            witness_id=wid,
            work_id=work.work_id,
            unit_id=unit["id"],
            title=unit["title"],
            recension=detect_recension(unit["title"]),
            edition_statement=edition,
            edition_id=edition_urn(unit["id"], edition),
            publication_period=unit.get("dynasty", ""),
            source_type=classify_source_type(unit["title"],
                                             unit.get("dynasty", "")),
            author=unit.get("author", ""),
            dynasty=unit.get("dynasty", ""),
            category=unit.get("category", ""),
            item_id=item_urn(unit["id"]))
        self.witnesses[wid] = witness
        self._witness_by_unit[unit["id"]] = wid
        work.witness_ids.append(wid)
        self.items[witness.item_id] = DigitalItem(
            item_id=witness.item_id, witness_id=wid,
            files=list(unit.get("files") or []),
            source_sha256=self.lib.catalog.get("archive_sha256", ""))

    # ------------------------------------------------------------------
    # 查詢面
    # ------------------------------------------------------------------
    def witness_for_unit(self, unit_id: str) -> Optional[WitnessRecord]:
        wid = self._witness_by_unit.get(unit_id)
        return self.witnesses.get(wid) if wid else None

    def work_for_unit(self, unit_id: str) -> Optional[WorkRecord]:
        w = self.witness_for_unit(unit_id)
        return self.works.get(w.work_id) if w else None

    def resolve_work(self, title: str) -> IdentityResolution:
        """書名 → Work 解析（含別名/折疊匹配）；多義如實返回全部候選。"""
        base = work_base_title(title)
        wids = self._works_by_base.get(base, [])
        if not wids:
            # 全量別名掃描（傳本標題也可作查詢入口）
            hits = [w for w in self.works.values()
                    if any(base == work_base_title(a) or title == a
                           for a in w.title_aliases)]
            wids = [w.work_id for w in hits]
        if not wids:
            return IdentityResolution(
                query=title, note="未命中任何 Work（在本註冊表範圍內）")
        if len(wids) == 1:
            w = self.works[wids[0]]
            return IdentityResolution(
                query=title, resolved_work_id=w.work_id,
                matched_on=["folded_base_title"],
                confidence=0.9 if w.identity_status != "needs_review" else 0.6,
                needs_human_adjudication=w.identity_status == "needs_review",
                candidates=[{"work_id": w.work_id,
                             "canonical_title": w.canonical_title}])
        return IdentityResolution(
            query=title,
            matched_on=["folded_base_title"],
            confidence=0.4,
            needs_human_adjudication=True,
            candidates=[{"work_id": wid,
                         "canonical_title": self.works[wid].canonical_title,
                         "authors": self.works[wid].attributed_authors,
                         "period": self.works[wid].work_period}
                        for wid in wids],
            note="同名異書：多個 Work 候選，需指定作者/朝代或人工裁決")

    def stats(self) -> Dict:
        return {
            "n_works": len(self.works),
            "n_witnesses": len(self.witnesses),
            "n_items": len(self.items),
            "n_needs_review": sum(1 for w in self.works.values()
                                  if w.identity_status == "needs_review"),
            "n_curated": sum(1 for w in self.works.values()
                             if w.identity_status == "curated"),
            "library_fingerprint": self.lib.catalog.get("archive_sha256", ""),
        }
