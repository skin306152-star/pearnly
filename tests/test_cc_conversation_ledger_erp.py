"""ERP 的 CC 只写本对话账套（A 线 11-12，D2：直写、代录、Cowork、ERP 一套）与写入只走计划的 ERP 推送入口（B 线下沉）。

- `target.pick(..., wanted)`：给了本对话账套就用名单里那一本，不按票据日期另判；不在这家名单里 → 没有账套；没电脑能写照旧。
- provider `masters.cc_projects.ledgers`：这家这个月覆盖的账套在前，恰好一本即默认，多本按记过的「这家这一年用哪套」，一本都不
  覆盖退最新一本可写的；不接 Express 的公司不用本对话账套（None）。
- `express.conversation.wanted`：只在 CC 对话 / 代录任务里、对话挂的就是这家时给。
- ERP 版 R2-5：本对话账套 T.8（2025，次年段到 2026-12-31）、同期 T.9、票据日期 2026-06 → 写进 T.8，不问。
- 「没有账套」不再算等电脑：不在会自己好的原因里（计划跳过写「没有账套」），网页的单仍每分钟再看（新年度账套报上来就接着写）。
- `push.send` 在 CC 对话里执行（直写，非代录）只推已开始计划里的单；代录交单、网页、每分钟重试不受影响。
- 票面合计在对话里只改数（`printed.set_total(release=False)`）。

替身同 test_erp_express_direct（共用通道的账套表用内存替身，写账规则真的）；对话与本对话账套由 ``self.pin`` 给（共用层另有测试）。
"""
import json
import sys
import types
import unittest
from unittest import mock

import test_erp_express_direct as base

fake, frappe, E, books, channel = base.fake, base.frappe, base.E, base.books, base.channel
from pearnly_erp_portal.express import choices, conversation, target  # noqa: E402

T7_PATH = r"S:\2567\EXP67\T7NIEX12"
T8_PATH = r"S:\2568\EXP68\T8NIEX12"
T9_PATH = r"C:\PearnlyExpress\70EXP\T9NIEX12"
COMPANY = dict(base.COMPANY)  # CO-00001，税号 OWN，分店 00012


def _utils(case):
    """写账模块从 frappe.utils 导入（同 test_erp_express_direct.PushModule）。"""
    utils = types.ModuleType("frappe.utils")
    utils.now_datetime = utils.nowdate = lambda: "2026-10-24"
    patcher = mock.patch.dict(sys.modules, {"frappe.utils": utils})
    patcher.start()
    case.addCleanup(patcher.stop)


def year(path, uid, start, end, next_end=None, writable=True):
    row = base.book(path=path, uid=uid, start=start, end=end)
    row.update(next_end=next_end, writable=writable)
    return row


class _Books(base.Base):
    """R2-5 的三本：T.7（2024）、T.8 与 T.9（都是 2025，次年段到 2026-12-31）。"""

    def setUp(self):
        super().setUp()
        self.t7, self.t8, self.t9 = self.computer([
            year(T7_PATH, "vsn:7/7", "2024-01-01", "2024-12-31", "2025-12-31"),
            year(T8_PATH, "vsn:8/8", "2025-01-01", "2025-12-31", "2026-12-31"),
            year(T9_PATH, "vsn:9/9", "2025-01-01", "2025-12-31", "2026-12-31")])
        fake.STATE.db.table("ERP Company")[COMPANY["name"]] = {**COMPANY, "express_enabled": 1}


class _Conversation(_Books):
    """CC 对话里执行（直写对话挂着 CO-00001），本对话账套由 ``self.pin`` 给。"""

    def setUp(self):
        super().setUp()
        self.pin = {"status": "set", "id": self.t8, "label": "T.8", "options": []}
        self.filed = COMPANY["name"]
        frappe.local.pearnly_assistant = {"session_id": "sesn_1"}
        self.addCleanup(setattr, frappe.local, "pearnly_assistant", None)
        patcher = mock.patch.object(conversation, "_conversation_of",
                                    lambda task: (self.filed, self.pin) if conversation.session() or task else ("", None))
        patcher.start()
        self.addCleanup(patcher.stop)


