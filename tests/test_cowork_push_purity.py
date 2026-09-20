"""Keep migrated preparation independent from frameworks, IO and wall clocks."""
import ast
from importlib.util import resolve_name
from pathlib import Path
import unittest


PURE_ROOT = {"__init__.py", "lifecycle.py", "receipts.py", "retry.py"}


def pure_paths(root):
    # Native persistence and application orchestration have explicit IO duties.
    # Every seventh-batch pure file remains covered, including future modules
    # added inside express/targets. New root modules must be classified here.
    actual = {path.name for path in (root / "push").glob("*.py")}
    if actual != PURE_ROOT:
        raise AssertionError(f"Unclassified push root modules: {actual ^ PURE_ROOT}")
    return [*(root / "targets").rglob("*.py"), *(root / "push/express").rglob("*.py"),
            *(root / "push" / name for name in sorted(PURE_ROOT))]


class PurePushBoundary(unittest.TestCase):
    def test_imports_stay_within_explicit_pure_dependencies(self):
        root = Path(__file__).resolve().parents[1] / "apps/pearnly_cowork/pearnly_cowork"
        allowed = {"__future__", "collections", "copy", "dataclasses", "datetime", "decimal",
                   "difflib", "enum", "hashlib", "json", "math", "ntpath", "re", "typing", "uuid"}
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
                        "pearnly_cowork.push.receipts", "pearnly_cowork.targets")),
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
