"""Keep migrated preparation independent from frameworks, IO and wall clocks."""
import ast
from pathlib import Path
import unittest


class PurePushBoundary(unittest.TestCase):
    def test_imports_stay_within_explicit_pure_dependencies(self):
        root = Path(__file__).resolve().parents[1] / "apps/pearnly_cowork/pearnly_cowork"
        allowed = {"__future__", "collections", "copy", "dataclasses", "datetime", "decimal",
                   "difflib", "enum", "hashlib", "json", "math", "ntpath", "re", "typing", "uuid"}
        for domain in ("push", "targets"):
            for path in (root / domain).rglob("*.py"):
                for node in ast.walk(ast.parse(path.read_text())):
                    if isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        if node.level:
                            continue  # domain-local explicit imports
                        names = [node.module or ""]
                    else:
                        continue
                    for name in names:
                        self.assertTrue(name.split(".")[0] in allowed or name.startswith((
                            "pearnly_cowork.contracts", "pearnly_cowork.push", "pearnly_cowork.targets")),
                            f"{path.relative_to(root)}:{node.lineno}: non-pure dependency {name}")

    def test_preparation_never_reads_io_or_wall_clock(self):
        root = Path(__file__).resolve().parents[1] / "apps/pearnly_cowork/pearnly_cowork"
        for domain in ("push", "targets"):
            for path in (root / domain).rglob("*.py"):
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
