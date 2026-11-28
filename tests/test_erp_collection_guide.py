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



def _codes(found):
    """拒收结果里的码（`run._refusal`：{key: {code, text}}）。"""
    return {key: value["code"] for key, value in found["refused"].items()}

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


def _deadlock_first(times=1):
    """放行第一把锁（本笔用到的快照行，`snapshot.lock`）先撞 ``times`` 次死锁（实验栈 1213），之后照常。"""
    left, real = [times], T.service.snapshot.lock

    def lock(book_key, docnums):
        if left[0]:
            left[0] -= 1
            raise frappe.QueryDeadlockError("1213 Deadlock found when trying to get lock")
        return real(book_key, docnums)
    return mock.patch.object(T.service.snapshot, "lock", side_effect=lock)


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

    def test_a_deadlock_on_release_is_retried_whole_and_queues_one_job(self):
        name, found = self.to_review()
        with _deadlock_first(), mock.patch("pearnly_core.transaction.conflicts.time.sleep"):
            done = run.confirm(name, found["version"])
        self.assertEqual((done["status"], done["result"]["status"]), ("done", "queued"))
        self.assertEqual(len(self.jobs.rows), 1)

    def test_the_written_result_shows_the_re_number(self):
        name, found = self.to_review()
        run.confirm(name, found["version"])
        draft, _version = guide._draft_name(self.runs[name].draft)
        self.jobs.report("J-" + draft, "success", docnum="RE6900012")
        result = run.view(self.runs[name])["result"]
        self.assertEqual((result["status"], result["docnum"]), ("entered", "RE6900012"))
        self.assertNotIn("wait", result)


    def test_a_result_unknown_is_its_own_row_with_the_check_sentence(self):
        """主会话 11-25：CC 桌面版写到一半断了，结果卡「结果不明」+ 先在 Express 核对那句，不算失败。"""
        name, found = self.to_review()
        run.confirm(name, found["version"])
        draft, _version = guide._draft_name(self.runs[name].draft)
        self.jobs.report("J-" + draft, "manual", reason="cc_desktop.result_unknown")
        result = run.view(self.runs[name])["result"]
        self.assertEqual((result["status"], result["reason_text"]), ("unconfirmed", "express.reason.resultUnknown"))


class Checks(Base):
    def test_a_customer_code_differing_only_in_blank_space_is_not_the_customer(self):
        name = run.start(SESSION, "collection")["run"]
        refused = run.answer(name, {"customer": "2100883 "})
        self.assertEqual(_codes(refused), {"customer": "not offered"})

    def test_an_amount_over_what_can_be_collected_now_is_refused(self):
        name, _found = self.to_invoices()
        self.read_back()
        refused = run.answer(name, {"invoices": [{"code": "IV2", "amount": "500.01"}]})
        self.assertEqual(_codes(refused), {"invoices": "over max"})

    def test_the_methods_must_add_up_to_the_invoices(self):
        name, _found = self.to_invoices()
        self.read_back()
        run.answer(name, {"invoices": IV1_600})
        refused = run.answer(name, {"methods": [{"code": guide.CASH, "amount": "500"}]})
        self.assertEqual(_codes(refused), {"methods": C.AMOUNT_MISMATCH})

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
        self.assertEqual(_codes(run.answer(name, {"voucher": ["upl_1", "upl_2"]})), {"voucher": C.VOUCHER_MISSING})
        self.attached["upl_1"] = b"%PDF-1.4 slip"
        reviewed = run.answer(name, {"voucher": ["upl_1"]})
        self.assertEqual(reviewed["status"], "reviewing")
        draft, _version = guide._draft_name(self.runs[name].draft)
        doc = frappe.get_doc("ERP Collection", draft)
        self.assertTrue(doc.payments[0].voucher)


class Offsets(Base):
    """退货 / 减项进收款（11-28）：抵减单列在发票下面、带类型与 ``negative``；收款方式那张卡的应分 = 发票 − 抵减单（产品给
    ``total``，引擎照它出）；合计核对按同一口径；复核卡上抵减单带负号。"""

    ROWS = [
        {"docnum": "IV1", "rectyp": "3", "docdat": "2026-10-01", "customer": "2100883", "salesman": "S1",
         "department": "CPI", "net": "1000.00", "received": "0.00", "remaining": "1000.00"},
        {"docnum": "SR1", "rectyp": "5", "docdat": "2026-09-01", "customer": "2100883", "net": "300.00",
         "received": "0.00", "remaining": "300.00"}]

    def test_a_credit_note_is_listed_last_and_comes_off_what_the_methods_must_split(self):
        name, _found = self.to_invoices()
        self.read_back(self.ROWS)
        question = self.questions(run.view(self.runs[name]))["invoices"]
        self.assertEqual([(row["code"], row.get("negative", False)) for row in question["options"]],
                         [("IV1", False), ("SR1", True)])
        self.assertEqual(question["options"][1]["note"], "Credit Note · 2026-09-01")
        found = run.answer(name, {"invoices": [{"code": "IV1", "amount": "600"}, {"code": "SR1", "amount": "300"}]})
        self.assertEqual(self.questions(found)["methods"]["total"], "300.00")
        refused = run.answer(name, {"methods": [{"code": guide.CASH, "amount": "600"}]})
        self.assertEqual(_codes(refused), {"methods": C.AMOUNT_MISMATCH})
        found = run.answer(name, {"methods": [{"code": guide.CASH, "amount": "300"}]})
        rows = found["review"]["tables"][0]["rows"]
        self.assertEqual([(row["code"], row["amount"]) for row in rows], [("IV1", "600.00"), ("SR1", "-300.00")])
        self.assertEqual(found["review"]["total"], "300.00")


