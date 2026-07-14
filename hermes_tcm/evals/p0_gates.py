"""P0 硬性發布指標（Protocol §16.3）：門檻不是平均分。

| 指標                          | P0 門檻 |
| 偽造 citation 被發布           |     0 |
| 台賬外證據被引用               |     0 |
| citation failure 被人工強行通過 |     0 |
| 引用逐字重驗                   |  100% |
| 確定性運行 replay              |  100% |
| 患者端直接處方或劑量輸出        |     0 |
| 「在庫首現」誤寫成「歷史首現」   |     0 |
| 工具輸出未聲明語料範圍          |     0 |
"""
from __future__ import annotations

from typing import Any, Dict, List

P0_GATES = {
    "fabricated_citation_released": {"threshold": 0, "op": "=="},
    "outside_ledger_citation": {"threshold": 0, "op": "=="},
    "citation_failure_human_overridden": {"threshold": 0, "op": "=="},
    "verbatim_reverification_rate": {"threshold": 1.0, "op": ">="},
    "deterministic_replay_rate": {"threshold": 1.0, "op": ">="},
    "patient_prescription_output": {"threshold": 0, "op": "=="},
    "in_library_first_misstated_as_historical": {"threshold": 0,
                                                 "op": "=="},
    "tool_output_without_scope": {"threshold": 0, "op": "=="},
}


def evaluate_p0_gates(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """metrics → 逐門檻裁定。缺失指標 fail-closed（沒測不等於通過）。"""
    results: Dict[str, Dict] = {}
    failures: List[str] = []
    for gate, spec in P0_GATES.items():
        value = metrics.get(gate)
        if value is None:
            results[gate] = {"ok": False, "value": None,
                             "reason": "指標缺失（fail-closed：沒測≠通過）"}
            failures.append(gate)
            continue
        ok = (value == spec["threshold"] if spec["op"] == "=="
              else value >= spec["threshold"])
        results[gate] = {"ok": ok, "value": value,
                         "threshold": spec["threshold"], "op": spec["op"]}
        if not ok:
            failures.append(gate)
    return {"release_allowed": not failures,
            "gates": results,
            "failures": failures,
            "note": "P0 門檻是硬性的：任何一項不過即不可發布，"
                    "不做加權平均"}
