"""静默默认闸 ``scripts/check-express-defaults.py``：认得两种写法、登记过的键放行、超出基线就红、基线只许减少。

    python3 tests/test_check_express_defaults.py
"""
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_express_defaults", ROOT / "scripts/check-express-defaults.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class Detect(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def scan(self, src):
        path = self.dir / "m.py"
        path.write_text(src, encoding="utf-8")
        return [key for _, key in mod.findings(path)]

    def test_get_with_invented_default(self):
        self.assertEqual(self.scan('x = p.get("qty", 1)\n'), ["qty"])
        self.assertEqual(self.scan('x = p.get("unit", DEFAULT_UNIT)\n'), ["unit"])

    def test_get_or_invented_constant(self):
        self.assertEqual(self.scan('x = p.get("unit") or "PCS"\n'), ["unit"])
        self.assertEqual(self.scan('x = p.get("loc") or S.MAIN\n'), ["loc"])

    def test_empty_normalisation_passes(self):
        src = ('a = p.get("a")\nb = p.get("b", None)\nc = p.get("c") or other()\nd = str(p.get("d") or "").strip()\n'
               'e = p.get("e", 0)\nf = p.get("f") or []\n')
        self.assertEqual(self.scan(src), [])


class Baseline(unittest.TestCase):
    def test_over_counts_only_increase(self):
        self.assertEqual(mod.over({"f.py": {"k": 2}}, {"f.py": {"k": 2}}), [])
        self.assertEqual(len(mod.over({"f.py": {"k": 3}}, {"f.py": {"k": 2}})), 1)
        self.assertEqual(len(mod.over({"g.py": {"k": 1}}, {})), 1)

    def test_registered_default_keys_not_counted(self):
        from pearnly_core.express_rules.contracts import required_fields as rf  # noqa: E402
        current = mod.counts()
        for keys in current.values():
            self.assertFalse(set(keys) & set(rf.DEFAULT_KEYS))

    def test_repo_within_baseline(self):
        baseline = json.loads(mod.BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(mod.over(mod.counts(), baseline), [])

    def test_scanned_files_exist(self):
        for rel in mod.SCANNED:
            self.assertTrue((ROOT / rel).exists(), rel)


if __name__ == "__main__":
    unittest.main()
