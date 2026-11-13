"""LINE「点序号选」按钮标签（`pearnly_core.line_ui.labels.choice_labels`，多产品共用；11-13 收款真机：同一家两本账套截到 20 字后一样）。

纯函数，不连库不出网。另核 ERP 的 LINE 入口里列名称让人点序号选的地方都经它出标签，不再各自 ``[:20]`` 截。Cowork 识别后问仓的按钮以仓库编码开头
（「01 Main」），截断后不会相同，不改；DMS 列的是客户编码，同理。
"""
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.line_ui.labels import QUICK_LABEL_MAX, choice_labels  # noqa: E402


class Labels(unittest.TestCase):
    def check(self, names):
        labels = choice_labels(names)
        self.assertEqual(len(labels), len(names))
        for index, label in enumerate(labels, 1):
            self.assertTrue(label.startswith(f"{index}. "), label)
            self.assertLessEqual(len(label), QUICK_LABEL_MAX, label)
        distinct = {name for name in names}
        if len(distinct) == len(names):  # 名称互不相同 → 序号后面那段也互不相同
            self.assertEqual(len({label.split(". ", 1)[1] for label in labels}), len(labels), labels)
        return labels

    def test_two_books_of_one_company_differ_after_the_number(self):
        labels = self.check(["9.บริษัท Pearnly จำกัด TEST2 · \\\\accserver\\d$\\ACCOUNT\\69EXP\\TEST2",
                             "9.บริษัท Pearnly จำกัด · \\\\accserver\\d$\\ACCOUNT\\69EXP\\TEST"])
        self.assertTrue(labels[0].startswith("1. …TEST2"))

    def test_short_names_stay_as_they_are(self):
        self.assertEqual(self.check(["TEST2", "TEST"]), ["1. TEST2", "2. TEST"])

    def test_same_beginning_shows_where_they_differ(self):
        self.assertEqual(self.check(["Warehouse Main Bangkok North", "Warehouse Main Bangkok South", "Shop"]),
                         ["1. …North", "2. …South", "3. Shop"])
        self.check(["บริษัท ทดสอบ สาขา 00001 กรุงเทพมหานคร", "บริษัท ทดสอบ สาขา 00002 เชียงใหม่"])
        self.check(["A" * 40 + "x", "A" * 40 + "y", "A" * 40 + "z"])

    def test_twelve_numbers_still_fit(self):
        self.check([f"Company with a long name {index:02d}" for index in range(12)])

    def test_identical_names_are_left_to_the_number(self):
        self.assertEqual(self.check(["same", "same"]), ["1. same", "2. same"])


class CallSites(unittest.TestCase):
    """列名称点序号选的按钮都经 `choice_labels`（按类修，不只收款选账套这一处）。"""

    SITES = {
        "apps/pearnly_erp_portal/pearnly_erp_portal/integrations/line_collection.py": 4,
        "apps/pearnly_erp_portal/pearnly_erp_portal/integrations/line_flow.py": 2,
        "apps/pearnly_erp_portal/pearnly_erp_portal/integrations/line_items.py": 1,
    }

    def test_numbered_buttons_use_the_shared_labels(self):
        for path, count in self.SITES.items():
            source = (ROOT / path).read_text(encoding="utf-8")
            self.assertGreaterEqual(source.count("choice_labels("), count, path)
            self.assertNotRegex(source, re.compile(r'_button\(f"\{index\}\. '), path)


if __name__ == "__main__":
    unittest.main()
