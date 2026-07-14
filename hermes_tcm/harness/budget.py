"""RunBudget V2：工具調用/子代理/牆鐘/token/成本 統一預算（Protocol §10.2）。

原子扣減；達到預算即停（超限調用返回 BUDGET_EXHAUSTED 不執行）；
計數器屬於 run 不屬於進程（resume 時由持久化狀態重建）。
"""
from __future__ import annotations

import threading
import time
from typing import Dict

from .run_spec import BudgetSpec


class RunBudgetV2:
    def __init__(self, spec: BudgetSpec = None):
        self.spec = spec or BudgetSpec()
        self.used_tool_calls = 0
        self.denied_tool_calls = 0
        self.used_subagents = 0
        self.used_input_tokens = 0
        self.used_cost = 0.0
        self._t0 = time.time()
        self._lock = threading.Lock()

    def reserve_tool_call(self, tool_name: str = "") -> bool:
        with self._lock:
            if self.spec.max_wall_ms and \
                    (time.time() - self._t0) * 1000 > self.spec.max_wall_ms:
                self.denied_tool_calls += 1
                return False
            if self.used_tool_calls >= self.spec.max_tool_calls:
                self.denied_tool_calls += 1
                return False
            self.used_tool_calls += 1
            return True

    def reserve_subagent(self, name: str = "") -> bool:
        with self._lock:
            if self.used_subagents >= self.spec.max_subagents:
                return False
            self.used_subagents += 1
            return True

    def add_tokens(self, n: int) -> bool:
        """記賬並返回是否仍在預算內（token 計量在真實 LLM 後端下有效；
        deterministic 後端如實記 0）。"""
        with self._lock:
            self.used_input_tokens += max(0, int(n))
            return self.used_input_tokens <= self.spec.max_input_tokens

    def add_cost(self, c: float) -> bool:
        with self._lock:
            self.used_cost += max(0.0, float(c))
            return self.used_cost <= self.spec.max_cost

    def snapshot(self) -> Dict:
        with self._lock:
            return {
                "max_tool_calls": self.spec.max_tool_calls,
                "used_tool_calls": self.used_tool_calls,
                "denied_tool_calls": self.denied_tool_calls,
                "max_subagents": self.spec.max_subagents,
                "used_subagents": self.used_subagents,
                "max_input_tokens": self.spec.max_input_tokens,
                "used_input_tokens": self.used_input_tokens,
                "max_cost": self.spec.max_cost,
                "used_cost": round(self.used_cost, 4),
                "elapsed_ms": int((time.time() - self._t0) * 1000),
                "max_wall_ms": self.spec.max_wall_ms,
            }

    def restore(self, used_tool_calls: int = 0, used_subagents: int = 0,
                used_input_tokens: int = 0, used_cost: float = 0.0) -> None:
        """resume：預算屬於 run，不屬於進程。"""
        with self._lock:
            self.used_tool_calls = used_tool_calls
            self.used_subagents = used_subagents
            self.used_input_tokens = used_input_tokens
            self.used_cost = used_cost
