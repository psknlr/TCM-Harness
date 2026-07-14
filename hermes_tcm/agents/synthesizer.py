"""Synthesizer：只基於已驗證主張綜合（Protocol §11.1）。

輸入是各專家的結構化 SpecialistReport（不含推理過程）+ 獨立核驗
結果；輸出是不新增事實的綜合文本。failed 主張不進入表達；
needs_review 主張帶顯著標記。
"""
from __future__ import annotations

from typing import Dict, List, Sequence

from ..claims.records import ClaimRecord


class Synthesizer:
    def compose(self, claims: Sequence[ClaimRecord],
                conflicts: Sequence[Dict] = ()) -> Dict:
        lines: List[str] = []
        for c in claims:
            if c.status == "failed":
                continue
            qualifier = "".join(f"（{q}）" for q in c.forced_qualifiers)
            marker = "" if c.status == "verified" else "【待人工審核】"
            lines.append(f"{marker}{c.claim_text}{qualifier}")
        if conflicts:
            lines.append("【專家分歧】" + "；".join(
                f"{c['claim_type']}：{len(c['divergent_texts'])} 種表述"
                for c in conflicts))
        answer = "。".join(lines) + ("。" if lines else "")
        return {"answer": answer or "（無可發布結論）",
                "n_claims_used": sum(1 for c in claims
                                     if c.status != "failed"),
                "n_claims_dropped": sum(1 for c in claims
                                        if c.status == "failed"),
                "note": "綜合只重排已驗證主張的表達，不新增事實"}
