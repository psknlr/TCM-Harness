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

# 超時線程熔斷（P0：thread.join(timeout) 只是不再等待，工具線程本身
# 無法被強制終止——滯留線程達到上限即熔斷新調用，防資源泄漏擴大）
MAX_ZOMBIE_THREADS = 4
_ZOMBIE_THREADS: List[threading.Thread] = []
_ZOMBIE_LOCK = threading.Lock()


class BrokerTimeout(Exception):
    pass


class BrokerCircuitOpen(Exception):
    """超時滯留線程達到上限，熔斷新調用（等待滯留線程結束）。"""


class CapabilityBroker:
    def __init__(self, registry: ToolNamespaceRegistry,
                 ledger: TypedEvidenceLedger,
                 principal: Optional[Principal] = None,
                 budget=None,
                 corpus_version: str = "",
                 approved_operations: Optional[List[str]] = None,
                 trace=None, parent_span_id: Optional[str] = None):
        self.registry = registry
        self.ledger = ledger
        self.principal = principal or Principal(subject="anonymous",
                                                role="researcher")
        self.budget = budget
        self.corpus_version = corpus_version or ledger.corpus_version
        self.approved_operations = set(approved_operations or [])
        self.trace = trace
        self.parent_span_id = parent_span_id
        self.audit_log: deque = deque(maxlen=256)
        self.coverages: Dict[str, SearchCoverage] = {}
        self.tool_calls: List[Dict] = []
        self.guardrail_events: List[Dict] = []
        self._token = mint_broker_token("capability_broker")
        self._cache: Dict[str, Dict] = {}
        self._lock = threading.Lock()

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

        # 1. 角色裁剪
        if contract.roles and self.principal.role not in contract.roles:
            return _finish({"error": f"角色 {self.principal.role} 無權調用 "
                                     f"{resolved}（允許：{contract.roles}）"})

        # 2. 目的限制
        capability = _tool_capability(resolved)
        if capability:
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

        # 6. 緩存（鍵含語料版本——換版自動失效）
        key = "::".join([resolved, self.corpus_version,
                         json.dumps(arguments, ensure_ascii=False,
                                    sort_keys=True, default=str)])
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None and contract.cacheable:
            out = copy.deepcopy(cached)
            out["cache_hit"] = True
            n_ev = self._register_evidence(resolved, span_id, out,
                                           arguments, node_id)
            self._check_evidence_contract(contract, resolved, n_ev)
            return _finish(out, cache_hit=True)

        # 7. 超時執行（熔斷保護：滯留超時線程達上限即拒絕新調用）
        try:
            result = _run_with_timeout(contract.func, arguments,
                                       contract.timeout_ms / 1000.0)
        except BrokerCircuitOpen as exc:
            self.guardrail_events.append(
                {"event": "timeout_circuit_open", "tool": resolved,
                 "note": str(exc)})
            return _finish({"tool": resolved,
                            "error": f"circuit_open：{exc}"})
        except BrokerTimeout:
            # 誠實邊界：超時只是不再等待——工具線程無法被強制終止，
            # 副作用可能在超時之後仍然發生
            if contract.side_effect != "read_only":
                self.guardrail_events.append(
                    {"event": "timeout_side_effect_risk", "tool": resolved,
                     "note": "非只讀工具超時：執行線程不可強制終止，"
                             "副作用可能在超時後仍然發生——重試前必須"
                             "確認冪等性，必要時人工核對寫入結果"})
            else:
                self.guardrail_events.append(
                    {"event": "tool_timeout_thread_leaked", "tool": resolved,
                     "note": "只讀工具超時：線程繼續佔用資源直至自行結束"})
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

        # 9. 證據 + 覆蓋登記（台賬唯一寫入口）。available:False 的結果
        # 既不入賬也不入緩存——庫未就緒不是可複用的成功結果
        if "error" not in result \
                and result.get("available", True) is not False:
            n_ev = self._register_evidence(resolved, span_id, result,
                                           arguments, node_id)
            self._check_evidence_contract(contract, resolved, n_ev)
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
    def _register_evidence(self, tool: str, span_id: str, out: Dict,
                           arguments: Dict, node_id: str) -> int:
        """工具結果 → EvidenceRecord V2 + SearchCoverage 登記。

        返回本次調用完成核驗的證據記錄數（含已在賬去重命中）——
        契約守衛據此判定 returns_primary_text 是否兌現。

        兩條轉換路徑（統一證據適配層）：

        1. passage_evidence（classics P 層記錄）→ from_legacy_p_record；
        2. 領域證據形狀（evidence_excerpts / supporting_clauses /
           canonical_support 等）→ Domain Pack 的 evidence_normalizer。
        """
        from ._shared import work_registry
        reg = None
        try:
            reg = work_registry()
        except Exception:
            reg = None
        n_evidence = 0
        cov_id = (out.get("coverage") or {}).get("coverage_id", "") \
            if isinstance(out.get("coverage"), dict) else ""
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
            v2.coverage_id = cov_id
            self.ledger.register(node_id, v2, self._token)
            n_evidence += 1
        # 領域證據適配（P0 修復：shanghan 工具的條文證據曾在台賬計零）
        from ..domains.registry import normalize_domain_evidence
        seen_ids = {r.evidence_id for r in self.ledger.node_records(node_id)}
        for v2 in normalize_domain_evidence(tool, out, self.corpus_version):
            if v2.evidence_id in seen_ids:
                n_evidence += 1     # 已在賬（如緩存命中重放）也算兌現
                continue
            v2.tool_call_id = span_id
            v2.span_id = span_id
            v2.registered_by = "capability_broker"
            if not v2.coverage_id:
                v2.coverage_id = cov_id
            self.ledger.register(node_id, v2, self._token)
            n_evidence += 1
        cov = out.get("coverage")
        if isinstance(cov, dict) and cov.get("coverage_id"):
            try:
                sc = SearchCoverage.from_dict(cov)
                if self.corpus_version and not sc.corpus_versions:
                    sc.corpus_versions = [self.corpus_version]
                self.coverages[sc.coverage_id] = sc
            except (TypeError, ValueError):
                pass
        return n_evidence

    def _check_evidence_contract(self, contract: ToolContractV2,
                                 tool: str, n_evidence: int) -> None:
        """returns_primary_text 契約兌現守衛：聲明返回原文的工具在成功
        調用後台賬證據為零，不再靜默通過——記 guardrail 事件（非硬錯：
        誠實零命中是合法結果；發布側由主張核驗/發布閘門按台賬 fail-closed
        裁定，引用不會憑空出現）。"""
        if contract.evidence_contract.returns_primary_text \
                and n_evidence == 0:
            self.guardrail_events.append(
                {"event": "evidence_contract_unfulfilled", "tool": tool,
                 "note": "契約聲明 returns_primary_text，但本次成功調用"
                         "未產生任何可核驗正文證據——該結果不可被引用"
                         "（主張核驗與發布閘門按台賬裁定）"})

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
    """深度參數校驗（純標準庫的 JSON Schema 子集，遞歸執行）。

    頂層保持原語義：required 空值視為缺失、未知參數默認拒絕；
    值級校驗遞歸覆蓋嵌套 object / array items / 長度 / pattern /
    數值邊界，且 boolean 不再被 integer/number 誤接收
    （Python isinstance(True, int) == True）。"""
    props = contract.input_schema.get("properties", {})
    required = contract.input_schema.get("required", [])
    missing = [r for r in required
               if r not in arguments or arguments.get(r) in (None, "")]
    if missing:
        return f"缺少必填參數 {'、'.join(missing)}"
    unknown = [k for k in arguments if k not in props]
    if unknown:
        return f"未知參數 {'、'.join(unknown)}（可用：{'、'.join(props)}）"
    for k, v in arguments.items():
        problem = _validate_value(k, v, props.get(k, {}))
        if problem:
            return problem
    return None


