"""代录弹框登记表与 CC 桌面版配方对拍（``pearnly_core.express_operator.choice_points``；总设计
``docs/work/2026-11-14-cc-guided-master-design.md`` 10.2，P0 第 12 个提交）。

断言的是应有状态：
- 配方 ``dialogs`` 里的每个框都登记了，登记的每个框配方里都有，共同字段（匹配文字、是否自动、点哪个钮、落什么结果与原因）同值；
  配方里多一个没登记的框，这里就红；
- 每个框归到四类之一；拒收类与登录类落成的原因码都是代录原因码；
- 业务选择框都写明开始前问哪一题（写账前置判定的题类）；答案接上了的，选项码对上的按钮就是配方按那个值点的按钮；
  还没接上的只有登记在案的那两个（只许减少）。

不依赖 Frappe，只读配方文件。

    python3 tests/test_operator_choice_points.py
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.express_operator import choice_points as cp, contracts  # noqa: E402
from pearnly_core.write_precheck import contracts as precheck  # noqa: E402

RECIPES = {cp.EXPRESS: sorted((ROOT / "companion/src/companion/express_operator/recipes").glob("*.json")),
           cp.MRERP: sorted((ROOT / "companion/src/companion/mrerp_operator/recipes").glob("*.json"))}


def dialogs(path):
    return json.loads(path.read_text(encoding="utf-8"))["dialogs"]


class RecipeTests(unittest.TestCase):
    def test_every_recipe_dialog_is_registered_with_the_same_values(self):
        for ledger, paths in RECIPES.items():
            self.assertTrue(paths, ledger)
            for path in paths:
                self.assertEqual(cp.differences(ledger, dialogs(path)), [], path.name)

    def test_a_dialog_only_in_the_recipe_or_only_registered_is_reported(self):
        recipe = dialogs(RECIPES[cp.EXPRESS][0])
        extra = recipe + [{"name": "price_changed", "match": "ราคาเปลี่ยน", "auto": True, "button": "ตกลง"}]
        self.assertEqual(cp.differences(cp.EXPRESS, extra), ["express: price_changed is in the recipe but not registered"])
        self.assertIn("express: license is registered but not in the recipe",
                      cp.differences(cp.EXPRESS, [row for row in recipe if row["name"] != "license"]))

    def test_a_changed_button_or_match_is_reported(self):
        recipe = [dict(row) for row in dialogs(RECIPES[cp.EXPRESS][0])]
        next(row for row in recipe if row["name"] == "service_vat_basis")["button"] = cp.CASH_BASIS
        self.assertEqual(len(cp.differences(cp.EXPRESS, recipe)), 1)
        self.assertIn("service_vat_basis.button differs", cp.differences(cp.EXPRESS, recipe)[0])


class RegisterTests(unittest.TestCase):
    def rows(self):
        return [(ledger, row) for ledger in cp.DIALOGS for row in cp.of(ledger)]

    def test_every_dialog_has_a_class_and_a_unique_name(self):
        for ledger in cp.DIALOGS:
            names = [row["name"] for row in cp.of(ledger)]
            self.assertEqual(len(names), len(set(names)), ledger)
        for ledger, row in self.rows():
            self.assertIn(row["cls"], cp.CLASSES, row["name"])
            self.assertIsInstance(row["verified"], bool, row["name"])

    def test_refusals_and_session_dialogs_end_in_known_operator_reasons(self):
        for ledger, row in self.rows():
            if row["cls"] in (cp.BLOCK, cp.SESSION):
                self.assertTrue(cp.reasons(row), (ledger, row["name"]))
            for reason in cp.reasons(row):
                self.assertIn(reason, contracts.REASONS, (ledger, row["name"]))

    def test_notices_are_clicked_through_and_never_end_a_document(self):
        for ledger, row in self.rows():
            if row["cls"] == cp.NOTICE:
                self.assertTrue(row.get("auto") and row.get("button"), row["name"])
                self.assertNotIn("outcome", row)
                for reason in cp.reasons(row):
                    self.assertNotEqual(contracts.REASONS[reason], contracts.HUMAN, row["name"])

    def test_every_choice_names_the_question_asked_before_the_start(self):
        found = [(ledger, row) for ledger, row in self.rows() if row["cls"] == cp.CHOICE]
        self.assertEqual(sorted(row["name"] for _, row in found), ["service_output_vat_basis", "service_vat_basis", "unit"])
        for ledger, row in found:
            self.assertIn(row["ask"], precheck.KINDS, row["name"])
        self.assertEqual([row["name"] for row in cp.choices(cp.MRERP)], [])

    def test_a_wired_choice_clicks_the_button_of_the_answer_and_the_unwired_ones_are_the_registered_two(self):
        unwired = set()
        for ledger, row in self.rows():
            if row["cls"] != cp.CHOICE:
                continue
            if not row.get("answer"):
                unwired.add((ledger, row["name"]))
                continue
            self.assertEqual(row["answer"], "srv_vattyp")
            by_value = row.get("button_by_srv_vattyp") or {}
            for option in row["options"]:
                self.assertEqual(by_value.get(option["code"], row["button"]), option["button"], (row["name"], option))
        self.assertEqual(unwired, set(cp.NOT_WIRED))
        self.assertLessEqual(len(cp.NOT_WIRED), 1)


if __name__ == "__main__":
    unittest.main()
