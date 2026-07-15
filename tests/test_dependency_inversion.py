"""P0-3 依賴倒置守衛：hermes_tcm 內核不得散落 import hermes_shanghan。

依賴方向治理（AST 級強制）：內核觸達 legacy 包的縫隙只有兩個——

    hermes_tcm/platform.py          平台層服務網關
    hermes_tcm/domains/shanghan.py  shanghan Domain Pack 接縫

其餘任何內核模塊直接 import hermes_shanghan（含 import_module 字符串
形式）即紅。新領域包接入時把自己的模塊加進 ALLOWED，而不是繞過本測試。
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KERNEL = ROOT / "hermes_tcm"

# 允許觸達 legacy 包的內核模塊（相對 KERNEL 的 POSIX 路徑）
ALLOWED = {
    "platform.py",
    "domains/shanghan.py",
}


def _legacy_imports(path: Path):
    """文件中的 hermes_shanghan import 清單（AST 級，註釋/文檔串不計）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "hermes_shanghan":
                    found.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module \
                    and node.module.split(".")[0] == "hermes_shanghan":
                found.append(f"from {node.module} import …")
        elif isinstance(node, ast.Call):
            # importlib.import_module("hermes_shanghan…") 字符串形式
            fn = node.func
            name = getattr(fn, "attr", "") or getattr(fn, "id", "")
            if name == "import_module" and node.args \
                    and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str) \
                    and node.args[0].value.startswith("hermes_shanghan"):
                found.append(f"import_module({node.args[0].value!r})")
    return found


class TestDependencyInversion(unittest.TestCase):
    def test_legacy_imports_only_in_allowed_seams(self):
        offenders = {}
        for path in sorted(KERNEL.rglob("*.py")):
            rel = path.relative_to(KERNEL).as_posix()
            if rel in ALLOWED:
                continue
            found = _legacy_imports(path)
            if found:
                offenders[rel] = found
        self.assertEqual(
            offenders, {},
            "內核模塊繞過依賴倒置縫隙直接 import hermes_shanghan——"
            "一律改走 hermes_tcm.platform 或 Domain Pack 接縫")

    def test_allowed_seams_exist_and_do_import(self):
        """允許清單不是死配置：兩個縫隙模塊真實存在且確實承擔 import。"""
        for rel in sorted(ALLOWED):
            path = KERNEL / rel
            self.assertTrue(path.exists(), rel)
            self.assertTrue(_legacy_imports(path),
                            f"{rel} 應是 legacy 依賴的實際承擔者")

    def test_no_reverse_dependency(self):
        """反向不變量：legacy 包不 import 新內核（插件不知道宿主之上
        還有誰；hermes_shanghan 保持獨立可用）。"""
        legacy_root = ROOT / "hermes_shanghan"
        offenders = {}
        for path in sorted(legacy_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mod = ""
                if isinstance(node, ast.Import):
                    mod = node.names[0].name
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    mod = node.module or ""
                if mod.split(".")[0] == "hermes_tcm":
                    offenders.setdefault(
                        path.relative_to(legacy_root).as_posix(),
                        []).append(mod)
        self.assertEqual(offenders, {})


class TestDomainPackInterface(unittest.TestCase):
    def test_shanghan_implements_full_interface(self):
        from hermes_tcm.domains.base import DomainPackInterface
        from hermes_tcm.domains.registry import get_domain_pack
        pack = get_domain_pack("shanghan").load_implementation()
        self.assertIsInstance(pack, DomainPackInterface)
        for method in ("metadata", "health", "register_tools",
                       "detect_intent", "extract_entities", "build_plan",
                       "normalize_evidence", "claim_policies",
                       "specialists", "evaluation_suites",
                       "call_legacy_tool"):
            self.assertTrue(callable(getattr(pack, method)), method)

    def test_shanghan_pack_health(self):
        from hermes_tcm.domains.registry import get_domain_pack
        health = get_domain_pack("shanghan").load_implementation().health()
        self.assertTrue(health["healthy"], health)
        self.assertEqual(health["status"], "ready")
        self.assertTrue(all(c["ok"] for c in health["checks"]))

    def test_detect_intent_scores(self):
        from hermes_tcm.domains.registry import get_domain_pack
        pack = get_domain_pack("shanghan").load_implementation()
        self.assertEqual(pack.detect_intent("桂枝湯的核心方證")["score"], 1.0)
        self.assertEqual(pack.detect_intent("宋代雕版工藝")["score"], 0.0)

    def test_build_plan_matches_controller(self):
        """領域計劃單一主源：pack.build_plan 與 controller 計劃一致。"""
        from hermes_tcm.domains.registry import get_domain_pack
        pack = get_domain_pack("shanghan").load_implementation()
        steps = pack.build_plan("formula_pattern")
        self.assertEqual([s["tool"] for s in steps],
                         ["formula.resolve", "text.search_passages"])
        self.assertEqual(pack.build_plan("witness_comparison"), [])

    def test_planned_pack_has_no_seams(self):
        """未就緒領域不偽裝：jingui 無 implementation/normalizer。"""
        from hermes_tcm.domains.registry import get_domain_pack
        pack = get_domain_pack("jingui")
        self.assertEqual(pack.status, "planned")
        self.assertFalse(pack.implementation)
        self.assertFalse(pack.evidence_normalizer)
        self.assertIsNone(pack.load_implementation())

    def test_base_interface_fails_closed(self):
        from hermes_tcm.domains.base import DomainPackInterface
        base = DomainPackInterface()
        self.assertFalse(base.health()["healthy"])
        self.assertEqual(base.detect_intent("x")["score"], 0.0)
        self.assertEqual(base.build_plan("formula_pattern"), [])
        self.assertIn("error", base.call_legacy_tool("t", {}))


if __name__ == "__main__":
    unittest.main()