class FullOffset(Base):
    """抵减单正好抵平发票（净额 0，原生 29 张 RE）：收款方式那张卡应分 0.00，交空的分摊就算答了，进复核、草稿没有方式；
    应分不是 0 时空的分摊照旧不收；只勾抵减单照旧退回发票那一步。"""

    ROWS = [{"docnum": "IV1", "rectyp": "3", "docdat": "2026-10-01", "customer": "2100883", "salesman": "S1",
             "department": "CPI", "net": "1000.00", "received": "0.00", "remaining": "1000.00"},
            {"docnum": "AI1", "rectyp": "0", "docdat": "2026-09-01", "customer": "2100883", "net": "300.00",
             "received": "0.00", "remaining": "300.00"}]

    def test_an_empty_split_settles_a_full_offset_only(self):
        name, _found = self.to_invoices()
        self.read_back(self.ROWS)
        run.view(self.runs[name])  # 出卡时落下读回（同页面）
        found = run.answer(name, {"invoices": [{"code": "IV1", "amount": "300"}, {"code": "AI1", "amount": "300"}]})
        self.assertEqual(self.questions(found)["methods"]["total"], "0.00")
        found = run.answer(name, {"methods": []})
        self.assertEqual(found["status"], "reviewing")
        self.assertEqual((found["review"]["total"], found["review"]["ready"]), ("0.00", True))
        self.assertEqual(found["review"]["tables"][1]["rows"], [])
        draft, _version = guide._draft_name(self.runs[name].draft)
        self.assertEqual((str(frappe.db.get_value("ERP Collection", draft, "total")), self.runs[name].status),
                         ("0.00", "reviewing"))

    def test_offsets_above_the_invoices_pay_the_difference_back_in_cash(self):
        # 原生 69EXP 测试账套 TEST 的 RE0000034（11-28）：应分为负，交空的分摊，草稿现金记负数
        name, _found = self.to_invoices()
        self.read_back(self.ROWS)
        run.view(self.runs[name])
        found = run.answer(name, {"invoices": [{"code": "IV1", "amount": "100"}, {"code": "AI1", "amount": "300"}]})
        self.assertEqual(self.questions(found)["methods"]["total"], "-200.00")
        found = run.answer(name, {"methods": []})
        self.assertEqual(found["status"], "reviewing")
        self.assertEqual((found["review"]["total"], found["review"]["ready"]), ("-200.00", True))
        self.assertEqual([(row["code"], row["amount"]) for row in found["review"]["tables"][1]["rows"]], [(guide.CASH, "-200.00")])
        refused = run.answer(name, {"methods": [{"code": guide.CASH, "amount": "200"}]})
        self.assertIn("methods", _codes(refused))

    def test_an_empty_split_is_refused_when_money_is_due(self):
        name, _found = self.to_invoices()
        self.read_back(self.ROWS)
        run.view(self.runs[name])
        run.answer(name, {"invoices": [{"code": "IV1", "amount": "600"}, {"code": "AI1", "amount": "300"}]})
        refused = run.answer(name, {"methods": []})
        self.assertIn("methods", _codes(refused))


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


    def test_reopening_a_step_from_review_withdraws_the_old_draft_too(self):
        # A2 11-19 实验栈：复核卡上点「改」回去重问、再到复核，第一次复核建的草稿没作废，收款列表里多一笔孤儿草稿
        name, found = self.to_review()
        old, _version = guide._draft_name(self.runs[name].draft)
        back = run.reopen(name, "invoices", found["version"])
        self.assertEqual(back["status"], "asking")
        run.answer(name, {"invoices": [{"code": "IV1", "amount": "700"}]})
        again = run.answer(name, {"methods": [{"code": guide.CASH, "amount": "700"}]})
        self.assertEqual(again["status"], "reviewing")
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
            self.assertEqual(_codes(run.answer(name, {"day": "2027-01-02"})), {"day": T.service.DAY_NEEDED})
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

    METHODS = {"cash": True, "transfers": [{"prefix": "1112-01", "code": "1112-01", "name": "KBANK", "label": "KBANK 1234"}],
               "discount": {"prefix": "5300-09", "code": "5300-09", "name": "Discount", "label": "Discount"}}

    def test_the_method_card_takes_one_row_on_mrerp_and_discount_is_free(self):
        ctx = {"answers": {"company": "CO-1", "invoices": IV1_600}}
        with mock.patch.object(guide.service, "methods", return_value=dict(self.METHODS)):
            found = guide.method_options(ctx)
        self.assertNotIn("max_rows", found)  # Express：照旧不限
        self.assertFalse(any(row.get("free") for row in found["options"]))
        with mock.patch.object(guide.service, "methods", return_value={**self.METHODS, "single": True}):
            found = guide.method_options(ctx)
            by_code = guide.method_options(ctx, codes=[guide.CASH, guide.DISCOUNT + "5300-09"])  # 引擎作答时按码取也带上
        self.assertEqual(found["max_rows"], 1)
        self.assertEqual([row["code"] for row in found["options"] if row.get("free")], [guide.DISCOUNT + "5300-09"])
        self.assertEqual(by_code["max_rows"], 1)

    def test_the_card_refuses_a_second_money_row_on_mrerp(self):
        name, _found = self.to_invoices()
        self.read_back()
        run.answer(name, {"invoices": IV1_600})
        with mock.patch.object(guide.service, "methods", return_value={**self.METHODS, "single": True}):
            refused = run.answer(name, {"methods": self.TWO})
            self.assertEqual(_codes(refused), {"methods": run.TOO_MANY_ROWS})
            self.assertEqual(self.questions(refused)["methods"]["max_rows"], 1)

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
