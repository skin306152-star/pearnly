"""ERP 收款的引导式（总设计 P1，`collection.guide` + 声明 `collection.cc_guide`）：用共用引擎 `assistant.guide.run` 真实现走一遍。

不连库、不出网：沿用收款测试的 Frappe 替身（`test_erp_collection`：NLTTEST 收款类型、快照里 2100883 两张 IV1 1000 / IV2 500）与
对话替身（`test_erp_collection_cc`：本对话挂 CO-1、本对话账套 = 那一本、对话附件）；引导运行记录放内存（真实站点上的表与并发另在
实验栈验，`scripts/regression/guide_run_check.py`）。

断言的是应有状态：
- 公司、日期、账套能定就不问（进「已定」），先问客户；客户编码按原样比，只差空白的那一件不算；
- 发票按客户即时读回：在读时照列上次读到的、题目下一行「正在读取 Express」、页面要再取；读到了照列；电脑没连 / 要更新照实说；
  读回到了而一张都没有才说没有未收清的发票；
- 金额不超此刻可收；方式合计要等于发票合计；有转账才问凭证、每种转账一张；
- 答完建草稿、进复核：复核卡上公司·账套·客户·日期·各张收多少·方式·合计，每行指回那一步；写进草稿的客户、发票、金额就是卡上的；
- 确定 = 页面上这一下（``guide:<运行>:<版本>``）：放行、排队、结果卡；同一确定不放第二次；
- 复核后别人收了同一张：确定时退回发票那一步、按此刻余额重问；复核后草稿在别处改过：回复核、带新版本；
- 改了前面的答案再生成草稿：上一张草稿作废；收款日期不在期间里时问日期并给可录的日期段。

    python3 tests/test_erp_collection_guide.py
"""
import datetime
import importlib
import json
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_erp_collection_cc as CC  # noqa: E402  装好 Frappe 替身、收款数据与对话替身

T, frappe, SESSION = CC.T, CC.frappe, CC.SESSION
C = T.C

from pearnly_core.assistant.guide import registry, run  # noqa: E402
from pearnly_erp_portal.collection import cc_guide, guide  # noqa: E402

GUIDE_HOOKS = {**CC.HOOKS, "pearnly_guides": ["pearnly_erp_portal.collection.cc_guide.GUIDES"]}
IV1_600 = [{"code": "IV1", "amount": "600"}]
CASH_600 = [{"code": guide.CASH, "amount": "600"}]


