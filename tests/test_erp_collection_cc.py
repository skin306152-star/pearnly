"""收款第 4 步：ERP 版 CC 对话里录收款（`collection.cc_tools`、写入计划里的收款 `collection.plan`）。

不连库、不出网：沿用第 2 步的 Frappe 替身（`test_erp_collection`：内存表、NLTTEST 收款类型、快照里 2100883 两张发票
IV1 1000 / IV2 500）。写入计划用共用层真实现（`pearnly_core.assistant.write_plan`、`write_precheck.contracts` / `products`），
只把「本对话的用户消息序号 / 最近一次停止」（`assistant.sessions`）、对话附件（`assistant.files`）换成替身；判定钩子指向
`collection.plan`（站点上经 `express.precheck.check` 转来，源码断言见最后一组）。

覆盖：工具声明（命名、步骤名四语、hooks、只在直写对话）；建草稿 → 写入计划 → 用户下一句同意 → 开始 → 放行（同意 = 用户消息
序号）；没有新用户消息不开始；复述后改过不写（开始前改、开始后改）；停止后不开始；没开始的计划不放行；代录会话里没有收款工具；
凭证从对话附件来；权限（没有收款岗位、别人的收款、别家公司）；撤回；结果查询；对话没挂公司。
"""
import importlib
import json
import pathlib
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_erp_collection as T  # noqa: E402  装好 Frappe 替身与第 2 步的数据

ROOT = T.ROOT
frappe = T.frappe
frappe.clear_messages = lambda: None
frappe.get_attr = lambda path: getattr(importlib.import_module(path.rpartition(".")[0]), path.rpartition(".")[2])
frappe.utils.get_system_timezone = lambda: "UTC"
frappe.local.lang = "zh"


def _db_set(self, values, update_modified=True):
    self.update(values)
    T.S.table(self.doctype)[self["name"]].update(values)


T.Doc.db_set = _db_set

import pearnly_core.assistant as assistant_package  # noqa: E402
from pearnly_core.assistant import write_plan  # noqa: E402
from pearnly_erp_portal.collection import cc_tools, plan, service  # noqa: E402

HOOKS = {"pearnly_write_precheck": {"allowed": "pearnly_erp_portal.collection.plan.allowed",
                                    "check": "pearnly_erp_portal.collection.plan.check",
                                    "remember": "pearnly_erp_portal.collection.plan.check"}}
SESSION = "sesn_1"


