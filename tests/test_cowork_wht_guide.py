"""Cowork 预扣税 ภ.ง.ด.3 / 53 草稿的引导式（总表 CW-D-WHT，`assistant.guided.wht`）：用共用引擎 `assistant.guide.run` 真实现走一遍。

替身沿用对账引导的测试（`test_cowork_recon_guide`），另加报税草稿服务、对话公司（`projects.of_session` + 公司主档）的替身。

断言的是应有状态（主会话 11-16 批的口径）：
- 有报税草稿功能且能读识别记录的人才见「预扣税」；
- 月份推荐对话月份；对话公司没有 13 位税号：题目下一行事实、确定不了；
- 答了月份出草稿（**付款公司就是本对话公司**，税号由服务端取，卡上没有公司可选），进复核卡；
- 复核卡两张表、每行可改：没定所得类型的收款方（只一种税率时推荐；改了按税号记住、重出草稿，那位不再出现）、待确认的行
  （算不算、日期、类型、税率、基数、税额、收款方税号、表；改动交给草稿的逐行改动，服务不收的回 refused）；
- 确定：工作簿进对话、结果给两张表的收款方数与税额、还待确认几行；同一确定不出两次工作簿。

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
    for edit in edits or ():
        if edit.get("row") != "H9" or (edit.get("rate") is not None and str(edit["rate"]) == "-1"):
            return {"status": "drafted", "draft": "WHT-1", "period": period, "questions": [], "review": [],
                    "forms": {}, "totals": {"tax": "0.00"}, "edits_rejected": [{"row": edit.get("row"), "reason": "edit_invalid"}]}
        STATE["included"] = edit.get("include", STATE.get("included", False))
    asked = [q for q in ({"payee_tax_id": "0105546015062", "payee_name": "Siam Rent", "base": "10000.00", "tax": "500.00",
                          "suggested": "rent"},
                         {"payee_tax_id": "3100100100101", "payee_name": "Somchai", "base": "2000.00", "tax": "60.00",
                          "suggested": ""}) if q["payee_tax_id"] not in STATE["remembered"]]
    review = [] if STATE.get("included") else [
        {"row": "H9", "payee_name": "Mali", "payee_tax_id": "3100100100199", "payment_date": "2026-10-05", "income_type": "",
         "rate": "3", "base": "1000.00", "tax": "30.00", "form": "pnd3", "include": False,
         "issues": [{"code": "pairing_unclear"}]}]
    return {"status": "drafted", "draft": "WHT-1", "period": period, "questions": asked,
            "forms": {"pnd53": {"payees": 2, "tax": "530.00", "base": "11000.00"}, "pnd3": {"payees": 1, "tax": "60.00",
                                                                                              "base": "2000.00"}},
            "totals": {"tax": "590.00"}, "review": review, "edits_rejected": []}


def _answer(items, draft=""):
    STATE["answered"].append((list(items), draft))
    for item in items:
        STATE["remembered"].add(item["payee_tax_id"])
    return {"status": "saved"}


R._module("pearnly_cowork.tax")
R._module("pearnly_cowork.tax.access", allowed=lambda user=None: STATE.get("tax", True))
R._module("pearnly_cowork.tax.wht", INCOME_CODES=CODES, FORMS=("pnd53", "pnd3"),
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
        STATE.update(drafts=[], answered=[], remembered=set(), company_tax="0105556000009", tax=True, read=True, included=False)
        R.CACHE.clear()
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


class Review(Base):
    def reviewed(self):
        name = run.start(SESSION, "wht")["run"]
        return name, run.answer(name, {"period": "2026-10"})

    def table(self, found, key):
        return next(t for t in found["review"]["tables"] if t["key"] == key)

    def test_answering_the_month_drafts_for_the_conversation_company_and_shows_the_review(self):
        name, found = self.reviewed()
        self.assertEqual(found["status"], run.REVIEWING)
        self.assertEqual(STATE["drafts"], [("0105556000009", "2026-10", [], "T9 Sincer", "00000")])
        self.assertEqual(found["review"]["total"], "590.00")
        self.assertEqual(self.docs[name].draft, "WHT-1@2026-10@1")

    def test_payees_without_an_income_type_are_rows_whose_type_can_be_set(self):
        _name, found = self.reviewed()
        rows = self.table(found, "payees")["rows"]
        self.assertEqual([row["code"] for row in rows], ["payee.0105546015062", "payee.3100100100101"])
        self.assertEqual((rows[0]["cells"], rows[1]["cells"]), ({"income_type": "rent"}, {"income_type": ""}))
        options = rows[0]["editable"]["income_type"]["options"]
        self.assertEqual([o["code"] for o in options], list(CODES))
        self.assertEqual(options[3]["name"], "cowork.tax.income.rent")

    def test_pending_rows_show_their_issue_and_every_editable_cell(self):
        _name, found = self.reviewed()
        row = self.table(found, "pending")["rows"][0]
        self.assertEqual((row["code"], row["cells"]["include"], row["issues"]), ("H9", "0", ["cowork.tax.issue.pairing_unclear"]))
        self.assertEqual(set(row["editable"]), {"include", "payment_date", "income_type", "rate", "base", "tax", "payee_tax_id", "form"})
        self.assertEqual(row["editable"]["rate"], {"input": "number"})
        columns = self.table(found, "pending")["columns"]
        self.assertEqual(columns[0], {"key": "payee_name", "label": "cowork.tax.col.payee_name"})   # 前端要已翻译的 label

    def test_setting_a_payees_income_type_remembers_it_by_tax_id_and_drafts_again(self):
        name, found = self.reviewed()
        ctx = {"session": SESSION, "run": name, "answers": {"period": "2026-10"}, "draft": self.docs[name].draft}
        out = wht.edit(ctx, self.docs[name].draft, "payee.0105546015062", "income_type", "rent")
        self.assertEqual(STATE["answered"], [([{"payee_tax_id": "0105546015062", "income_type": "rent"}], "WHT-1")])
        self.assertEqual(out, {"draft": "WHT-1@2026-10@2"})
        self.docs[name].draft = out["draft"]
        rows = self.table(run.view(self.docs[name]), "payees")["rows"]
        self.assertEqual([row["code"] for row in rows], ["payee.3100100100101"])

    def test_a_pending_row_is_edited_through_the_drafts_row_edits(self):
        name, _found = self.reviewed()
        draft = self.docs[name].draft
        ctx = {"session": SESSION, "run": name, "answers": {"period": "2026-10"}, "draft": draft}
        out = wht.edit(ctx, draft, "H9", "include", "1")
        self.assertEqual(STATE["drafts"][-1][2], [{"row": "H9", "include": True}])
        self.docs[name].draft = out["draft"]
        self.assertEqual(self.table(run.view(self.docs[name]), "pending")["rows"], [])

    def test_an_edit_the_service_does_not_take_is_refused(self):
        name, _found = self.reviewed()
        draft = self.docs[name].draft
        ctx = {"session": SESSION, "run": name, "answers": {"period": "2026-10"}, "draft": draft}
        self.assertEqual(wht.edit(ctx, draft, "H9", "rate", "-1"),
                         {"refused": {"rate": "edit_invalid"}, "refused_text": {"rate": "tax.option_invalid"}})
        self.assertEqual(wht.edit(ctx, draft, "H9", "address", "x")["refused"], {"address": "tax.edit_invalid"})
        self.assertEqual(wht.edit(ctx, draft, "payee.0105546015062", "rate", "3")["refused"], {"rate": "tax.edit_invalid"})

    def test_confirming_puts_the_workbook_in_the_conversation_once(self):
        name, found = self.reviewed()
        done = run.confirm(name, found["version"])
        result = done["result"]
        self.assertEqual((result["forms"]["pnd3"]["tax"], result["review_total"], result["questions_total"]), ("60.00", 1, 2))
        self.assertEqual(result["files"], [{"file": "F1", "name": "PND-2026-10.xlsx"}])
        run.confirm(name, found["version"])
        self.assertEqual(STATE["added"], ["PND-2026-10.xlsx"])


if __name__ == "__main__":
    unittest.main()