def _validate_value(path: str, v: Any, spec: Dict) -> Optional[str]:
    """單值遞歸校驗；path 是人可讀定位（如 filters.dynasty[2]）。"""
    if v is None or not isinstance(spec, dict):
        return None
    want = spec.get("type")
    if want:
        if want in ("integer", "number") and isinstance(v, bool):
            return f"參數 {path} 應為 {want}（boolean 不是數值）"
        type_map = {"string": str, "integer": int, "boolean": bool,
                    "array": list, "object": dict, "number": (int, float)}
        py = type_map.get(want)
        if py and not isinstance(v, py):
            return f"參數 {path} 應為 {want}"
    if "enum" in spec and v not in spec["enum"]:
        return f"參數 {path}={v!r} 不在枚舉 {spec['enum']}"
    if isinstance(v, str):
        if "minLength" in spec and len(v) < spec["minLength"]:
            return f"參數 {path} 長度 {len(v)} 低於下限 {spec['minLength']}"
        if "maxLength" in spec and len(v) > spec["maxLength"]:
            return f"參數 {path} 長度 {len(v)} 超過上限 {spec['maxLength']}"
        if "pattern" in spec:
            import re
            try:
                if not re.search(spec["pattern"], v):
                    return f"參數 {path} 不匹配 pattern {spec['pattern']!r}"
            except re.error:
                pass    # 契約自身的壞 pattern 不應拒絕用戶輸入
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in spec and v < spec["minimum"]:
            return f"參數 {path}={v} 低於下限 {spec['minimum']}"
        if "maximum" in spec and v > spec["maximum"]:
            return f"參數 {path}={v} 超過上限 {spec['maximum']}"
    if isinstance(v, list):
        if "minItems" in spec and len(v) < spec["minItems"]:
            return f"參數 {path} 項數 {len(v)} 低於下限 {spec['minItems']}"
        if "maxItems" in spec and len(v) > spec["maxItems"]:
            return f"參數 {path} 項數 {len(v)} 超過上限 {spec['maxItems']}"
        item_spec = spec.get("items")
        if isinstance(item_spec, dict):
            for i, item in enumerate(v):
                problem = _validate_value(f"{path}[{i}]", item, item_spec)
                if problem:
                    return problem
    if isinstance(v, dict):
        sub_props = spec.get("properties", {})
        for r in spec.get("required", []):
            if r not in v or v.get(r) in (None, ""):
                return f"參數 {path} 缺少必填字段 {r}"
        if spec.get("additionalProperties") is False:
            unknown = [k for k in v if k not in sub_props]
            if unknown:
                return (f"參數 {path} 含未知字段 {'、'.join(unknown)}"
                        f"（可用：{'、'.join(sub_props)}）")
        for k, sub in v.items():
            if k in sub_props:
                problem = _validate_value(f"{path}.{k}", sub, sub_props[k])
                if problem:
                    return problem
    return None


