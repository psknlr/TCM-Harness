"""Capability Broker：工具調用中介 + 證據台賬唯一寫入口（Protocol §4/§9）。

管道（逐項執行，不是願望清單）：

    角色裁剪 → 目的限制 → 參數校驗 → 審批檢查 → 預算扣減 → 緩存
    → 超時執行 → 輸出契約檢查 → 證據轉換登記（EvidenceRecord V2）
    → 覆蓋記錄登記 → 審計日誌

強不變量：

* 只有本 Broker 持鑄造令牌，能向 TypedEvidenceLedger 寫入；
* 工具結果中的 passage_evidence 逐條轉換為 EvidenceRecord V2 並
  綁定 tool_call_id/span_id/corpus_version；
* 帶 requires_coverage_record 契約的工具若未返回 coverage，
  記 guardrail 事件（工具輸出不得不聲明語料範圍）；
* 非只讀工具必須先有已批准的 ApprovalRequest。
"""
from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from collections import deque
from typing import Any, Dict, List, Optional

from ..core.principals import Principal
from ..core.policies import purpose_allows
from ..evidence.coverage import SearchCoverage
from ..evidence.ledger import TypedEvidenceLedger, mint_broker_token
from ..evidence.records import from_legacy_p_record
from .adapters import resolve_legacy_tool
from .contracts import ToolContractV2
from .registry import ToolNamespaceRegistry

MAX_RESULT_BYTES = 262_144


class BrokerTimeout(Exception):
    pass