class PickTests(_Books):
    def test_the_wanted_book_is_used_whichever_book_the_date_would_pick(self):
        # 本年、次年段里的日期都写进这本，不按日期另选同期的 T.9（期间外的见下一条：Express 会拒，不写）
        for day in ("2026-06-15", "2025-03-01", "2026-12-31", "2025-01-01"):
            picked = target.pick(COMPANY, day, self.t8)
            self.assertEqual((picked["book"]["key"], picked["reason"]), (self.t8, ""), day)
        # 没给：同期两本照旧停下要人选一次（网页 / LINE 的现状不动）
        self.assertEqual(target.pick(COMPANY, "2026-06-15")["reason"], E.SEVERAL_BOOKS)

    def test_a_wanted_book_not_of_this_company_is_no_book_and_undecided_is_ledger_needed(self):
        picked = target.pick(COMPANY, "2026-06-15", "not-a-book-of-this-company")
        self.assertEqual((picked["book"], picked["reason"]), (None, E.NO_BOOK))
        picked = target.pick(COMPANY, "2026-06-15", "")
        self.assertEqual((picked["book"], picked["reason"]), (None, conversation.LEDGER_NEEDED))

    def test_a_wanted_book_no_computer_can_write_stops_as_before(self):
        self.computer([year(T7_PATH, "vsn:7/7", "2024-01-01", "2024-12-31", "2025-12-31", writable=False),
                       year(T8_PATH, "vsn:8/8", "2025-01-01", "2025-12-31", "2026-12-31")])
        picked = target.pick(COMPANY, "2025-06-15", self.t7)  # T.7 的次年段（期间内）
        self.assertEqual((picked["book"]["key"], picked["reason"]), (self.t7, E.NO_WRITER))

    def test_a_wanted_book_whose_fiscal_period_misses_the_date_is_not_written(self):
        """Express 原生录单拒期间外的日期（Message#70 วันที่ไม่อยู่ในงวด）：本对话账套 T.8（2025、次年段到 2026-12-31），票据日期
        2027-01-05 → 不写、原因期间不符（同 Cowork `account_match.conflict`）；次年段里的 2026-06-09、本年的照写。不另判别的账套。"""
        for day in ("2027-01-05", "2024-12-31"):
            picked = target.pick(COMPANY, day, self.t8)
            self.assertEqual((picked["book"]["key"], picked["reason"]), (self.t8, E.PERIOD_OUTSIDE), day)
        for day in ("2026-06-09", "2025-03-01", "2026-12-31"):
            self.assertEqual(target.pick(COMPANY, day, self.t8)["reason"], "", day)
        # 期间外的日期不会自己变：不进每分钟那一轮、不算等电脑
        self.assertNotIn(E.PERIOD_OUTSIDE, E.POLL_REASONS)
        self.assertNotIn(E.PERIOD_OUTSIDE, E.RETRY_REASONS)

    def test_a_wanted_book_without_a_reported_period_is_not_held(self):
        row = base.book(path=T8_PATH, uid="vsn:8/8")
        row.update(period_start=None, period_end=None)
        key, = self.computer([row])
        self.assertEqual(target.pick(COMPANY, "2031-01-05", key)["reason"], "")

    def test_current_reads_the_wanted_book(self):
        self.assertEqual(target.current(COMPANY, "2026-06-15", self.t9)["key"], self.t9)
        self.assertIsNone(target.current(COMPANY, "2026-06-15", "nope"))