class Doc(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class Base(CC.Base):
    def setUp(self):
        super().setUp()
        self.runs, self.clock = {}, [0]
        self.now = {"session_id": SESSION, "mode": "chat", "company": "CO-1", "ledger": T.BOOK["key"], "period": "2026-11",
                    "stopped_at": None}
        patches = [
            mock.patch.object(frappe, "get_hooks", side_effect=lambda name: GUIDE_HOOKS.get(name, {})),
            mock.patch.object(run, "_insert", side_effect=self.insert),
            mock.patch.object(run, "_locked", side_effect=lambda name: self.runs.get(name)),
            mock.patch.object(run, "_save", side_effect=self.save),
            mock.patch.object(run, "_open_runs", side_effect=lambda sid: [
                d.name for d in self.runs.values() if d.session_id == sid and d.status in run.OPEN]),
            mock.patch.object(run, "_conversation", side_effect=lambda sid: dict(self.now)),
            mock.patch.object(run, "_announce", side_effect=lambda doc: None),
            # 交文件登记（`assistant.files.commit(via="guide")`）：对话附件替身里有的才认下
            mock.patch.object(run, "_commit_files", side_effect=lambda sid, by_step: next((k for k, ids in by_step.items() if any(i not in self.attached for i in ids)), "")),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        registry_cache = getattr(registry, "_cache", None)
        if isinstance(registry_cache, dict):
            registry_cache.clear()
        self.as_user("a@x")

    def insert(self, values):
        self.clock[0] += 1
        doc = Doc(values, name=f"RUN{len(self.runs) + 1}", modified=datetime.datetime(2026, 11, 15, 10, 0, self.clock[0]))
        self.runs[doc.name] = doc
        return doc

    def save(self, doc):
        self.clock[0] += 1
        doc.modified = datetime.datetime(2026, 11, 15, 10, 0, self.clock[0])

    def read_back(self, rows=None):
        """电脑把这位客户的未收发票读回来了（默认照快照里的两张）。"""
        key = T.snapshot.request_key(T.BOOK["key"], "2100883")
        rows = rows if rows is not None else [
            {"docnum": "IV1", "rectyp": "3", "docdat": "2026-10-01", "customer": "2100883", "salesman": "S1",
             "department": "CPI", "net": "1000.00", "received": "0.00", "remaining": "1000.00"},
            {"docnum": "IV2", "rectyp": "3", "docdat": "2026-10-05", "customer": "2100883", "salesman": "S2",
             "department": "", "net": "800.00", "received": "300.00", "remaining": "500.00"}]
        if key in self.requests.made:
            self.requests.finish(key, rows, "2026-11-15T04:00:00+00:00")

    @staticmethod
    def questions(found):
        return {q["key"]: q for q in found["card"]["questions"]}

    def to_invoices(self):
        name = run.start(SESSION, "collection")["run"]
        found = run.answer(name, {"customer": "2100883"})
        return name, found

    def to_review(self, invoices=None, methods=None):
        name, _found = self.to_invoices()
        self.read_back()
        run.answer(name, {"invoices": invoices or IV1_600})
        return name, run.answer(name, {"methods": methods or CASH_600})


class Declaration(unittest.TestCase):
    def test_collection_is_a_form_guide_in_erp_direct_write_with_every_path_defined(self):
        found = cc_guide.GUIDES[0]
        self.assertEqual((found["source"], found["modes"], found["writes"]), ("form", ["chat"], True))
        self.assertEqual([step["key"] for step in found["steps"]],
                         ["company", "day", "book", "customer", "invoices", "methods", "voucher"])
        rules = {step["key"]: step["rule"] for step in found["steps"]}
        # 钱和对象每次明选（不记不预选）；公司 / 日期 / 账套定得下来就不问
        self.assertEqual({k for k, v in rules.items() if v == "always"}, {"customer", "invoices", "methods", "voucher"})
        for step in found["steps"]:
            for key in ("options", "default", "check", "apply", "facts"):
                if key in step:
                    module, _dot, attr = step[key].rpartition(".")
                    self.assertTrue(callable(getattr(importlib.import_module(module), attr)), step[key])


class Walk(Base):
    def test_company_day_and_book_are_decided_and_the_customer_is_asked_first(self):
        found = run.start(SESSION, "collection")
        self.assertEqual(list(self.questions(found)), ["customer"])
        decided = {item["key"]: item for item in found["card"]["decided"]}
        self.assertEqual(set(decided), {"company", "day", "book"})
        self.assertEqual((decided["company"]["value"], decided["book"]["value"], decided["book"]["source"]),
                         ("CO-1", T.BOOK["key"], "conversation"))
        customers = self.questions(found)["customer"]["options"]
        self.assertEqual([row["code"] for row in customers], ["2100883", "2100900"])

    def test_invoices_while_reading_are_listed_with_a_fact_and_the_page_asks_again(self):
        _name, found = self.to_invoices()
        question = self.questions(found)["invoices"]
        self.assertEqual(question["facts"], ["Reading from Express"])
        self.assertTrue(question["wait"])
        self.assertEqual([(row["code"], row["max"]) for row in question["options"]], [("IV1", "1000.00"), ("IV2", "500.00")])

    def test_invoices_once_read_are_listed_without_a_fact(self):
        name, _found = self.to_invoices()
        self.read_back()
        found = run.view(self.runs[name])
        question = self.questions(found)["invoices"]
        self.assertNotIn("facts", question)
        self.assertNotIn("wait", question)

    def test_no_open_invoices_is_said_only_once_the_read_came_back(self):
        name, _found = self.to_invoices()
        self.assertNotIn("No open invoices", self.questions(_found)["invoices"]["facts"])
        self.read_back(rows=[])
        found = run.view(self.runs[name])
        question = self.questions(found)["invoices"]
        self.assertEqual((question["options"], question["facts"]), ([], ["No open invoices"]))

    def test_the_whole_walk_ends_in_a_queued_collection_with_the_values_from_the_card(self):
        name, found = self.to_review()
        self.assertEqual(found["status"], "reviewing")
        review = found["review"]
        lines = {line["key"]: line for line in review["lines"]}
        self.assertEqual((lines["customer"]["value"], lines["customer"]["step"]), ("2100883 ร้านป้าแดง", "customer"))
        self.assertEqual(review["tables"][0]["rows"][0]["amount"], "600.00")
        self.assertEqual((review["total"], review["ready"]), ("600.00", True))
        draft, version = guide._draft_name(self.runs[name].draft)
        row = frappe.db.get_value("ERP Collection", draft, ["status", "customer", "cash", "source"], as_dict=True)
        self.assertEqual((row.status, row.customer, str(row.cash), row.source), ("draft", "2100883", "600.00", "cc"))
        done = run.confirm(name, found["version"])
        self.assertEqual((done["status"], done["result"]["status"]), ("done", "queued"))
        self.assertTrue(done["result"]["wait"])
        consent = frappe.db.get_value("ERP Collection", draft, "consent_ref")
        self.assertEqual(consent, f"guide:{name}:{found['version']}")
        self.assertEqual(len(self.jobs.rows), 1)
        again = run.confirm(name, found["version"])  # 同一确定再来一次：不放第二次
        self.assertEqual((again["status"], len(self.jobs.rows)), ("done", 1))

    def test_the_written_result_shows_the_re_number(self):
        name, found = self.to_review()
        run.confirm(name, found["version"])
        draft, _version = guide._draft_name(self.runs[name].draft)
        self.jobs.report("J-" + draft, "success", docnum="RE6900012")
        result = run.view(self.runs[name])["result"]
        self.assertEqual((result["status"], result["docnum"]), ("entered", "RE6900012"))
        self.assertNotIn("wait", result)


class Checks(Base):
    def test_a_customer_code_differing_only_in_blank_space_is_not_the_customer(self):
        name = run.start(SESSION, "collection")["run"]
        refused = run.answer(name, {"customer": "2100883 "})
        self.assertEqual(refused["refused"], {"customer": "not offered"})

    def test_an_amount_over_what_can_be_collected_now_is_refused(self):
        name, _found = self.to_invoices()
        self.read_back()
        refused = run.answer(name, {"invoices": [{"code": "IV2", "amount": "500.01"}]})
        self.assertEqual(refused["refused"], {"invoices": "over max"})

    def test_the_methods_must_add_up_to_the_invoices(self):
        name, _found = self.to_invoices()
        self.read_back()
        run.answer(name, {"invoices": IV1_600})
        refused = run.answer(name, {"methods": [{"code": guide.CASH, "amount": "500"}]})
        self.assertEqual(refused["refused"], {"methods": C.AMOUNT_MISMATCH})

    def test_a_transfer_asks_for_one_slip_per_transfer_and_the_slip_goes_on_the_draft(self):
        name, _found = self.to_invoices()
        self.read_back()
        run.answer(name, {"invoices": IV1_600})
        methods = run.answer(name, {"methods": [{"code": guide.CASH, "amount": "100"}]})
        assert methods["outcome"] == "refused"
        transfer = next(row["code"] for row in self.questions(run.view(self.runs[name]))["methods"]["options"]
                        if row["code"].startswith(guide.TRANSFER))
        found = run.answer(name, {"methods": [{"code": transfer, "amount": "600"}]})
        self.assertEqual(list(self.questions(found)), ["voucher"])
        self.assertEqual(run.answer(name, {"voucher": ["upl_1", "upl_2"]})["refused"], {"voucher": C.VOUCHER_MISSING})
        self.attached["upl_1"] = b"%PDF-1.4 slip"
        reviewed = run.answer(name, {"voucher": ["upl_1"]})
        self.assertEqual(reviewed["status"], "reviewing")
        draft, _version = guide._draft_name(self.runs[name].draft)
        doc = frappe.get_doc("ERP Collection", draft)
        self.assertTrue(doc.payments[0].voucher)


class SentBack(Base):
    def test_someone_collecting_the_same_invoice_after_review_sends_the_invoices_step_back(self):
        name, found = self.to_review(invoices=[{"code": "IV1", "amount": "1000"}],
                                     methods=[{"code": guide.CASH, "amount": "1000"}])
        self.as_user("b@x")
        other = service_draft("1000")
        T.service.release(other["name"], other["version"], "line:evt-9")
        self.as_user("a@x")
        back = run.confirm(name, found["version"])
        self.assertEqual((back["outcome"], back["status"]), ("refused", "asking"))
        self.assertEqual(list(self.questions(back)), ["invoices"])
        self.assertEqual(back["card"]["returned"]["invoices"]["reason"], C.OVER_COLLECTED)
        self.assertEqual(json.loads(self.runs[name].answers), {"customer": "2100883"})

    def test_a_draft_changed_elsewhere_returns_to_review_with_the_new_version(self):
        name, found = self.to_review()
        draft, version = guide._draft_name(self.runs[name].draft)
        T.service.update_draft(draft, version, [{"docnum": "IV1", "amount": "500"}], cash="500")
        back = run.confirm(name, found["version"])
        self.assertEqual((back["outcome"], back["status"]), ("changed", "reviewing"))
        self.assertEqual(self.runs[name].draft, f"{draft}@{version + 1}")
        self.assertEqual(back["review"]["total"], "500.00")

    def test_the_review_card_carries_the_company_and_book_line_under_its_question(self):
        _name, found = self.to_review()
        draft, _version = guide._draft_name(self.runs[_name].draft)
        from pearnly_erp_portal.collection import cc_tools

        self.assertEqual(found["consent_note"], cc_tools.note_withdraw({"collection": draft}))
        self.assertTrue(found["consent_note"])
        self.assertEqual(found["consent_title_key"], "guide.consent.collection")
        self.assertEqual(guide.note({}, ""), "")  # 还没有草稿：那一行不出

    def test_changing_an_answer_under_review_withdraws_the_old_draft(self):
        name, _found = self.to_review()
        old, _version = guide._draft_name(self.runs[name].draft)
        run.answer(name, {"invoices": [{"code": "IV1", "amount": "700"}]})
        run.answer(name, {"methods": [{"code": guide.CASH, "amount": "700"}]})
        self.assertEqual(frappe.db.get_value("ERP Collection", old, "status"), C.WITHDRAWN)
        new, _version = guide._draft_name(self.runs[name].draft)
        self.assertNotEqual(new, old)


class Dates(Base):
    def test_a_day_outside_every_period_is_asked_with_the_periods_that_can_be_recorded(self):
        periods = [{"key": T.BOOK["key"], "label": "NLTTEST", "short": "NLTTEST", "start": "2025-01-01",
                    "end": "2026-12-31", "last": "2025-12-31"}]
        outside = {"day": "2027-01-02", "periods": periods}
        with mock.patch.object(T.service, "day_options", side_effect=lambda company, day=None: None if day == "2026-12-30" else outside):
            found = run.start(SESSION, "collection")
            question = self.questions(found)["day"]
            self.assertEqual(question["input"], "date")
            self.assertTrue(question["facts"][0].startswith("Accounting period"))
            name = found["run"]
            self.assertEqual(run.answer(name, {"day": "2027-01-02"})["refused"], {"day": T.service.DAY_NEEDED})
            after = run.answer(name, {"day": "2026-12-30"})
            self.assertEqual(list(self.questions(after)), ["customer"])


def service_draft(amount):
    return T.service.create_draft("CO-1", "2100883", [{"docnum": "IV1", "amount": amount}], cash=amount)


class MrErpLedger(Base):
    """账本是 MR.ERP 的公司（`collection.mrerp.handles` 打补丁为真）：一笔只收一种钱、收款导入菜单在账套那一步问一次、「正在读取」
    说 MR.ERP。Express 公司照旧（上面各组）。"""

    TWO = [{"code": guide.CASH, "amount": "300"}, {"code": guide.TRANSFER + "1112-01", "amount": "300"}]

    def test_one_money_method_on_mrerp_only(self):
        from pearnly_erp_portal.collection import mrerp as ledger

        ctx = {"answers": {"company": "CO-1", "invoices": IV1_600}}
        self.assertEqual(guide.method_check(ctx, self.TWO), "")  # Express：现金加转账照旧可以
        with mock.patch.object(ledger, "handles", return_value=True):
            self.assertEqual(guide.method_check(ctx, self.TWO), ledger.ONE_METHOD)
            self.assertEqual(guide.method_check(ctx, [{"code": guide.TRANSFER + "1112-01", "amount": "550"},
                                                      {"code": guide.DISCOUNT + "5300-09", "amount": "50"}]), "")

    def test_the_receipt_menu_is_asked_with_the_book_and_remembered(self):
        menus = {"options": [{"code": "169", "name": "รับชำระหนี้"}, {"code": "171", "name": "รับชำระ-2"}]}
        chosen, books = [], []
        ctx = {"answers": {"company": "CO-1"}, "session": SESSION}
        with mock.patch.object(guide.service, "menu_options", return_value=menus), \
                mock.patch.object(guide.service, "choose_menu", side_effect=lambda c, v, d=None: chosen.append(v)), \
                mock.patch.object(guide, "_apply_book", side_effect=lambda ctx, value: books.append(value)):
            self.assertIsNone(guide.book_default(ctx))
            codes = [row["code"] for row in guide.book_options(ctx)["options"]]
            self.assertEqual(codes, [T.BOOK["key"] + "|169", T.BOOK["key"] + "|171"])
            guide.book_apply(ctx, T.BOOK["key"] + "|171")
        self.assertEqual((books, chosen), ([T.BOOK["key"]], ["171"]))
        self.assertIsNotNone(guide.book_default(ctx))  # 不用选菜单时照旧按本对话账套定

    def test_reading_is_said_for_the_ledger(self):
        from pearnly_erp_portal.collection import mrerp as ledger

        self.assertEqual(guide.service.reading_text("CO-1"), "Reading from Express")
        with mock.patch.object(ledger, "handles", return_value=True):
            self.assertEqual(guide.service.reading_text("CO-1"), "Reading from MR.ERP")


if __name__ == "__main__":
    unittest.main()
