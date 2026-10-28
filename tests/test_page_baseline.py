"""页面入口基线（`scripts/regression/baseline.py`，回归方案 C 节）：基线文件是合法的，比对规矩按约定判
（地址状态变了 / 页头多出码 / 新地址 / 多出快捷键 = 不通过；码少了只报告）。纯离线，不起站点。"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("baseline", ROOT / "scripts" / "regression" / "baseline.py")
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


def row(path, *, final=None, codes=(), shortcuts=("ctrl+s · Trigger primary action",), denied=False, role="admin"):
    items = [{"text": code, "action": code, "declared": True} for code in codes]
    return {"product": "erp", "role": role, "account": f"{role}@erp.regression", "path": path, "final": final or path,
            "inventory": {"head_buttons": items, "head_menu": [], "head_dropdowns": [], "shortcuts": list(shortcuts),
                          "not_permitted": denied}}


class PageBaseline(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = baseline.BASE
        baseline.BASE = self.tmp

    def tearDown(self):
        baseline.BASE = self.saved

    def snapshot(self, rows):
        path = self.tmp / f"snap{len(list(self.tmp.iterdir()))}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return str(path)

    def test_committed_baselines_are_valid(self):
        files = sorted((ROOT / "tests" / "baseline" / "page-entries").glob("*.json"))
        self.assertEqual({f.stem for f in files}, {"erp", "cowork", "dms"})
        for file in files:
            data = json.loads(file.read_text(encoding="utf-8"))
            self.assertEqual(data["shortcuts"], ["ctrl+s"], file.name)  # 全局快捷键只留声明的 Ctrl+S
            for role, pages in data["pages"].items():
                for key, cell in pages.items():
                    self.assertIn(cell["state"], ("open", "denied", "home", "none"), f"{file.name} {role} {key}")
                    self.assertFalse([c for c in cell["codes"] if c.startswith("text:")],
                                     f"{file.name} {role} {key}: 页头有没声明码的入口")

    def test_rules(self):
        base = [row("/desk/pearnly-sales", codes=["New", "Export"]), row("/desk/todo", final="/desk/pearnly-home"),
                row("/desk/pearnly-sales/SD-1", codes=["Print"])]
        baseline.write([self.snapshot(base)])
        self.assertEqual(baseline.compare([self.snapshot(base)]), 0)
        # 页头少了一个码（数据状态）：只报告
        self.assertEqual(baseline.compare([self.snapshot([row("/desk/pearnly-sales", codes=["New"])])]), 0)
        # 页头多出码、地址从回首页变成打开、新地址、多出快捷键：都不通过
        self.assertEqual(baseline.compare([self.snapshot([row("/desk/pearnly-sales", codes=["New", "Export", "Delete"])])]), 1)
        self.assertEqual(baseline.compare([self.snapshot([row("/desk/todo")])]), 1)
        self.assertEqual(baseline.compare([self.snapshot([row("/desk/contact")])]), 1)
        self.assertEqual(baseline.compare([self.snapshot([row("/desk/pearnly-sales", codes=["New"],
                                                              shortcuts=["ctrl+s · x", "ctrl+k · y"])])]), 1)
        # 另一张单（同一类记录）不算新地址
        self.assertEqual(baseline.compare([self.snapshot([row("/desk/pearnly-sales/SD-2", codes=["Print"])])]), 0)

    def test_merge_keeps_union_of_codes(self):
        baseline.write([self.snapshot([row("/desk/pearnly-cashier", codes=["trade.openShift"])])])
        baseline.write([self.snapshot([row("/desk/pearnly-cashier", codes=["trade.newInvoice"])])], merge=True)
        data = json.loads((self.tmp / "erp.json").read_text(encoding="utf-8"))
        self.assertEqual(data["pages"]["admin"]["desk/pearnly-cashier"]["codes"], ["trade.newInvoice", "trade.openShift"])


if __name__ == "__main__":
    unittest.main()