class LedgersTests(_Books):
    """provider：本对话账套可选的几本与默认（同 Cowork `companies.provider.ledgers` 的形状）。"""

    def setUp(self):
        super().setUp()
        _utils(self)
        from pearnly_erp_portal.masters import cc_projects
        self.projects = cc_projects

    def ledgers(self, period):
        return self.projects.ledgers(COMPANY["name"], period, "", "staff@x")

    def test_two_books_covering_the_month_have_no_default_unless_remembered(self):
        found = self.ledgers("2025-06")
        self.assertEqual(found["default"], "")
        self.assertEqual([(item["id"], item["covers"]) for item in found["options"]],
                         [(self.t8, True), (self.t9, True), (self.t7, False)])
        self.assertEqual(found["options"][0]["label"], target.option_name(books.find(self.t8)))
        # 记过「这家这一年用哪套」（同网页选一次）：就是它
        with mock.patch.object(choices, "chosen", lambda company, kind, subject: self.t9
                               if (kind, subject) == (choices.ACCOUNT_SET, "year:2025-01-01") else ""):
            self.assertEqual(self.ledgers("2025-06")["default"], self.t9)

    def test_one_book_covering_the_month_is_the_default(self):
        found = self.ledgers("2024-06")
        self.assertEqual(found["default"], self.t7)
        self.assertEqual([item["covers"] for item in found["options"]], [True, False, False])

    def test_no_book_covering_the_month_falls_back_to_the_newest_writable(self):
        found = self.ledgers("2031-01")
        self.assertIn(found["default"], (self.t8, self.t9))
        self.assertTrue(all(not item["covers"] for item in found["options"]))
        self.assertEqual(len(found["options"]), 3)

    def _template_and_test(self):
        # 11-28 真机：云端 Z1（TEST）与 Express 空白模板（DAT）同税号同分店同年度，这个月都不覆盖时默认落到了 DAT
        def book(key, path, label):
            return {"key": key, "path": path, "label": label, "writable": 1,
                    "period_start": "2021-01-01", "period_end": "2021-12-31"}
        return [book("k-dat", "D:\\W\\ExpressI-Cloud\\DAT", "Z.blank"), book("k-test", "D:\\W\\ExpressI-Cloud\\TEST", "Z1.test")]

    def test_the_express_blank_template_is_never_the_default(self):
        with mock.patch.object(books, "for_company", lambda *a, **k: self._template_and_test()):
            found = self.ledgers("2026-11")
        self.assertEqual(found["default"], "k-test")
        self.assertEqual({item["id"] for item in found["options"]}, {"k-dat", "k-test"})   # 仍列出，只是不当默认

    def test_a_ledger_picked_once_becomes_the_default_when_no_book_covers_the_month(self):
        saved = {}
        with mock.patch.object(books, "for_company", lambda *a, **k: self._template_and_test()), \
                mock.patch.object(choices, "remember", lambda company, kind, subject, value, label="":
                                  saved.update({(kind, subject): value})), \
                mock.patch.object(choices, "chosen", lambda company, kind, subject: saved.get((kind, subject), "")):
            self.projects.remember_ledger(COMPANY["name"], "2026-11", "k-dat")
            self.assertEqual(saved, {(choices.ACCOUNT_SET, self.projects.FALLBACK_SUBJECT): "k-dat"})
            self.assertEqual(self.ledgers("2026-11")["default"], "k-dat")   # 人选的就是它（问一次记住）
            self.projects.remember_ledger(COMPANY["name"], "2026-11", "not-this-companys")
            self.assertEqual(self.ledgers("2026-11")["default"], "k-dat")

    def test_no_month_uses_this_month(self):
        with mock.patch.object(self.projects, "_this_month", lambda: "2024-06"):
            self.assertEqual(self.ledgers("")["default"], self.t7)

    def test_a_company_not_on_express_has_no_conversation_account_set(self):
        fake.STATE.db.table("ERP Company")[COMPANY["name"]]["express_enabled"] = 0
        self.assertIsNone(self.ledgers("2025-06"))
        fake.STATE.db.table("ERP Company")[COMPANY["name"]]["express_enabled"] = 1
        self.assertTrue(self.projects.LEDGER)