class CapabilityBroker:
    def __init__(self, registry: ToolNamespaceRegistry,
                 ledger: TypedEvidenceLedger,
                 principal: Optional[Principal] = None,
                 budget=None,
                 corpus_version: str = "",
                 approved_operations: Optional[List[str]] = None,
                 trace=None, parent_span_id: Optional[str] = None,
                 scope=None):
        self.registry = registry
        self.ledger = ledger
        self.principal = principal or Principal(subject="anonymous",
                                                role="researcher")
        self.budget = budget
        self.corpus_version = corpus_version or ledger.corpus_version
        self.approved_operations = set(approved_operations or [])
        self.trace = trace
        self.parent_span_id = parent_span_id
        # ScopeContract：非 None 且非全庫時，每次檢索受其約束（P0-4）
        self.scope = scope
        # 節點級工具白名單（P0-6）；None=不限（由角色/目的兜底）
        self.node_tool_scope: Optional[set] = None
        self.deadline: Optional[float] = None      # 節點截止（P0-6/P0-8）
        self.node_budget: int = 0                  # 0=不限（節點級預算）
        self._node_calls: List[int] = [0]
        self.audit_log: deque = deque(maxlen=256)
        self.coverages: Dict[str, SearchCoverage] = {}
        self.tool_calls: List[Dict] = []
        self.guardrail_events: List[Dict] = []
        self._token = mint_broker_token("capability_broker")
        self._cache: Dict[str, Dict] = {}
        self._lock = threading.Lock()

    def for_node(self, node_contract, deadline: Optional[float] = None):
        """節點受限視圖（P0-6）：工具白名單 = 節點 tool_scope 命名空間；
        deadline 供協作式取消；node budget 限本節點工具調用數。共享
        台賬/預算/覆蓋/scope（同一 run）。"""
        import copy as _copy
        sub = _copy.copy(self)
        scopes = getattr(node_contract, "tool_scope", None) or []
        sub.node_tool_scope = set(scopes) if scopes else None
        sub.deadline = deadline
        sub.node_budget = getattr(node_contract, "budget_tool_calls", 0) or 0
        sub._node_calls = [0]      # 本節點獨立計數（不共享）
        return sub

    # ------------------------------------------------------------------
    def call(self, name: str, arguments: Optional[Dict] = None,
             node_id: str = "execute") -> Dict:
        arguments = dict(arguments or {})
        t0 = time.time()
        span_id = uuid.uuid4().hex[:16]

        def _finish(out: Dict, cache_hit: bool = False) -> Dict:
            entry = {"tool": name, "span_id": span_id,
                     "tool_call_id": span_id,
                     # available:False（庫未就緒）不算成功調用
                     "ok": "error" not in out
                           and out.get("available", True) is not False,
                     "error": out.get("error"),
                     "ms": int((time.time() - t0) * 1000),
                     "cache_hit": cache_hit,
                     "node_id": node_id,
                     "principal": self.principal.subject,
                     "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            self.audit_log.append(entry)
            self.tool_calls.append(entry)
            return out

        # 0. legacy 名稱適配（P0-10：shanghan_*/classics_* 兼容入口）
        resolved = name
        if "." not in name:
            mapped = resolve_legacy_tool(name)
            if mapped is None:
                return _finish({"error": f"unknown tool: {name}",
                                "hint": "新命名空間見 registry.namespaces()；"
                                        "legacy 名稱僅支持已映射的 "
                                        "shanghan_*/classics_* 工具"})
            resolved = mapped["tool"]
            arguments = {**mapped.get("default_arguments", {}), **arguments}

        contract = self.registry.get(resolved)
        if contract is None:      # 默認拒絕：不在註冊表=不可調用
            return _finish({"error": f"unknown tool: {resolved}",
                            "available_namespaces":
                                sorted(self.registry.namespaces())})

        # 0b. 節點工具白名單（P0-6）：受限上下文只能調本節點 tool_scope
        if self.node_tool_scope is not None \
                and contract.namespace not in self.node_tool_scope:
            self.guardrail_events.append(
                {"event": "node_tool_scope_denied", "tool": resolved,
                 "node_scope": sorted(self.node_tool_scope)})
            return _finish({"error": f"node_tool_scope_denied：{resolved} "
                                     f"不在節點允許命名空間 "
                                     f"{sorted(self.node_tool_scope)}"})

        # 0c. 截止檢查（P0-6/P0-8）：協作式取消——過截止不再啟動新調用
        if self.deadline is not None and time.time() > self.deadline:
            return _finish({"error": "NODE_DEADLINE_EXCEEDED：節點已超時，"
                                     "不啟動新工具調用（協作式取消）"})

        # 0d. 節點級預算（P0-6）：本節點工具調用數上限
        if self.node_budget and self._node_calls[0] >= self.node_budget:
            return _finish({"error": f"NODE_BUDGET_EXHAUSTED：本節點工具"
                                     f"調用已達上限 {self.node_budget}"})

        # 1. 角色裁剪
        if contract.roles and self.principal.role not in contract.roles:
            return _finish({"error": f"角色 {self.principal.role} 無權調用 "
                                     f"{resolved}（允許：{contract.roles}）"})

        # 1b. Run scope 約束注入（P0-4）：scope-aware 工具強制帶範圍參數
        if self.scope is not None and not self.scope.is_unrestricted:
            arguments = self.scope.constrain_arguments(resolved, arguments)

        # 2. 目的限制（P1-9：優先用契約聲明的 capabilities，缺省回退
        # 到工具名啟發——聲明式優於名稱前綴猜測）
        caps = list(getattr(contract, "capabilities", None) or [])
        if not caps:
            fallback = _tool_capability(resolved)
            caps = [fallback] if fallback else []
        for capability in caps:
            ok, reason = purpose_allows(self.principal.purpose_of_use,
                                        capability, self.principal.role)
            if not ok:
                self.guardrail_events.append(
                    {"event": "purpose_denied", "tool": resolved,
                     "capability": capability, "reason": reason})
                return _finish({"error": f"purpose_denied：{reason}"})

        # 3. 參數校驗
        problem = _validate_args(contract, arguments)
        if problem:
            return _finish({"tool": resolved,
                            "error": f"參數校驗失敗：{problem}",
                            "expected_schema": contract.input_schema})

        # 4. 審批檢查（默認只讀自動；寫入需先批）
        if contract.side_effect != "read_only" \
                and resolved not in self.approved_operations:
            self.guardrail_events.append(
                {"event": "approval_required", "tool": resolved,
                 "approval": contract.approval})
            return _finish({"error": f"approval_required：{resolved} 是 "
                                     f"{contract.side_effect} 操作，須先獲 "
                                     f"{contract.approval} 審批"})

        # 5. 預算扣減（原子；超限即拒，達到預算即停）
        if self.budget is not None and \
                not self.budget.reserve_tool_call(resolved):
            return _finish({"error": "BUDGET_EXHAUSTED：本次運行工具預算"
                                     "已用盡，請基於已取證作答",
                            "budget": self.budget.snapshot()})
        self._node_calls[0] += 1        # 節點級計數（P0-6）

        # 6. 緩存（鍵含語料版本——換版自動失效）
        key = "::".join([resolved, self.corpus_version,
                         json.dumps(arguments, ensure_ascii=False,
                                    sort_keys=True, default=str)])
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None and contract.cacheable:
            out = copy.deepcopy(cached)
            out["cache_hit"] = True
            self._register_evidence(resolved, span_id, out, arguments,
                                    node_id)
            return _finish(out, cache_hit=True)

        # 7. 超時執行（只讀→超時線程可遺留熔斷；寫→同步不孤立）
        try:
            result = _run_with_timeout(
                contract.func, arguments, contract.timeout_ms / 1000.0,
                read_only=(contract.side_effect == "read_only"))
        except BrokerTimeout:
            return _finish({"tool": resolved,
                            "error": f"tool {resolved} timeout"
                                     f"（契約 {contract.timeout_ms}ms）"})
        except TypeError as exc:
            return _finish({"error": f"bad arguments for {resolved}: "
                                     f"{str(exc)[:200]}"})
        except Exception as exc:
            return _finish({"error": f"tool {resolved} failed: "
                                     f"{type(exc).__name__}: {str(exc)[:200]}"})

        # 8. 輸出契約
        if not isinstance(result, dict):
            return _finish({"error": f"tool {resolved} 輸出契約違例："
                                     f"期望 dict，得到 "
                                     f"{type(result).__name__}"})
        blob = json.dumps(result, ensure_ascii=False, default=str)
        if len(blob.encode("utf-8")) > MAX_RESULT_BYTES:
            return _finish({"tool": resolved,
                            "error": f"結果超過契約上限 "
                                     f"{MAX_RESULT_BYTES} bytes",
                            "hint": "縮小 limit/max_scan 或分頁調用"})

        # 8b. Run scope 後置過濾（P0-4）：constrain_arguments 未覆蓋的
        # 檢索路徑（citation.* 全庫時間有序、dynasty 多值）在此按 scope
        # 剔除越界命中，並把 scope_hash 回寫覆蓋記錄——聲明的 scope 與
        # 實際入賬證據一致，不靠 Agent 記得填參數
        if "error" not in result and self.scope is not None \
                and not self.scope.is_unrestricted:
            self._apply_scope(resolved, result)

        # 9. 證據 + 覆蓋登記（台賬唯一寫入口）。available:False 的結果
        # 既不入賬也不入緩存——庫未就緒不是可複用的成功結果
        if "error" not in result \
                and result.get("available", True) is not False:
            self._register_evidence(resolved, span_id, result, arguments,
                                    node_id)
            if contract.evidence_contract.requires_coverage_record \
                    and not result.get("coverage"):
                self.guardrail_events.append(
                    {"event": "coverage_missing", "tool": resolved,
                     "note": "契約要求覆蓋記錄但工具未返回——"
                             "工具輸出不得不聲明語料範圍"})
            with self._lock:
                if len(self._cache) >= 128:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[key] = copy.deepcopy(result)
        return _finish(result)

    # ------------------------------------------------------------------
    def _apply_scope(self, tool: str, result: Dict) -> None:
        """就地按 Run scope 過濾越界命中並回寫 scope_hash（P0-4）。

        過濾對象：hits / passage_evidence / attestations_time_ordered；
        earliest_in_library 從過濾後的時間線重算。剔除數量記入覆蓋
        known_gaps（不靜默丟棄）。"""
        scope = self.scope
        dropped = 0

        def _filt(key: str) -> None:
            nonlocal dropped
            items = result.get(key)
            if isinstance(items, list):
                kept = scope.filter_hits(items)
                dropped += len(items) - len(kept)
                result[key] = kept

        _filt("hits")
        _filt("passage_evidence")
        if isinstance(result.get("attestations_time_ordered"), list):
            _filt("attestations_time_ordered")
            ordered = result["attestations_time_ordered"]
            result["earliest_in_library"] = ordered[0] if ordered else None
        # n_hits 同步
        if "n_hits" in result and isinstance(result.get("hits"), list):
            result["n_hits"] = len(result["hits"])
        cov = result.get("coverage")
        if isinstance(cov, dict):
            cov["scope_hash"] = scope.scope_hash
            cov["scope_id"] = scope.scope_id
            if scope.categories:
                cov["included_categories"] = list(scope.categories)
            if scope.dynasties:
                cov["dynasty_range"] = list(scope.dynasties)
            if dropped:
                gaps = list(cov.get("known_gaps") or [])
                gaps.append(f"scope 過濾剔除 {dropped} 條越界命中")
                cov["known_gaps"] = gaps
        if dropped:
            result["scope_filtered_out"] = dropped

    def _register_evidence(self, tool: str, span_id: str, out: Dict,
                           arguments: Dict, node_id: str) -> None:
        """工具結果 → EvidenceRecord V2 + SearchCoverage 登記。"""
        from ._shared import work_registry
        reg = None
        try:
            reg = work_registry()
        except Exception:
            reg = None
        for rec in (out.get("passage_evidence") or []):
            if not (isinstance(rec, dict) and rec.get("passage_id")
                    and rec.get("verbatim_text") and rec.get("quote_hash")):
                continue        # 正文未返回的不入賬（V0 元數據不算證據）
            try:
                v2 = from_legacy_p_record(rec,
                                          corpus_version=self.corpus_version,
                                          work_registry=reg)
            except ValueError:
                self.guardrail_events.append(
                    {"event": "evidence_rejected", "tool": tool,
                     "passage_id": rec.get("passage_id"),
                     "note": "構造期完整性核驗未通過（hash/座標不一致）"})
                continue
            v2.tool_call_id = span_id
            v2.span_id = span_id
            v2.registered_by = "capability_broker"
            cov = out.get("coverage") or {}
            v2.coverage_id = cov.get("coverage_id", "")
            self.ledger.register(node_id, v2, self._token)
        cov = out.get("coverage")
        if isinstance(cov, dict) and cov.get("coverage_id"):
            try:
                sc = SearchCoverage.from_dict(cov)
                if self.corpus_version and not sc.corpus_versions:
                    sc.corpus_versions = [self.corpus_version]
                self.coverages[sc.coverage_id] = sc
            except (TypeError, ValueError):
                pass

    def audit_tail(self, n: int = 20) -> List[Dict]:
        return list(self.audit_log)[-n:]


# ---------------------------------------------------------------------------
def _tool_capability(name: str) -> str:
    """工具 → 能力標籤（目的限制檢查用）。只讀檢索類無標籤。
    特定工具判定在前，命名空間前綴兜底在後（順序即語義）。"""
    if name == "formula.compare_dosage":
        return "dosage_conversion"
    if name.startswith("formula.") and name != "formula.trace_lineage":
        return "formula_recommendation"
    return ""


def _validate_args(contract: ToolContractV2,
                   arguments: Dict) -> Optional[str]:
    props = contract.input_schema.get("properties", {})
    required = contract.input_schema.get("required", [])
    missing = [r for r in required
               if r not in arguments or arguments.get(r) in (None, "")]
    if missing:
        return f"缺少必填參數 {'、'.join(missing)}"
    unknown = [k for k in arguments if k not in props]
    if unknown:
        return f"未知參數 {'、'.join(unknown)}（可用：{'、'.join(props)}）"
    type_map = {"string": str, "integer": int, "boolean": bool,
                "array": list, "object": dict, "number": (int, float)}
    for k, v in arguments.items():
        spec = props.get(k, {})
        want = spec.get("type")
        py = type_map.get(want)
        if py and v is not None and not isinstance(v, py):
            return f"參數 {k} 應為 {want}"
        if v is None:
            continue
        if "enum" in spec and v not in spec["enum"]:
            return f"參數 {k}={v!r} 不在枚舉 {spec['enum']}"
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if "minimum" in spec and v < spec["minimum"]:
                return f"參數 {k}={v} 低於下限 {spec['minimum']}"
            if "maximum" in spec and v > spec["maximum"]:
                return f"參數 {k}={v} 超過上限 {spec['maximum']}"
    return None


# 超時遺留線程熔斷（P0-8）：Python 線程不可強殺，超時的只讀工作線程
# 仍在後台跑完；滯留過多時熔斷新調用而非無限堆線程。只讀工具的遺留
# 線程不產生持久副作用（無文件/網絡寫），其結果被丟棄、不入台賬——
# 唯一風險是 CPU/內存佔用，故以計數熔斷兜底。
MAX_ZOMBIE_THREADS = 16
_ZOMBIE_THREADS: List[threading.Thread] = []
_ZOMBIE_LOCK = threading.Lock()


def _prune_zombies() -> int:
    with _ZOMBIE_LOCK:
        _ZOMBIE_THREADS[:] = [t for t in _ZOMBIE_THREADS if t.is_alive()]
        return len(_ZOMBIE_THREADS)


def _run_with_timeout(func, arguments: Dict, timeout_s: float,
                      read_only: bool = True) -> Dict:
    # 非只讀工具（寫操作）**同步**執行——寫不能被超時孤立在後台線程，
    # 否則可能留下半寫狀態。寫工具是本地快速冪等操作，同步安全。
    if timeout_s <= 0 or not read_only:
        return func(**arguments)
    if _prune_zombies() >= MAX_ZOMBIE_THREADS:
        raise BrokerTimeout(
            f"熔斷：滯留超時工作線程過多（≥{MAX_ZOMBIE_THREADS}）——"
            "拒絕新調用，待後台線程退出")
    result: List[Any] = []
    error: List[BaseException] = []

    def _worker():
        try:
            result.append(func(**arguments))
        except BaseException as exc:   # noqa: BLE001 — 轉遞給調用線程
            error.append(exc)

    th = threading.Thread(target=_worker, daemon=True)
    th.start()
    th.join(timeout=timeout_s)
    if th.is_alive():
        with _ZOMBIE_LOCK:
            _ZOMBIE_THREADS.append(th)   # 登記遺留線程（供熔斷計數）
        raise BrokerTimeout(f"{timeout_s:g}s（遺留只讀線程已登記，"
                            "結果丟棄不入台賬）")
    if error:
        raise error[0]
    return result[0] if result else {}
