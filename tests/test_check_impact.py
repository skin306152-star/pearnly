"""共用件连带回归闸门（scripts/check-impact.py）：在临时仓库里核三件事——命中共用件列全入口、没登记的调用方失败、登记表错误失败。"""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_impact", ROOT / "scripts" / "check-impact.py")
impact = importlib.util.module_from_spec(spec)
sys.modules["check_impact"] = impact  # dataclass 要在 sys.modules 里找得到自己的模块
spec.loader.exec_module(impact)

REGISTRY = textwrap.dedent('''
    [[part]]
    id = "scan"
    name = "读码器"
    paths = ["apps/core/public/scan.js"]
    markers = ["p.scan("]
    entries = ["erp.products.scan", "cowork.stocktake.scan"]

    [[entry]]
    id = "erp.products.scan"
    product = "ERP"
    where = "pearnly-products"
    steps = "新建 → 扫码"
    expect = "有画面、读出码"
    check = "auto"
    smoke = "scan"
    files = ["apps/erp/public/products.js"]

    [[entry]]
    id = "cowork.stocktake.scan"
    product = "Cowork"
    where = "pearnly-stocktake"
    steps = "盘点 → 扫码"
    expect = "有画面、读出码"
    check = "manual"
    files = ["apps/cowork/public/stocktake.js"]
''')


class Repo:
    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.dir.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        self.write("apps/core/public/scan.js", "p.scan = function () {}\n")
        self.write("apps/erp/public/products.js", "p.scan({target: 'code'})\n")
        self.write("apps/cowork/public/stocktake.js", "p.scan({target: 'qty'})\n")
        self.write("scripts/impact/shared-parts.toml", REGISTRY)
        self.git("add", "-A")
        self.git("commit", "-qm", "base")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True).stdout

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def run(self, *args):
        return impact.main(["--repo", str(self.root), "--base", "main", *args])


class CheckImpact(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.addCleanup(self.repo.dir.cleanup)

    def test_changing_the_shared_part_lists_every_entry(self):
        self.repo.write("apps/core/public/scan.js", "p.scan = function () { /* broken */ }\n")
        parts, entries, errors = impact.load(self.repo.root / "scripts/impact/shared-parts.toml")
        self.assertEqual(errors, [])
        changes = impact.changed("main", self.repo.root)
        hit, touched = impact.impact(parts, entries, changes, impact.tracked(self.repo.root), self.repo.root)
        self.assertEqual([part.id for part in hit], ["scan"])
        report = impact.render(hit, touched, parts, entries, markdown=True)
        self.assertIn("erp.products.scan", report)
        self.assertIn("cowork.stocktake.scan", report)
        self.assertEqual(self.repo.run(), 0)

    def test_an_unregistered_caller_fails(self):
        self.repo.write("apps/erp/public/counts.js", "p.scan({target: 'count'})\n")
        self.repo.git("add", "-A")
        self.assertEqual(self.repo.run(), 1)
        parts, entries, _ = impact.load(self.repo.root / "scripts/impact/shared-parts.toml")
        errors = impact.validate(parts, entries, impact.tracked(self.repo.root), self.repo.root)
        self.assertTrue(any("apps/erp/public/counts.js" in error for error in errors))

    def test_a_caller_change_alone_lists_its_own_entry(self):
        self.repo.write("apps/erp/public/products.js", "p.scan({target: 'barcode'})\n")
        parts, entries, _ = impact.load(self.repo.root / "scripts/impact/shared-parts.toml")
        hit, touched = impact.impact(parts, entries, impact.changed("main", self.repo.root),
                                     impact.tracked(self.repo.root), self.repo.root)
        self.assertEqual(hit, [])
        self.assertEqual(list(touched), ["erp.products.scan"])

    def test_registry_mistakes_fail(self):
        broken = REGISTRY.replace('files = ["apps/cowork/public/stocktake.js"]', 'files = ["apps/cowork/public/gone.js"]')
        broken = broken.replace('check = "manual"', 'check = "maybe"')
        self.repo.write("scripts/impact/shared-parts.toml", broken)
        parts, entries, errors = impact.load(self.repo.root / "scripts/impact/shared-parts.toml")
        errors += impact.validate(parts, entries, impact.tracked(self.repo.root), self.repo.root)
        text = "\n".join(errors)
        self.assertIn("check 只能是", text)
        self.assertIn("gone.js", text)
        self.assertIn("apps/cowork/public/stocktake.js", text)  # 原来的调用方因此变成没登记


class RealRegistry(unittest.TestCase):
    def test_the_repository_registry_matches_the_code(self):
        parts, entries, errors = impact.load()
        errors += impact.validate(parts, entries, impact.tracked())
        self.assertEqual(errors, [], "\n".join(errors))


if __name__ == "__main__":
    unittest.main()