class WantedTests(_Conversation):
    def test_only_in_a_conversation_filed_under_this_company(self):
        self.assertEqual(conversation.wanted(COMPANY), self.t8)
        self.pin = {"status": "needed", "id": "", "label": "", "options": []}
        self.assertEqual(conversation.wanted(COMPANY), "")
        self.filed = "CO-OTHER"
        self.assertIsNone(conversation.wanted(COMPANY))
        self.filed = COMPANY["name"]
        frappe.local.pearnly_assistant = None
        self.assertIsNone(conversation.wanted(COMPANY))  # 网页 / LINE / 每分钟重试：照日期

    def test_an_operator_task_uses_its_conversation(self):
        frappe.local.pearnly_assistant = None
        with conversation.for_task("TK1"):
            self.assertEqual(conversation.wanted(COMPANY), self.t8)
        self.assertIsNone(conversation.wanted(COMPANY))


    def test_a_background_write_carried_from_a_guide_confirm_uses_that_conversation(self):
        # 引导「确定」时提交、排进后台的那件（`push.queue` 带来的对话）：后台没有请求上下文，照样按那段对话的本对话账套
        frappe.local.pearnly_assistant = None
        with conversation.for_session("sesn_1"):
            self.assertEqual(conversation.session(), "sesn_1")
            self.assertEqual(conversation.wanted(COMPANY), self.t8)
        self.assertEqual(conversation.session(), "")
        self.assertIsNone(conversation.wanted(COMPANY))


class R2_5ReplayTests(_Conversation):
    """ERP 版 R2-5：本对话账套 T.8、同期 T.9、票据日期落在 T.8 的次年段（2026-06）→ 写进 T.8，不问哪套。"""

    def test_the_document_goes_into_the_conversation_account_set(self):
        self.masters(self.t8)
        result = self.prepare(base.doc(date="2026-06-15"))
        self.assertNotIn("hold", result, result)
        self.assertEqual(result["book"]["key"], self.t8)
        self.assertEqual(result["payload"]["account_set"], T8_PATH)

    def test_outside_the_conversation_it_still_asks_once_between_the_two(self):
        frappe.local.pearnly_assistant = None
        result = self.prepare(base.doc(date="2026-06-15"))
        self.assertEqual(result["hold"], E.CHOICE + ":" + choices.ACCOUNT_SET)

    def test_a_date_outside_the_conversation_account_sets_period_holds_on_the_period(self):
        self.masters(self.t8)
        result = self.prepare(base.doc(date="2027-01-05"))
        self.assertEqual((result.get("hold"), result["book"]["key"]), (E.PERIOD_OUTSIDE, self.t8))
        self.assertNotIn("payload", result)

    def test_an_undecided_conversation_account_set_holds_on_ledger_needed(self):
        self.pin = {"status": "needed", "id": "", "label": "", "options": []}
        self.assertEqual(self.prepare(base.doc(date="2026-06-15"))["hold"], conversation.LEDGER_NEEDED)

    def test_the_stock_entry_and_the_search_use_it_too(self):
        _utils(self)
        from pearnly_erp_portal.express import precheck, stock_entry
        body = {"date": "2026-06-15", "lines": [{"product": "P1"}]}
        result = stock_entry.prepare(body, COMPANY, lambda company, chosen: self.reads.append(chosen["key"]))
        self.assertEqual(result.get("hold"), E.READING)
        self.assertEqual(self.reads, [self.t8])  # 读的是 T.8 的主档，不是停在「同期两份」
        fake.STATE.db.table("Purchase Document")["PUR-1"] = {"name": "PUR-1", "company": COMPANY["name"],
                                                              "ledger_book": self.t9, "doc_date": "2026-06-15"}
        with mock.patch.object(precheck.push, "_company", lambda name: COMPANY):
            # 记过的 ledger_book（网页按日期选的 T.9）：对话里以本对话账套为准
            self.assertEqual(precheck._book_of("Purchase Document", "PUR-1", COMPANY["name"]), self.t8)
            frappe.local.pearnly_assistant = None
            self.assertEqual(precheck._book_of("Purchase Document", "PUR-1", COMPANY["name"]), self.t9)