class Base(T.Base):
    def setUp(self):
        super().setUp()
        self.turn, self.stopped, self.attached = [0], [None], {}
        self.company = ["CO-1"]
        self.ledger = [self.ledger_of(T.BOOK)]
        self.express_open = [False]
        frappe.local.pearnly_assistant = {"session_id": SESSION, "may_wait": False}
        sessions = types.ModuleType("pearnly_core.assistant.sessions")
        sessions.user_turn = lambda sid: self.turn[0]
        sessions.last_stop = lambda sid: self.stopped[0]
        files = types.ModuleType("pearnly_core.assistant.files")

        def attachment(ref):
            if ref not in self.attached:
                frappe.throw("This attachment is not in this conversation.")
            return ref, self.attached[ref]

        files.attachment = attachment
        patches = [
            mock.patch.dict(sys.modules, {"pearnly_core.assistant.sessions": sessions,
                                          "pearnly_core.assistant.files": files}),
            mock.patch.object(assistant_package, "sessions", sessions, create=True),
            mock.patch.object(assistant_package, "files", files, create=True),
            mock.patch.object(frappe, "get_hooks", side_effect=lambda name: HOOKS.get(name, {})),
            mock.patch.object(cc_tools, "_company", side_effect=lambda: self.company[0]),
            # 本对话账套（共用层 `assistant.ledger`，A 线 11-12）：这段对话此刻的那一本
            mock.patch("pearnly_core.assistant.ledger.of_session", side_effect=lambda sid=None: self.ledger[0]),
            # 共用通道的「在等 Express 关闭」（express-wait-display 11-12 的约定，未合入前用替身）
            mock.patch("pearnly_core.cc_desktop.jobs.book_express_wait", create=True,
                       side_effect=lambda key, moment=None: {"waiting": self.express_open[0], "since": None}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(setattr, frappe.local, "pearnly_assistant", None)
        self.conversation("a@x")

    def conversation(self, user, project="CO-1"):
        T.S.table("Pearnly Assistant Session")["S-" + user] = {
            "name": "S-" + user, "session_id": SESSION, "user": user, "operator_task": "", "project": project,
            "period": "2026-11"}

    @staticmethod
    def ledger_of(book, options=()):
        listed = [{"id": item["key"], "label": item["label"], "covers": True} for item in (options or [book])]
        return {"status": "set", "id": book["key"], "label": book["label"], "options": listed}

    def call(self, fn, **tool_input):
        return json.loads(fn(tool_input))

    def make(self, amount="600", **extra):
        self.as_user(extra.pop("user", "a@x"))
        extra.setdefault("cash", amount)
        return self.call(cc_tools.draft, customer="2100883", invoices=[{"docnum": "IV1", "amount": amount}], **extra)

    def plan_of(self, document):
        return json.loads(write_plan.tool_plan({"documents": [document]}))

    def start(self, made_plan):
        return json.loads(write_plan.tool_start({"plan": made_plan}))

    def agreed(self, document):
        """计划 → 复述 → 用户回了一句 → 开始。"""
        made_plan = self.plan_of(document)["plan"]
        self.turn[0] += 1
        self.assertEqual(self.start(made_plan)["status"], "started")

    def record(self, name):
        return frappe.db.get_value("ERP Collection", name, ["status", "consent_ref", "draft_version"], as_dict=True)


class Declarations(unittest.TestCase):
    def test_tools_follow_the_template_and_every_step_has_four_languages(self):
        names = [tool["name"] for tool in cc_tools.TOOLS]
        self.assertEqual(names, list(cc_tools.STEP_NAMES))
        words = {}
        for line in (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/i18n/messages.tsv").read_text(
                encoding="utf-8").splitlines()[1:]:
            cells = line.split("\t")
            words.setdefault(cells[0], cells[1:4])
        for tool in cc_tools.TOOLS:
            self.assertTrue(tool["name"].startswith("erp_collection_"), tool["name"])
            self.assertEqual(tool.get("modes"), ["chat"], tool["name"])  # 只在直写对话（用户 11-11：代录不做收款）
            self.assertNotIn("approval", tool)  # 不加「允许」卡片：放行由写入计划 + 用户下一句定
            module, _dot, function = tool["handler"].rpartition(".")
            self.assertEqual(module, "pearnly_erp_portal.collection.cc_tools")
            self.assertTrue(callable(getattr(cc_tools, function)), tool["handler"])
            for text in (tool["label"], cc_tools.STEP_NAMES[tool["name"]]):
                self.assertEqual(len([cell for cell in words.get(text, []) if cell.strip()]), 3, text)
        hooks = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/hooks.py").read_text(encoding="utf-8")
        self.assertIn('"pearnly_erp_portal.collection.cc_tools.TOOLS"]', hooks)
        self.assertIn('"pearnly_erp_portal.collection.cc_tools.STEP_NAMES"]', hooks)

    def test_an_operator_conversation_has_no_collection_tool_and_a_direct_one_has_all_ten(self):
        # 用户 11-11：代录不做收款。各模式只用自己的工具：代录会话给模型的工具表里一个 erp_collection_* 都没有（共用层
        # `tools.mode_allowed`，同 `definition.brief` 按模式整组换上）
        from pearnly_core.assistant import tools

        with mock.patch.object(tools, "declared", lambda: list(cc_tools.TOOLS)):
            chat = [t["name"] for t in tools.declared() if tools.mode_allowed(t["name"], "chat")]
            operator = [t["name"] for t in tools.declared() if tools.mode_allowed(t["name"], "operator")]
        self.assertEqual(len(chat), 10)
        self.assertEqual(chat, [t["name"] for t in cc_tools.TOOLS])
        self.assertEqual([name for name in operator if name.startswith("erp_collection_")], [])

    def test_the_plan_document_carries_the_version(self):
        self.assertEqual(plan.WRITE, service.WRITE)
        self.assertEqual(plan.doc_id("COL261111-AB12", 3), "R:COL261111-AB12@3")
        self.assertEqual(plan.parse("R:COL261111-AB12@3"), ("COL261111-AB12", 3))
        for value in ("R:COL261111-AB12", "P:COL261111-AB12@3", "R:@3", "R:COL@x", ""):
            self.assertEqual(plan.parse(value), ("", None), value)

    def test_the_erp_precheck_hands_collection_ids_to_the_collection_plan(self):
        source = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/express/precheck.py").read_text(encoding="utf-8")
        self.assertIn("if _collection().allowed(user, company):", source)
        self.assertIn("collections.check(company, [doc_id for doc_id in doc_ids if collections.mine(doc_id)])", source)
        self.assertIn("from pearnly_erp_portal.collection import plan", source)


class Flow(Base):
    def test_draft_plan_answer_start_release(self):
        made = self.make("600")
        self.assertTrue(made["ready"])
        self.assertEqual(made["document"], plan.doc_id(made["name"], 1))
        self.assertIn("pearnly_write_plan", made["note"])
        self.assertEqual(self.record(made["name"]).status, "draft")
        planned = self.plan_of(made["document"])
        self.assertEqual((planned["status"], planned["count"], planned["documents"]), ("ready", 1, [made["document"]]))
        self.assertEqual(planned["account_sets"][0]["account_set_title"], made["book"])  # 复述「公司·账套」
        refused = self.call(cc_tools.push, document=made["document"])  # 还没开始：不放
        self.assertEqual((refused["status"], refused["reason"]), ("not_pushed", "not_started"))
        self.assertEqual(self.start(planned["plan"])["status"], "not_confirmed")  # 复述后用户还没回话
        self.turn[0] += 1  # 用户：写入
        started = self.start(planned["plan"])
        self.assertEqual((started["status"], started["documents"]), ("started", [made["document"]]))
        self.assertEqual(self.jobs.rows, {})  # 开始只标计划；放行由收款工具做
        pushed = self.call(cc_tools.push, document=made["document"])
        self.assertEqual(pushed["status"], "queued")
        self.assertIn("erp_collection_status", pushed["note"])
        self.assertNotIn("express_wait", pushed)
        row = self.record(made["name"])
        self.assertEqual((row.status, row.consent_ref), ("queued", f"cc:{SESSION}:1"))
        self.assertEqual(len(self.jobs.rows), 1)
        self.assertEqual(self.call(cc_tools.push, document=made["document"])["status"], "not_saved")  # 不重交
        self.assertEqual(len(self.jobs.rows), 1)

    def test_written_and_failed_results(self):
        made = self.make("600")
        self.agreed(made["document"])
        self.call(cc_tools.push, document=made["document"])
        self.jobs.report("J-" + made["name"], "success", docnum="RE6900012")
        status = self.call(cc_tools.status, collection=made["name"])
        self.assertEqual((status["result"], status["docnum"]), ("entered", "RE6900012"))
        listed = self.call(cc_tools.recent)
        self.assertEqual((listed["total"], listed["collections"][0]["name"]), (1, made["name"]))

    def test_a_change_after_it_was_said_back_is_not_released(self):
        made = self.make("600")
        planned = self.plan_of(made["document"])
        changed = self.call(cc_tools.draft, collection=made["name"], version=1,
                            invoices=[{"docnum": "IV1", "amount": "700"}], cash="700")  # 复述后又改（版本 2）
        self.assertEqual(changed["document"], plan.doc_id(made["name"], 2))
        self.turn[0] += 1
        started = self.start(planned["plan"])
        self.assertEqual((started["status"], started["changed"]), ("nothing_to_write", [made["document"]]))
        self.assertEqual(self.call(cc_tools.push, document=made["document"])["reason"], "not_in_plan")
        self.assertEqual(self.call(cc_tools.push, document=changed["document"])["reason"], "not_in_plan")
        self.assertEqual(self.record(made["name"]).status, "draft")
        self.assertEqual(self.jobs.rows, {})

    def test_a_change_after_the_start_is_not_released_either(self):
        made = self.make("600")
        self.agreed(made["document"])
        service.update_draft(made["name"], 1, [{"docnum": "IV1", "amount": "700"}], cash="700")  # 网页「我的收款」改了
        pushed = self.call(cc_tools.push, document=made["document"])
        self.assertEqual((pushed["status"], pushed["version"]), ("changed", 2))
        self.assertEqual(self.record(made["name"]).status, "draft")

    def test_the_card_reads_back_the_latest_version(self):
        made = self.make("600")
        service.update_draft(made["name"], 1, [{"docnum": "IV1", "amount": "700"}], cash="700")
        shown = self.call(cc_tools.card, collection=made["name"])
        self.assertEqual((shown["version"], shown["total"], shown["document"]),
                         (2, "700.00", plan.doc_id(made["name"], 2)))

    def test_an_old_version_in_a_new_plan_is_skipped_with_the_reason(self):
        made = self.make("600")
        service.update_draft(made["name"], 1, [{"docnum": "IV1", "amount": "700"}], cash="700")
        planned = self.plan_of(made["document"])
        self.assertEqual((planned["status"], planned["skipped"]),
                         ("nothing_to_write", [{"doc_id": made["document"], "reason": "collection.changed"}]))

    def test_a_stop_after_the_plan_keeps_it_from_starting(self):
        made = self.make("600")
        planned = self.plan_of(made["document"])
        self.turn[0] += 1
        self.stopped[0] = T.S.tick()
        self.assertEqual(self.start(planned["plan"])["status"], "stopped")
        self.assertEqual(self.call(cc_tools.push, document=made["document"])["reason"], "not_started")
        self.assertEqual(self.jobs.rows, {})

    def test_a_stop_after_the_start_keeps_it_from_release(self):
        made = self.make("600")
        self.agreed(made["document"])
        self.stopped[0] = T.S.tick()
        self.assertEqual(self.call(cc_tools.push, document=made["document"])["reason"], "stopped")
        self.assertEqual(self.record(made["name"]).status, "draft")

    def test_no_company_filed(self):
        self.company[0] = ""
        out = self.make("600")
        self.assertEqual(out["reason"], "no_company")
        self.assertIn("pearnly_file_conversation", out["note"])


class Vouchers(Base):
    def test_a_transfer_slip_comes_from_the_conversation_attachment(self):
        self.attached["slip.jpg"] = b"jpeg"
        made = self.make("600", cash="0", methods=[{"prefix": "BB", "amount": "600", "file": "slip.jpg"}])
        self.assertTrue(made["ready"])
        voucher = made["methods"][0]["voucher"]
        self.assertEqual(frappe.db.get_value("File", voucher, ["is_private", "attached_to_name"]), (1, made["name"]))

    def test_a_missing_slip_is_not_ready_until_attached(self):
        made = self.make("600", cash="0", methods=[{"prefix": "BB", "amount": "600"}])
        self.assertEqual((made["ready"], made["problem"]), (False, "collection.voucher_missing"))
        self.assertNotIn("document", made)
        self.assertEqual(self.plan_of(plan.doc_id(made["name"], 1))["skipped"][0]["reason"], "collection.voucher_missing")
        missing = self.call(cc_tools.voucher, collection=made["name"], version=1, prefix="BB", file="nope.jpg")
        self.assertEqual(missing["status"], "not_saved")
        self.attached["slip.jpg"] = b"jpeg"
        attached = self.call(cc_tools.voucher, collection=made["name"], version=1, prefix="BB", file="slip.jpg")
        self.assertEqual((attached["ready"], attached["document"]), (True, plan.doc_id(made["name"], 2)))


class Rights(Base):
    def test_someone_without_the_collector_post_is_refused(self):
        self.as_user("nobody@x")
        self.assertEqual(self.call(cc_tools.customers, search="21")["reason"], "not_permitted")
        self.assertEqual(self.make("600", user="nobody@x")["reason"], "not_permitted")

    def test_a_colleague_in_the_same_company_sees_plans_and_withdraws_it(self):
        """用户 11-14：本公司有收款权限的人不分谁录的——同事的收款也能看、放行、撤回；别家公司的看不到。"""
        made = self.make("600")
        self.as_user("b@x")
        self.conversation("b@x")
        planned = self.plan_of(made["document"])
        self.assertEqual(planned["skipped"], [])
        self.assertEqual(self.call(cc_tools.status, collection=made["name"])["name"], made["name"])
        listed = self.call(cc_tools.recent)
        self.assertEqual([row["name"] for row in listed["collections"]], [made["name"]])  # 本公司全部，不分谁录的
        self.assertEqual(self.call(cc_tools.recent, collector="b@x")["collections"], [])  # 按「谁录的」筛
        self.assertEqual(self.call(cc_tools.withdraw, collection=made["name"])["status"], "withdrawn")
        self.as_user("other@x")
        self.assertEqual(self.call(cc_tools.status, collection=made["name"])["reason"], "not_permitted")

    def test_a_collection_of_another_company_is_not_planned(self):
        self.as_user("a@x")
        self.assertEqual(plan.check("CO-2", [plan.doc_id(self.make("600")["name"], 1)]), {"documents": []})
        self.assertFalse(plan.allowed("a@x", "CO-2"))
        self.assertTrue(plan.allowed("boss@x", "CO-1"))  # 管理员默认有收款（11-14）
        self.assertTrue(plan.allowed("a@x", "CO-1"))

    def test_customers_invoices_and_methods_come_from_the_service(self):
        found = self.call(cc_tools.customers, search="ป้า")
        self.assertEqual((found["total"], found["customers"]), (1, [{"code": "2100883", "name": "ร้านป้าแดง"}]))
        invoices = self.call(cc_tools.invoices, customer="2100883")
        self.assertEqual([(row["docnum"], row["available"]) for row in invoices["invoices"]],
                         [("IV1", "1000.00"), ("IV2", "500.00")])
        methods = self.call(cc_tools.methods)
        self.assertEqual({row["prefix"] for row in methods["transfers"]}, T.LISTED)
        over = self.make("1200")
        self.assertEqual((over["status"], over["reason"]), ("not_saved", "collection.over_collected"))
        self.assertTrue(over["message"])


class Withdraw(Base):
    def test_withdraw_a_draft_and_a_released_one(self):
        made = self.make("100")
        self.assertEqual(self.call(cc_tools.withdraw, collection=made["name"])["collection"]["status"], "withdrawn")
        queued = self.make("100")
        self.agreed(queued["document"])
        self.call(cc_tools.push, document=queued["document"])
        self.assertEqual(self.call(cc_tools.withdraw, collection=queued["name"])["status"], "withdrawn")
        self.assertEqual(self.call(cc_tools.status, collection=queued["name"])["result"], "withdrawn")
        self.assertEqual(self.jobs.rows["J-" + queued["name"]]["status"], "cancelled")


class ConversationBook(Base, T.TwoBooks):
    """CC 对话里收款三步一律用本对话账套（A10 / A11，11-12）：列发票、列方式、建草稿同一本，不按今天、不按单据日期各算各的。"""

    def setUp(self):
        super().setUp()
        self.loaded = []
        loader = mock.patch("pearnly_erp_portal.express.facts.load",
                            side_effect=lambda key: self.loaded.append(key) or T.FACTS)
        loader.start()
        self.addCleanup(loader.stop)

    def test_across_the_year_end_the_three_steps_use_the_conversations_book(self):
        # 今天 2026-11-11 按日期是 TEST（2026）；对话选的是 TEST25（2025 年度，次年段到 2026 年底）
        self.books = [T.TEST26, T.TEST25]
        self.ledger[0] = self.ledger_of(T.TEST25, [T.TEST26, T.TEST25])
        self.as_user("a@x")
        listed = self.call(cc_tools.invoices, customer="2100883")
        self.assertEqual([row["docnum"] for row in listed["invoices"]], ["IV1"])
        self.assertIn(T.snapshot.request_key(T.TEST25["key"], "2100883"), self.requests.made)
        self.assertNotIn(T.snapshot.request_key(T.TEST26["key"], "2100883"), self.requests.made)
        self.loaded.clear()
        self.call(cc_tools.methods)
        self.assertEqual(set(self.loaded), {T.TEST25["key"]})
        for date in ("2025-12-28", None):
            made = self.make("100", **({"date": date} if date else {}))
            self.assertTrue(made["ready"], made)
            self.assertEqual(T.S.table("ERP Collection")[made["name"]]["book_key"], T.TEST25["key"], date)

    def test_an_undecided_conversation_book_is_asked_in_the_conversation(self):
        self.ledger[0] = {"status": "needed", "id": "", "label": "",
                          "options": [{"id": T.TEST26["key"], "label": "TEST", "covers": True},
                                      {"id": T.TEST26_COPY["key"], "label": "TEST", "covers": True}]}
        self.as_user("a@x")
        refused = self.call(cc_tools.invoices, customer="2100883")
        self.assertEqual((refused["status"], refused["reason"]), ("not_saved", "ledger_needed"))
        self.assertIn("pearnly_conversation_ledger", refused["note"])
        self.assertEqual([row["id"] for row in refused["allowed_account_sets"]], [T.TEST26["key"], T.TEST26_COPY["key"]])
        self.assertEqual(self.call(cc_tools.methods)["reason"], "ledger_needed")
        self.assertEqual(self.make("100")["reason"], "ledger_needed")
        self.ledger[0] = self.ledger_of(T.TEST26_COPY, [T.TEST26, T.TEST26_COPY])  # 用户在提问卡上选了、记在会话上
        made = self.make("100")
        self.assertEqual(T.S.table("ERP Collection")[made["name"]]["book_key"], T.TEST26_COPY["key"])

    def test_a_draft_in_another_book_than_the_conversations_is_not_written(self):
        self.books = [T.TEST26, T.TEST25]
        self.ledger[0] = self.ledger_of(T.TEST25, [T.TEST26, T.TEST25])
        made = self.make("100")
        self.ledger[0] = self.ledger_of(T.TEST26, [T.TEST26, T.TEST25])  # 之后在右栏换成 TEST
        planned = self.plan_of(made["document"])
        self.assertEqual(planned["status"], "nothing_to_write")
        self.assertEqual(planned["skipped"][0]["reason"], "collection.book_not_conversation")


class ExpressOpen(Base):
    def test_a_queued_collection_waiting_for_express_tells_cc_why(self):
        self.express_open[0] = True
        made = self.make("600")
        self.agreed(made["document"])
        pushed = self.call(cc_tools.push, document=made["document"])
        self.assertEqual((pushed["status"], pushed["express_wait"]), ("queued", True))
        self.assertIn("written automatically once Express is closed", pushed["note"])


if __name__ == "__main__":
    unittest.main()
