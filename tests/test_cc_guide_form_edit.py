"""按表单的业务在复核卡上改一格（``guide.run.edit`` 的 form 分派；P3 预扣税草稿的待确认行逐行复核要用）。

断言的是应有状态（沿用 `test_cc_guide_run` 的替身与收款样子的表单声明，声明多点名一个 ``edit``）：
- 只在复核中收；还在问、已确定的不收；版本与看到的不一致回 ``changed``、什么都不改；
- 产品回新草稿号：换成新草稿、版本 +1、复核卡按新草稿重算；回 ``refused``（带已翻译说明）照原样交回、草稿与版本不变；
- 声明没点名 ``edit`` 的不收；声明形状：表单的 ``edit`` 要有 ``review``，按聊天的不能有 ``edit``。

    python3 tests/test_cc_guide_form_edit.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_cc_guide_run as R  # noqa: E402  装好 frappe 替身、引导运行与表单声明

run = R.run
from pearnly_core.assistant.guide import declaration  # noqa: E402

GUIDE = {**R.GUIDE, "edit": "app.edit"}


class Base(R.Base):
    def setUp(self):
        super().setUp()
        self.edits = []
        R.ATTRS["app.GUIDES"] = [GUIDE]
        R.ATTRS["app.edit"] = self.edit
        R.ATTRS["app.review"] = lambda ctx, draft: {"draft": draft, "rows": [ctx["answers"]["customer"]]}

    def edit(self, ctx, draft, row, field, value):
        self.edits.append((draft, row, field, value))
        if field == "amount" and str(value).startswith("-"):
            return {"refused": {"amount": "negative"}, "refused_text": {"amount": "金额不能为负"}}
        return {"draft": f"{draft}+{row}.{field}={value}"}


class FormEdit(Base):
    def test_an_edit_on_review_takes_the_new_draft_and_the_review_follows_it(self):
        name, found = self.to_review()
        self.assertEqual(found["status"], run.REVIEWING)
        before = found["version"]
        found = run.edit(name, "IV001", "rate", "3", before)
        self.assertEqual(self.edits, [(f"R:{name}", "IV001", "rate", "3")])
        self.assertEqual(found["version"], before + 1)
        self.assertEqual(found["review"]["draft"], f"R:{name}+IV001.rate=3")  # 复核卡按新草稿重算
        self.assertEqual(self.docs[name].draft, f"R:{name}+IV001.rate=3")

    def test_a_refused_edit_comes_back_as_it_is_and_changes_nothing(self):
        name, found = self.to_review()
        refused = run.edit(name, "IV001", "amount", "-5", found["version"])
        # 拒收形状同作答与单据源：{格: {code, text}}（text 按术语表 guide.refused.<码>，这里没有译文为空串）；产品的说明在 refused_text 原样交回
        self.assertEqual((refused["outcome"], refused["refused"], refused["refused_text"]),
                         (run.REFUSED, {"amount": {"code": "negative", "text": ""}}, {"amount": "金额不能为负"}))
        self.assertEqual((self.docs[name].draft, self.docs[name].version), (f"R:{name}", found["version"]))

    def test_an_edit_is_only_taken_on_review_and_on_the_version_seen(self):
        name = run.start("S1", "collection")["run"]
        found = run.view(self.docs[name])
        self.assertEqual(run.edit(name, "IV001", "rate", "3", found["version"])["outcome"], run.REFUSED)  # 还在问
        name, found = self.to_review()
        self.assertEqual(run.edit(name, "IV001", "rate", "3", found["version"] - 1)["outcome"], run.CHANGED)
        self.assertEqual(self.edits, [])

    def test_an_empty_row_or_cell_is_refused_and_a_product_answer_out_of_contract_is_an_error(self):
        name, found = self.to_review()
        self.assertEqual(run.edit(name, "", "rate", "3", found["version"])["outcome"], run.REFUSED)
        self.assertEqual(run.edit(name, "IV001", "", "3", found["version"])["outcome"], run.REFUSED)
        self.assertEqual(self.edits, [])
        R.ATTRS["app.edit"] = lambda ctx, draft, row, field, value: {"draft": ""}
        self.assertRaises(ValueError, run.edit, name, "IV001", "rate", "3", found["version"])

    def test_a_form_guide_that_names_no_edit_refuses(self):
        R.ATTRS["app.GUIDES"] = [R.GUIDE]
        name, found = self.to_review()
        self.assertEqual(run.edit(name, "IV001", "rate", "3", found["version"])["outcome"], run.REFUSED)
        self.assertEqual(self.edits, [])


class Shape(unittest.TestCase):
    def test_edit_on_a_form_guide_needs_review_and_a_chat_guide_has_none(self):
        self.assertEqual(declaration.problems(GUIDE), [])
        without_review = {key: value for key, value in GUIDE.items() if key != "review"}
        self.assertTrue(any("needs review" in text for text in declaration.problems(without_review)))
        self.assertIn("app.edit", declaration.paths(GUIDE))


if __name__ == "__main__":
    unittest.main()