class NoBookTests(unittest.TestCase):
    """A9：「没有账套」不是等电脑。计划与判定如实报「没有账套」，不再永远等电脑；网页的单仍每分钟再看。"""

    def test_no_book_is_not_a_reason_that_fixes_itself(self):
        self.assertNotIn(E.NO_BOOK, E.RETRY_REASONS)
        self.assertTrue(E.needs_person(E.HELD, E.NO_BOOK))
        self.assertIn(E.NO_BOOK, E.POLL_REASONS)  # 网页 / LINE 的单：电脑报了新年度账套就接着写
        for reason in E.RETRY_REASONS:
            self.assertIn(reason, E.POLL_REASONS)

    def test_the_precheck_reports_no_account_set_and_ledger_needed_as_not_writable(self):
        _utils(self)
        from pearnly_core.write_precheck import contracts as card
        from pearnly_erp_portal.express import precheck
        self.assertEqual(precheck._state({"hold": E.NO_BOOK}), (card.NOT_WRITABLE, precheck.NO_ACCOUNT_SET))
        self.assertEqual(precheck.NO_ACCOUNT_SET, "no_account_set")
        self.assertEqual(precheck._state({"hold": conversation.LEDGER_NEEDED}),
                         (card.NOT_WRITABLE, conversation.LEDGER_NEEDED))
        self.assertEqual(precheck._state({"hold": E.NO_WRITER}), (card.WAITING, E.NO_WRITER))
        # 期间不符：判定 / 计划如实报（同 Cowork 推送的原因码），不归等电脑
        self.assertEqual(precheck._state({"hold": E.PERIOD_OUTSIDE}), (card.NOT_WRITABLE, precheck.PERIOD_MISMATCH))
        self.assertEqual(precheck.PERIOD_MISMATCH, "account_period_mismatch")
        from pearnly_core.assistant import write_plan
        self.assertEqual(write_plan.SKIP_PERIOD_MISMATCH, precheck.PERIOD_MISMATCH)

    def test_the_period_reason_has_words_in_four_languages(self):
        from pearnly_erp_portal.express import pending
        key = pending.REASONS[E.PERIOD_OUTSIDE]
        rows = [line.split("\t") for line in (base.ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/i18n/messages.tsv")
                .read_text(encoding="utf-8").splitlines()]
        words = next((row[:4] for row in rows if len(row) >= 5 and row[4] == key), None)
        self.assertTrue(words and all(words), key)


class PlanGateTests(base.PushModule):
    """写入只走计划下沉到 ERP 推送入口：`push.send` 在 CC 对话里执行（直写）要 `write_plan.permit` 放行那张单。"""

    LINKED = "2026-10-20 00:00:00"

    def setUp(self):
        super().setUp()
        base.LinkStates.link_fixture(self)
        fake.STATE.db.table("ERP Company")["CO-1"].update(express_enabled=1, express_linked_at=self.LINKED)
        db = fake.STATE.db
        db.table("Purchase Document")["PUR-1"] = {"name": "PUR-1", "status": "submitted", "doc_kind": "purchase_invoice",
                                                  "company": "CO-1", "creation": "2026-10-21 09:00:00",
                                                  "ledger_state": E.QUEUED, "owner": "staff@x"}
        db.table("ERP Stock Entry")["SE-1"] = {"name": "SE-1", "status": "submitted", "direction": "in",
                                               "warehouse": "W1", "creation": "2026-10-21 09:00:00",
                                               "ledger_state": E.QUEUED, "owner": "staff@x"}
        self.permitted = set()
        self.asked = []
        from pearnly_core.assistant import write_plan

        def permit(ids):
            self.asked.append(list(ids))
            return [i for i in ids if i in self.permitted], {i: "not_started" for i in ids if i not in self.permitted}

        patcher = mock.patch.object(write_plan, "permit", permit)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.prepared = []
        patcher = mock.patch.object(self.push, "_prepare_body", lambda doctype, company, body: self.prepared.append(
            doctype) or ({"hold": E.READING, "book": {}}, None, None))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(self.push, "document", lambda doctype, name: {"ref": name, "number": name,
                                                                                  "lines": [1]})
        patcher.start()
        self.addCleanup(patcher.stop)
        frappe.local.pearnly_assistant = {"session_id": "sesn_1"}
        self.addCleanup(setattr, frappe.local, "pearnly_assistant", None)

    def test_a_direct_push_in_a_conversation_without_a_started_plan_is_refused(self):
        out = self.push.send("Purchase Document", "PUR-1")
        self.assertEqual((out["status"], out["reason"]), ("not_pushed", "not_started"))
        self.assertEqual(self.prepared, [])
        self.assertEqual(fake.STATE.db.table("Purchase Document")["PUR-1"]["ledger_state"], E.QUEUED)  # 单据不动
        self.permitted.add("P:PUR-1")
        self.assertEqual(self.push.send("Purchase Document", "PUR-1")["status"], "held")
        self.assertEqual(self.prepared, ["Purchase Document"])

    def test_a_stock_entry_is_found_by_either_of_its_document_ids(self):
        self.permitted.add("M:SE-1")  # erp_list_documents 给的写法；判定卡是 T:
        self.assertNotEqual(self.push.send("ERP Stock Entry", "SE-1")["status"], "not_pushed")
        self.assertIn("T:SE-1", self.asked[-1])

    def test_operator_hand_in_the_web_and_the_minute_retry_are_not_gated(self):
        self.assertNotEqual(self.push.send("Purchase Document", "PUR-1", operator={"id": "b1", "task": "TK1"})["status"],
                            "not_pushed")
        frappe.local.pearnly_assistant = None
        fake.STATE.db.table("Purchase Document")["PUR-1"].update(ledger_state=E.QUEUED, ledger_reason="")
        self.assertNotEqual(self.push.send("Purchase Document", "PUR-1")["status"], "not_pushed")
        self.assertEqual(self.asked, [])

    def test_an_operator_hand_in_prepares_with_the_tasks_conversation_account_set(self):
        seen = []
        with mock.patch.object(self.push, "_prepare_body", lambda doctype, company, body: seen.append(
                conversation._TASK.get()) or ({"hold": E.READING, "book": {}}, None, None)):
            frappe.local.pearnly_assistant = None
            self.push.send("Purchase Document", "PUR-1", operator={"id": "b1", "task": "TK1"})
        self.assertEqual(seen, ["TK1"])
        self.assertEqual(conversation._TASK.get(), "")


    def test_an_operator_hand_in_prepares_again_a_document_the_web_left_on_a_choice(self):
        # 测试机1 11-12 按类查全（ERP 代录交单）：网页按日期停在「同期两本选哪本」（held、choice_needed:account_set、没写账件）的单，
        # 代录交单照样按任务会话的本对话账套重新准备，不因网页那一问停下
        fake.STATE.db.table("Purchase Document")["PUR-1"].update(
            ledger_state=E.HELD, ledger_reason="choice_needed:account_set", ledger_job=None, ledger_book=None,
            ledger_needed='[{"kind": "account_set", "subject": "year:2025-01-01"}]')
        seen = []
        with mock.patch.object(self.push, "_prepare_body", lambda doctype, company, body: seen.append(
                conversation._TASK.get()) or ({"hold": E.READING, "book": {}}, None, None)):
            frappe.local.pearnly_assistant = None
            self.push.send("Purchase Document", "PUR-1", operator={"id": "b1", "task": "TK1"})
        self.assertEqual(seen, ["TK1"])

    def test_a_document_submitted_from_a_guided_intake_passes_by_the_intake_record_in_the_plan(self):
        # 引导「确定」的写入计划记的是收单记录 ``I:<记录>``；提交出的采购单按它认
        fake.STATE.db.table("ERP LINE Intake")["INT-1"] = {"name": "INT-1", "source": "cc", "purchase": "PUR-1"}
        self.permitted.add("I:INT-1")
        self.assertNotEqual(self.push.send("Purchase Document", "PUR-1")["status"], "not_pushed")
        self.assertIn("I:INT-1", self.asked[-1])

    def test_queue_carries_the_guide_conversation_into_the_background_send(self):
        queued = []
        with mock.patch.object(frappe, "enqueue", lambda method, **kwargs: queued.append(kwargs), create=True), \
                mock.patch.object(self.push, "_eligible", lambda doctype, doc: True):
            fake.STATE.db.table("Purchase Document")["PUR-1"].update(ledger_state="")
            with conversation.for_session("sesn_1"):
                self.push.queue("Purchase Document", "PUR-1")
            fake.STATE.db.table("Purchase Document")["PUR-1"].update(ledger_state="")
            self.push.queue("Purchase Document", "PUR-1")  # 网页 / LINE / 代录开录时提交的：不带
        self.assertEqual([item.get("session") for item in queued], ["sesn_1", None])
        seen = []
        frappe.local.pearnly_assistant = None
        self.permitted.add("P:PUR-1")
        with mock.patch.object(self.push, "_prepare_body", lambda doctype, company, body: seen.append(
                conversation.session()) or ({"hold": E.READING, "book": {}}, None, None)):
            fake.STATE.db.table("Purchase Document")["PUR-1"].update(ledger_state=E.QUEUED)
            self.push.send("Purchase Document", "PUR-1", session="sesn_1")
        self.assertEqual(seen, ["sesn_1"])          # 后台按那段对话准备（本对话账套）
        self.assertIn("P:PUR-1", self.asked[-1])  # 也过写入计划的门
        self.assertEqual(conversation.session(), "")


class PrintedTotalTests(base.Base):
    """B3：对话里改票面合计只改数，不当场推（推送交回计划）；网页照旧当场重新写账。"""

    def setUp(self):
        super().setUp()
        _utils(self)
        fake.STATE.db.table("Purchase Document")["PUR-1"] = {
            "name": "PUR-1", "status": "submitted", "printed_total": 0, "ledger_state": E.HELD,
            "ledger_reason": E.PRINTED_TOTAL_MISSING, "ledger_job": None}
        self.sent = []
        from pearnly_erp_portal.express import printed, push
        self.printed = printed
        for target_, name, value in ((push, "send", lambda doctype, name, operator=None: self.sent.append(name)),
                                     (frappe.db, "commit", lambda: None), (frappe, "as_json", json.dumps),
                                     (frappe, "get_doc", lambda values, *a, **k: types.SimpleNamespace(
                                         insert=lambda **kw: None))):  # 版本记录、改动依据不在这里测
            patcher = mock.patch.object(target_, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_release_false_only_changes_the_number(self):
        out = self.printed.set_total("Purchase Document", "PUR-1", "107.00", basis="page 1", by="cc", release=False)
        row = fake.STATE.db.table("Purchase Document")["PUR-1"]
        self.assertEqual((out["released"], self.sent, row["printed_total"]), (False, [], 107.0))
        self.assertEqual((row["ledger_state"], row["ledger_reason"]), (E.HELD, E.PRINTED_TOTAL_MISSING))
        out = self.printed.set_total("Purchase Document", "PUR-1", "107.00", basis="page 1")
        self.assertEqual((out["released"], self.sent), (True, ["PUR-1"]))


if __name__ == "__main__":
    unittest.main()
