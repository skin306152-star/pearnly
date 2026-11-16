"""Cowork 增值税 ภ.พ.30 草稿的引导式（总表 CW-D-VAT，`assistant.guided.vat`）：用共用引擎 `assistant.guide.run` 真实现走一遍。

替身沿用对账引导的测试（`test_cowork_recon_guide`：对话账套、写入闸、读回服务、对话文件），另加报税草稿服务的替身。

断言的是应有状态（主会话 11-16 批的口径）：
- 有报税草稿功能的人才见「增值税」；
- 月份推荐对话月份；本月回读已读完不问，没有就出卡时请读一次；本对话账套不是 Express：回读那一步给「只有 Express 账套可以回读」、确定不了；
- 第 3 栏、第 10 栏可不填，负数不收；第 10 栏推荐上月草稿第 12 栏（写明哪个月的草稿）、点了才算，上月没有草稿不推荐；
- 零税额的行推荐零税率、两项用报表第 2、3 栏的栏名；
- 确定：出草稿一次、卡上填的数原样交给服务（CC 不经手）、工作簿进对话；服务回「没有回读」退回回读那一步。

    python3 tests/test_cowork_vat_guide.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_cowork_recon_guide as R  # noqa: E402  装好 frappe 与对话、读回、文件的替身

STATE, run, SESSION = R.STATE, R.run, R.SESSION


def _draft(connection, key, period, options=None):
    STATE["drafts"].append((connection, key, period, dict(options or {})))
    if STATE.get("no_readback"):
        return {"status": "readback_needed"}
    return {"status": "drafted", "draft": "VAT-1", "period": period, "outcome": "pay",
            "boxes": {"5": "700.00", "7": "300.00", "11": "400.00"}, "issues": [{"code": "x"}, {"code": "y"}]}


def _previous(connection, key, period):
    return dict(STATE.get("previous") or {})


R._module("pearnly_cowork.tax")
R._module("pearnly_cowork.tax.access", allowed=lambda user=None: STATE.get("tax", True))
R._module("pearnly_cowork.tax.service", vat_draft=_draft, previous_excess=_previous,
          workbook=lambda summary, lang=None: ("PP30-2026-10.xlsx", b"PK"))

from pearnly_cowork.assistant.guided import vat  # noqa: E402

READY = {"readback": "RB1", "status": "ready", "account_set": "ABC 2026", "period": "2026-10"}


class Base(R.Base):
    def setUp(self):
        super().setUp()
        STATE.update(drafts=[], tax=True)
        R.HOOKS["pearnly_guides"] = ["pearnly_cowork.assistant.guided.vat.GUIDES"]

    def to_options(self):
        STATE["readbacks"]["2026-10"] = dict(READY)
        name = run.start(SESSION, "vat")["run"]
        return name, run.answer(name, {"period": "2026-10"})


class Menu(Base):
    def test_only_people_with_tax_drafts_see_vat(self):
        self.assertEqual(R.registry.offered()["chat"], ["vat"])
        STATE["tax"] = False
        self.assertEqual(R.registry.offered()["chat"], [])


class Steps(Base):
    def test_the_month_is_suggested_and_a_finished_readback_is_used_without_asking(self):
        found = run.start(SESSION, "vat")
        self.assertEqual(self.asked(found)["period"]["preselected"], "2026-10")
        _name, found = self.to_options()
        self.assertEqual(list(self.asked(found)), ["exempt_sales", "excess_brought_forward", "zero_vat_as"])
        self.assertEqual(STATE["requests"], [])

    def test_without_a_readback_the_computer_is_asked_once(self):
        name = run.start(SESSION, "vat")["run"]
        found = run.answer(name, {"period": "2026-09"})
        self.assertTrue(self.asked(found)["readback"]["wait"])
        self.assertEqual(STATE["requests"], [("CONN-E", "2026", "2026-09")])

    def test_an_mrerp_conversation_book_says_only_express_can_be_read_back(self):
        STATE["current"] = R.MRERP_ID
        self.now["ledger"] = R.MRERP_ID
        name = run.start(SESSION, "vat")["run"]
        found = run.answer(name, {"period": "2026-10"})
        question = self.asked(found)["readback"]
        self.assertEqual((question["options"], question["facts"]), ([], ["Only Express account sets can be read back."]))
        self.assertFalse(found["card"]["ready"])

    def test_box_10_suggests_last_months_box_12_with_its_month_and_counts_only_when_clicked(self):
        STATE["previous"] = {"value": "250.00", "period": "2026-09"}
        _name, found = self.to_options()
        excess = self.asked(found)["excess_brought_forward"]
        self.assertEqual((excess["preselected"], excess["facts"]), ("250.00", ["Box 12 of the 2026-09 draft: 250.00"]))
        self.assertNotIn("value", excess)
        STATE["previous"] = {}
        _name, found = self.to_options()
        self.assertNotIn("preselected", self.asked(found)["excess_brought_forward"])

    def test_zero_vat_rows_suggest_zero_rated_with_the_report_box_names(self):
        _name, found = self.to_options()
        zero = self.asked(found)["zero_vat_as"]
        self.assertEqual(zero["preselected"], "zero_rated")
        self.assertEqual(zero["options"], [{"code": "zero_rated", "name": "cowork.tax.pp30.box.2"},
                                           {"code": "exempt", "name": "cowork.tax.pp30.box.3"}])

    def test_boxes_3_and_10_may_be_left_empty_but_not_negative(self):
        name, _found = self.to_options()
        self.assertEqual(run.answer(name, {"exempt_sales": "-5"})["refused"], {"exempt_sales": "tax.option_invalid"})
        found = run.answer(name, {"zero_vat_as": "zero_rated"})
        self.assertTrue(found["card"]["ready"])


class Confirm(Base):
    def test_the_draft_is_made_once_from_what_was_entered_on_the_card(self):
        name, _found = self.to_options()
        found = run.answer(name, {"exempt_sales": "1000.50", "excess_brought_forward": "250", "zero_vat_as": "exempt"})
        done = run.confirm(name, found["version"])
        self.assertEqual(STATE["drafts"], [("CONN-E", "2026", "2026-10",
                                            {"exempt_sales": "1000.50", "excess_brought_forward": "250", "zero_vat_as": "exempt"})])
        result = done["result"]
        self.assertEqual((result["status"], result["outcome"], result["boxes"]["11"], result["issues_total"]),
                         ("ready", "pay", "400.00", 2))
        self.assertEqual(result["files"], [{"file": "F1", "name": "PP30-2026-10.xlsx"}])
        run.confirm(name, found["version"])
        self.assertEqual(len(STATE["drafts"]), 1)

    def test_no_readback_any_more_goes_back_to_the_readback_step(self):
        name, _found = self.to_options()
        found = run.answer(name, {"zero_vat_as": "zero_rated"})
        STATE["no_readback"] = True
        back = run.confirm(name, found["version"])
        self.assertEqual(back["outcome"], run.REFUSED)
        self.assertEqual(back["card"]["returned"]["readback"]["reason"], "readback_needed")


if __name__ == "__main__":
    unittest.main()
