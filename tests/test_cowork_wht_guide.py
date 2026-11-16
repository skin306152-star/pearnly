"""Cowork 预扣税 ภ.ง.ด.3 / 53 草稿的引导式（总表 CW-D-WHT，`assistant.guided.wht`）：用共用引擎 `assistant.guide.run` 真实现走一遍。

替身沿用对账引导的测试（`test_cowork_recon_guide`），另加报税草稿服务、对话公司（`projects.of_session` + 公司主档）的替身。

断言的是应有状态（主会话 11-16 批的口径）：
- 有报税草稿功能且能读识别记录的人才见「预扣税」；
- 月份推荐对话月份；对话公司没有 13 位税号：题目下一行事实、确定不了；
- 确定出草稿一次，**付款公司就是本对话公司**（税号由服务端取，卡上没有公司可选）；结果给两张表的收款方数与税额、待确认几行、工作簿；
- 没定所得类型的收款方每位一题（13 种，只一种税率时推荐、点了才算）；作答按税号记住、重出草稿，结果里那位不再问；不在 13 种里的不收。

    python3 tests/test_cowork_wht_guide.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_cowork_recon_guide as R  # noqa: E402

STATE, run, SESSION = R.STATE, R.run, R.SESSION
CODES = ("service", "hire_of_work", "professional", "rent", "advertising", "promotion", "prize", "transport", "insurance",
         "interest", "dividend", "commission", "other")


def _wht_draft(tax_id, period, edits=None, name="", branch=""):
    STATE["drafts"].append((tax_id, period, list(edits or []), name, branch))
    asked = [q for q in ({"payee_tax_id": "0105546015062", "payee_name": "Siam Rent", "base": "10000.00", "tax": "500.00",
                          "suggested": "rent"},
                         {"payee_tax_id": "3100100100101", "payee_name": "Somchai", "base": "2000.00", "tax": "60.00",
                          "suggested": ""}) if q["payee_tax_id"] not in STATE["remembered"]]
    return {"status": "drafted", "draft": "WHT-1", "period": period, "questions": asked,
            "forms": {"pnd53": {"payees": 2, "tax": "530.00", "base": "11000.00"}, "pnd3": {"payees": 1, "tax": "60.00",
                                                                                              "base": "2000.00"}},
            "totals": {"tax": "590.00"}, "review": [{"row": "H9"}]}


def _answer(items, draft=""):
    STATE["answered"].append((list(items), draft))
    for item in items:
        STATE["remembered"].add(item["payee_tax_id"])
    return {"status": "saved"}


R._module("pearnly_cowork.tax")
R._module("pearnly_cowork.tax.access", allowed=lambda user=None: STATE.get("tax", True))
R._module("pearnly_cowork.tax.wht", INCOME_CODES=CODES,
          tax_id=lambda value: "".join(ch for ch in str(value or "") if ch.isdigit()))
R._module("pearnly_cowork.tax.service", wht_draft=_wht_draft, answer=_answer,
          workbook=lambda summary, lang=None: ("PND-2026-10.xlsx", b"PK"))
R._module("pearnly_core.assistant.projects", of_session=lambda session: ("CO-1", "2026-10", "00000"))
R._module("pearnly_cowork.access.scope", require=lambda capability: None, default_workspace=lambda: "WS1",
          require_read=lambda: None if STATE.get("read", True) else (_ for _ in ()).throw(PermissionError("x")))
R.frappe.db.get_value = lambda doctype, name, fields, as_dict=False: (
    {"tax_id": STATE["company_tax"], "title": "T9 Sincer"} if as_dict else STATE["company_tax"])

from pearnly_cowork.assistant.guided import wht  # noqa: E402


class Base(R.Base):
    def setUp(self):
        super().setUp()
        STATE.update(drafts=[], answered=[], remembered=set(), company_tax="0105556000009", tax=True, read=True)
        R.HOOKS["pearnly_guides"] = ["pearnly_cowork.assistant.guided.wht.GUIDES"]

    def drafted(self):
        name = run.start(SESSION, "wht")["run"]
        found = run.answer(name, {"period": "2026-10"})
        return name, run.confirm(name, found["version"])


class Menu(Base):
    def test_tax_drafts_and_document_reading_are_both_needed(self):
        self.assertEqual(R.registry.offered()["chat"], ["wht"])
        STATE["read"] = False
        self.assertEqual(R.registry.offered()["chat"], [])
        STATE.update(read=True, tax=False)
        self.assertEqual(R.registry.offered()["chat"], [])


class Month(Base):
    def test_the_month_is_suggested_and_there_is_no_company_to_choose(self):
        found = run.start(SESSION, "wht")
        self.assertEqual(list(self.asked(found)), ["period"])
        self.assertEqual(self.asked(found)["period"]["preselected"], "2026-10")
        self.assertNotIn("facts", self.asked(found)["period"])

    def test_a_conversation_company_without_a_tax_id_cannot_draft(self):
        STATE["company_tax"] = ""
        found = run.start(SESSION, "wht")
        self.assertEqual(self.asked(found)["period"]["facts"], ["tax.company_invalid"])
        refused = run.answer(found["run"], {"period": "2026-10"})
        self.assertEqual(refused["refused"], {"period": "tax.company_invalid"})


class Draft(Base):
    def test_the_draft_is_for_the_conversation_company_once(self):
        name, done = self.drafted()
        self.assertEqual(STATE["drafts"], [("0105556000009", "2026-10", [], "T9 Sincer", "00000")])
        result = done["result"]
        self.assertEqual((result["forms"]["pnd53"]["payees"], result["forms"]["pnd3"]["tax"], result["review_total"]),
                         (2, "60.00", 1))
        self.assertEqual(result["files"], [{"file": "F1", "name": "PND-2026-10.xlsx"}])
        run.confirm(name, done["version"] - 1)
        self.assertEqual(len(STATE["drafts"]), 1)

    def test_payees_without_an_income_type_are_asked_one_by_one(self):
        _name, done = self.drafted()
        subs = done["result"]["questions"]
        self.assertEqual([sub["key"] for sub in subs], ["payee.0105546015062", "payee.3100100100101"])
        self.assertEqual(subs[0]["title"], "Siam Rent 0105546015062")
        self.assertEqual(subs[0]["preselected"], "rent")
        self.assertNotIn("preselected", subs[1])
        self.assertEqual([row["code"] for row in subs[0]["options"]], list(CODES))
        self.assertEqual(subs[0]["options"][3]["name"], "cowork.tax.income.rent")

    def test_answers_are_remembered_by_tax_id_and_the_draft_is_made_again(self):
        name, done = self.drafted()
        after = run.follow(name, {"payee.0105546015062": "rent", "payee.3100100100101": "service"}, done["version"])
        self.assertEqual(STATE["answered"], [([{"payee_tax_id": "0105546015062", "income_type": "rent"},
                                               {"payee_tax_id": "3100100100101", "income_type": "service"}], "WHT-1")])
        self.assertEqual(len(STATE["drafts"]), 2)
        self.assertNotIn("questions", after["result"])

    def test_an_income_type_outside_the_list_is_not_taken(self):
        name, done = self.drafted()
        refused = run.follow(name, {"payee.0105546015062": "salary", "payee.3100100100101": "service"}, done["version"])
        self.assertEqual(refused["refused"], {"questions": "not offered"})
        self.assertEqual(STATE["answered"], [])


if __name__ == "__main__":
    unittest.main()
