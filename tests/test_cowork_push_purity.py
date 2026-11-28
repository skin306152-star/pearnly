"""Keep migrated preparation independent from frameworks, IO and wall clocks."""
import ast
from importlib.util import resolve_name
from pathlib import Path
import unittest


PURE_ROOT = {"__init__.py", "export_actions.py", "lifecycle.py", "retry.py"}
# Root modules with explicit framework/IO duties are acknowledged, not scanned
# for purity. export_facts reads the native ledger through Frappe; choices stores the
# accountant's per-account-set decisions (the rules are pure: push/express/choices.py).
NON_PURE_ROOT = {"export_facts.py", "choices.py", "express_wait.py", "unknown.py"}  # express_wait：读推送记录与当前时间（11-12）；unknown：读推送记录判结果不明（11-25）


def pure_paths(root):
    # Native persistence and application orchestration have explicit IO duties.
    # Every seventh-batch pure file remains covered, including future modules
    # added inside express/targets. New root modules must be classified here.
    actual = {path.name for path in (root / "push").glob("*.py")}
    if actual - NON_PURE_ROOT != PURE_ROOT:
        raise AssertionError(f"Unclassified push root modules: {actual - NON_PURE_ROOT - PURE_ROOT}")
    return [*(root / "targets").rglob("*.py"), *(root / "push/express").rglob("*.py"),
            *(root / "push" / name for name in sorted(PURE_ROOT))]


class PurePushBoundary(unittest.TestCase):
    def test_imports_stay_within_explicit_pure_dependencies(self):
        root = Path(__file__).resolve().parents[1] / "apps/pearnly_cowork/pearnly_cowork"
        # html：只用 html.unescape（纯函数，读 MR.ERP 导入页菜单名，targets/mrerp_menus.py）。
        # unicodedata：只用 NFC 正规化比名称（`push/express/choices.norm_name`）。
        allowed = {"__future__", "collections", "copy", "dataclasses", "datetime", "decimal",
                   "difflib", "enum", "hashlib", "html", "json", "math", "ntpath", "re", "typing",
                   "unicodedata", "uuid"}
        for path in pure_paths(root):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        package = "pearnly_cowork." + ".".join(path.relative_to(root).parts[:-1])
                        base = resolve_name("." * node.level + (node.module or ""), package)
                        names = [base + "." + item.name for item in node.names] if not node.module else [base]
                    else:
                        names = [node.module or ""]
                else:
                    continue
                for name in names:
                    self.assertTrue(name.split(".")[0] in allowed or name.startswith((
                        "pearnly_cowork.contracts", "pearnly_cowork.push.express",
                        "pearnly_cowork.push.lifecycle", "pearnly_cowork.push.retry",
                        "pearnly_cowork.targets",
                        # 共用层的纯函数包（ADR-025：Express 写账规则与回执口径，2026-10-23 由本域搬去；只依赖标准库与
                        # 票种决策表）与 MR.ERP「单号已存在」的判定（`retry` 从这里取，2026-11-02）、「用户数据错」的判定
                        # （同样只用标准库，2026-11-03）。
                        "pearnly_core.express_rules", "pearnly_core.cc_desktop.wire.ack_rules",
                        "pearnly_core.mrerp.push.duplicates", "pearnly_core.mrerp.push.user_data",
                        # 全平台共用的票种决策表（ADR-0012），本身只依赖标准库（tests/test_tax_documents.py 核对）。
                        "pearnly_core.tax_documents")),
                        f"{path.relative_to(root)}:{node.lineno}: non-pure dependency {name}")

    def test_preparation_never_reads_io_or_wall_clock(self):
        root = Path(__file__).resolve().parents[1] / "apps/pearnly_cowork/pearnly_cowork"
        for path in pure_paths(root):
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.Call):
                    continue
                name = ast.unparse(node.func)
                self.assertNotIn(name, {"open", "print", "eval", "exec", "__import__", "input"},
                                 f"{path.relative_to(root)}:{node.lineno}: {name}")
                self.assertFalse(name.endswith((".now", ".utcnow", ".today", ".read_text", ".read_bytes")),
                                 f"{path.relative_to(root)}:{node.lineno}: {name}")


if __name__ == "__main__":
    unittest.main()