def _run_with_timeout(func, arguments: Dict, timeout_s: float) -> Dict:
    """超時執行 + 滯留線程熔斷。

    誠實邊界（不可繞過的實現約束）：CPython 線程不可被強制終止，
    join(timeout) 之後工具線程仍在運行——超時語義是「調用方不再等待
    且結果不被採納」，不是「工具已停止」。緩解措施：

    * 滯留線程進程級登記，達到 MAX_ZOMBIE_THREADS 即熔斷新調用；
    * 超時結果不入台賬、不入緩存（call() 管道保證）；
    * 非只讀工具超時記 timeout_side_effect_risk 守衛事件（見 call()）。

    真正可終止的隔離（worker 進程池）需要可 pickle 的工具函數，與
    legacy 綁定方法工具面衝突，屬後續工程項——如實聲明，不冒充已有。"""
    if timeout_s <= 0:
        return func(**arguments)
    with _ZOMBIE_LOCK:
        _ZOMBIE_THREADS[:] = [t for t in _ZOMBIE_THREADS if t.is_alive()]
        if len(_ZOMBIE_THREADS) >= MAX_ZOMBIE_THREADS:
            raise BrokerCircuitOpen(
                f"{len(_ZOMBIE_THREADS)} 個超時工具線程仍在運行，"
                "熔斷新調用（等待滯留線程結束）")
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
            _ZOMBIE_THREADS.append(th)
        raise BrokerTimeout(f"{timeout_s:g}s")
    if error:
        raise error[0]
    return result[0] if result else {}
