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
            words.setdefault(cells[4] if len(cells) > 4 and cells[4].strip() else cells[0], cells[1:4])  # 带键的按键查
        for tool in cc_tools.TOOLS:
            self.assertTrue(tool["name"].startswith("erp_collection_"), tool["name"])
            self.assertEqual(tool.get("modes"), ["chat"], tool["name"])  # 只在直写对话（用户 11-11：代录不做收款）
            if tool["name"] in ("erp_collection_withdraw", "erp_collection_resolve_result"):
                # 撤回先出「允许」卡片（用户 11-12：删除、撤回类在对话里点一次「允许」）；MR.ERP 结果不明的核对同样（11-28）
                self.assertIs(tool["approval"], True)
                self.assertEqual(len([cell for cell in words.get(tool["ask"], []) if cell.strip()]), 3, tool["ask"])
            else:
                self.assertNotIn("approval", tool)  # 不加「允许」卡片：放行由写入计划 + 用户下一句定
            module, _dot, function = tool["handler"].rpartition(".")
            self.assertEqual(module, "pearnly_erp_portal.collection.cc_tools")
            self.assertTrue(callable(getattr(cc_tools, function)), tool["handler"])
            for text in (tool["label"], cc_tools.STEP_NAMES[tool["name"]]):
                self.assertEqual(len([cell for cell in words.get(text, []) if cell.strip()]), 3, text)
        hooks = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/hooks.py").read_text(encoding="utf-8")
        self.assertIn('"pearnly_erp_portal.collection.cc_tools.TOOLS"]', hooks)
        self.assertIn('"pearnly_erp_portal.collection.cc_tools.STEP_NAMES"]', hooks)

    def test_an_operator_conversation_has_no_collection_tool_and_a_direct_one_has_all_nine(self):
        # 用户 11-11：代录不做收款。各模式只用自己的工具：代录会话给模型的工具表里一个 erp_collection_* 都没有（共用层
        # `tools.mode_allowed`，同 `definition.brief` 按模式整组换上）
        from pearnly_core.assistant import tools

        with mock.patch.object(tools, "declared", lambda: list(cc_tools.TOOLS)):
            chat = [t["name"] for t in tools.declared() if tools.mode_allowed(t["name"], "chat")]
            operator = [t["name"] for t in tools.declared() if tools.mode_allowed(t["name"], "operator")]
        self.assertEqual(len(chat), 9)
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


class TypedCollectionOpensTheCard(Base):
    """打字说要收款只打开收款卡（用户 11-16「按建议做」）：给模型的工具里没有建草稿 / 挂凭证 / 放行，只有开卡；说到的客户名 / 单号
    原样交给引导预填搜索框，不带金额；查询类工具照旧。开卡的工具是收款引导声明的那一个。"""

    def test_the_model_can_only_open_the_card_and_query(self):
        names = [tool["name"] for tool in cc_tools.TOOLS]
        self.assertEqual(names[0], "erp_collection_guide")
        self.assertFalse(set(names) & set(cc_tools.TOOLS_REPLACED_BY_CARD))
        self.assertTrue({"erp_collection_customers", "erp_collection_invoices", "erp_collection_status",
                         "erp_collection_list"} <= set(names))
        self.assertEqual(list(cc_tools.TOOLS[0]["input_schema"]["properties"]), ["hint"])
        # 11-20 真模型：说到客户就只传客户，没说客户才传发票号（模型曾把「码 + 发票号」一起传）
        hint = cc_tools.TOOLS[0]["input_schema"]["properties"]["hint"]["description"]
        self.assertIn("only when no customer was named, the invoice number", hint)
        self.assertIn("Never both", hint)
        # 工具说明写它做什么，不写给模型的命令句（11-20：模型把说明复述成一句英文步骤出现在中文回复里）
        description = cc_tools.TOOLS[0]["description"]
        self.assertTrue(description.startswith("Opens the collection card"))
        self.assertNotIn("Never pass", description)
        for name in cc_tools.TOOLS_REPLACED_BY_CARD:   # 函数留着：整条撤回时恢复声明即可
            self.assertTrue(callable(getattr(cc_tools, name.removeprefix("erp_collection_"))))
        from pearnly_erp_portal.collection import cc_guide

        self.assertEqual(cc_guide.GUIDES[0]["tool"], "erp_collection_guide")

    def test_opening_starts_the_collection_guide_in_this_conversation_with_the_hint(self):
        from pearnly_core.assistant.guide import run

        started = []
        warmed = []
        with mock.patch.object(run, "start", lambda session, business, **kw: started.append((session, business, kw))
                               or {"run": "RUN1", "status": "asking"}), \
                mock.patch.object(service, "warm", lambda company, day=None: warmed.append(company)):
            out = json.loads(cc_tools.guide({"hint": "  ร้านป้าแดง   600 "}))
        self.assertEqual(warmed, [cc_tools._company()])   # 同 LINE 选公司：没读过未收发票的账套先整本读
        self.assertEqual(started, [(SESSION, "collection", {"hint": "ร้านป้าแดง 600"})])
        self.assertEqual((out["status"], out["run"]), ("opened", "RUN1"))
        self.assertIn("names no customer, invoice or amount and asks for none", out["note"])
        with mock.patch.object(run, "start", lambda session, business, **kw: started.append(kw) or {"run": "RUN2"}):
            cc_tools.guide({})
        self.assertEqual(started[-1], {"hint": ""})

    def test_a_card_that_cannot_open_says_why(self):
        from pearnly_core.assistant.guide import run

        def refuse(*args, **kwargs):
            raise frappe.ValidationError("Invalid request")
        with mock.patch.object(run, "start", refuse):
            out = json.loads(cc_tools.guide({"hint": "IV1"}))
        self.assertEqual(out["status"], "not_saved")
        frappe.local.pearnly_assistant = {}
        self.assertEqual(json.loads(cc_tools.guide({}))["reason"], cc_tools.NOT_IN_CONVERSATION)


class OtherEntriesDoNotUseTheCcTools(unittest.TestCase):
    """拿掉 CC 的建草稿 / 挂凭证 / 放行三个工具不影响网页、LINE、MR.ERP 账套的收款（主会话 11-19 ②）：除了 CC 工具模块自己和收款引导
    （只借它的撤回问句拼法 `note_withdraw`），没有别的产品代码引用 `collection.cc_tools`；它们都直接调 `collection.service`。
    行为由 test_erp_collection（网页 / 服务函数）、test_erp_collection_line（LINE）、test_erp_admin_collection、test_erp_collection_guide 守住。"""

    def test_only_the_cc_tool_module_and_the_guide_note_reference_it(self):
        app = ROOT / "apps" / "pearnly_erp_portal" / "pearnly_erp_portal"
        users = {}
        for path in app.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "collection import cc_tools" in text or "collection.cc_tools" in text:
                users[str(path.relative_to(app))] = text
        self.assertEqual(sorted(users), ["collection/cc_tools.py", "collection/guide.py", "hooks.py"])
        guide = users["collection/guide.py"]
        self.assertEqual(guide.count("cc_tools."), guide.count("cc_tools.note_withdraw"))
        for name in cc_tools.TOOLS_REPLACED_BY_CARD:
            self.assertNotIn(name, users["hooks.py"])
        for entry in ("collection/api.py", "integrations/line_collection.py", "integrations/line_collection_cards.py"):
            self.assertIn("service", (app / entry).read_text(encoding="utf-8"), entry)


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

    def test_a_result_unknown_is_unconfirmed_not_failed(self):
        """主会话 11-25：CC 桌面版写到一半断了（写没写进去不知道）不说失败：结果 unconfirmed + 不再录的定式。"""
        made = self.make("600")
        self.agreed(made["document"])
        self.call(cc_tools.push, document=made["document"])
        self.jobs.report("J-" + made["name"], "manual", reason=T.C.EXPRESS_UNKNOWN)
        status = self.call(cc_tools.status, collection=made["name"])
        self.assertEqual((status["result"], status["note"]), ("unconfirmed", cc_tools.UNCONFIRMED_NOTE))
        self.assertNotIn("did not take", status["note"])

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
        self.assertEqual((found["total"], found["customers"]), (1, [{"code": "2100883", "name": "ร้านป้าแดง", "tax_id": ""}]))  # 税号一并给（找客户同一份查询）
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

    def test_the_allow_card_lists_the_customer_invoices_and_amount_and_changes_nothing(self):
        """撤回先出「允许」卡片（用户 11-12）：卡上是客户、哪几张发票、多少钱；出卡片只读，不撤回。"""
        made = self.make("100")
        rows = cc_tools.preview_withdraw({"collection": made["name"]})
        self.assertEqual([row["after"] for row in rows], ["2100883 · ร้านป้าแดง", "IV1", "100.00"])
        self.assertEqual(self.call(cc_tools.status, collection=made["name"])["status"], "draft")
        # 问句下面那一行事实：公司与账套的名字（不写公司编号）
        fact = cc_tools.note_withdraw({"collection": made["name"]})
        view = service.status(made["name"])
        book = view["book"]
        expected = book if book.startswith(view["company_name"]) else " · ".join(p for p in (view["company_name"], book) if p)
        self.assertEqual(fact, expected)  # 账套名里已带公司名的不重复（11-16）
        self.assertTrue(fact)
        self.assertNotIn(view["company"], fact.split(" · "))
        self.assertEqual(cc_tools.note_withdraw({"collection": "NOPE"}), "")  # 没有这一笔：那一行不出，不报错


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
        self.assertEqual(len(refused["allowed_account_sets"]), 2)
        # 只给名称，不给账套键（book_key 是内部编号，CC 会复述给人；A 线 11-15）
        self.assertFalse(any(T.TEST26["key"] in str(row) or T.TEST26_COPY["key"] in str(row)
                             for row in refused["allowed_account_sets"]))
        self.assertTrue(all(row["label"] for row in refused["allowed_account_sets"]))
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

    def test_a_date_outside_the_conversations_book_asks_for_the_date(self):
        """11-15 用户：对话账套是老账套、今天不在它的期间里：不拒，回 date_needed 带这一本的期间，叫模型问日期；换账套走对话的
        账套选择，不在这里另给。"""
        self.books = [T.TEST26, T.OLD]
        T.snapshot.replace("CO-1", T.OLD["key"], T.OPEN, "2026-11-11T03:00:00+00:00")
        self.ledger[0] = self.ledger_of(T.OLD, [T.TEST26, T.OLD])
        self.as_user("a@x")
        refused = self.call(cc_tools.invoices, customer="2100883")
        self.assertEqual((refused["status"], refused["reason"]), ("not_saved", "collection.date_needed"))
        self.assertEqual([(row["start"], row["end"], row["last"]) for row in refused["periods"]],
                         [("2024-01-01", "2025-12-31", "2024-12-31")])
        self.assertIn("OLD", refused["periods"][0]["account_set"])
        self.assertIn("ask_user", refused["note"])
        self.assertEqual(self.call(cc_tools.methods)["reason"], "collection.date_needed")
        listed = self.call(cc_tools.invoices, customer="2100883", date="2024-12-31")
        self.assertEqual([row["docnum"] for row in listed["invoices"]], ["IV1"])
        self.assertTrue(self.call(cc_tools.methods, date="2024-12-31")["cash"])
        made = self.make("100", date="2024-12-31")
        self.assertTrue(made["ready"], made)
        row = T.S.table("ERP Collection")[made["name"]]
        self.assertEqual((row["book_key"], str(row["doc_date"])), (T.OLD["key"], "2024-12-31"))


class ExpressOpen(Base):
    def test_a_queued_collection_waiting_for_express_tells_cc_why(self):
        self.express_open[0] = True
        made = self.make("600")
        self.agreed(made["document"])
        pushed = self.call(cc_tools.push, document=made["document"])
        self.assertEqual((pushed["status"], pushed["express_wait"]), ("queued", True))
        self.assertIn("written automatically once Express is closed", pushed["note"])


NBSP = "นาย\u00a0ทดสอบ"  # 11-15 真机：Express 里的客户编码带不换行空格（69EXP\TEST）


class CodeFidelity(Base):
    """模型复述编码时空白会变（不换行空格 → 普通空格，真模型 3/3），库的排序规则又把两者当相等：工具一律换回主档原样，
    草稿、写入载荷里是 Express 的原样编码；只差空白的两位客户交人选；写前被拒给定式说明，不叫人去 Express 建客户。"""

    def setUp(self):
        super().setUp()
        T.S.table("ERP Customer")["C9"] = {"name": "C9", "express_company": "CO-1", "express_code": NBSP,
                                          "customer_name": "นาย ทดสอบ", "is_active": 1}
        T.snapshot.replace("CO-1", T.BOOK["key"], [
            {"docnum": "IV0000006", "rectyp": "3", "docdat": "2026-11-01", "customer": NBSP, "salesman": "",
             "department": "", "net": "1000.00", "received": "0.00", "remaining": "1000.00"}],
            "2026-11-15T03:00:00+00:00")
        self.as_user("a@x")

    def draft_as(self, given, docnum="IV0000006"):
        return self.call(cc_tools.draft, customer=given, invoices=[{"docnum": docnum, "amount": "1000"}], cash="1000")

    def test_every_spelling_lands_as_the_master_code_in_the_draft_and_the_payload(self):
        for given in ("นาย ทดสอบ", "นาย  ทดสอบ", " นาย ทดสอบ ", "นาย\u3000ทดสอบ", NBSP):
            with self.subTest(given=repr(given)):
                found = self.call(cc_tools.invoices, customer=given)
                self.assertEqual(found["customer"], NBSP)
                self.assertEqual([row["docnum"] for row in found["invoices"]], ["IV0000006"])
                made = self.draft_as(given)
                self.assertTrue(made["ready"], made)
                self.assertEqual(made["customer"], NBSP)
                self.assertEqual(frappe.db.get_value("ERP Collection", made["name"], "customer"), NBSP)
                self.call(cc_tools.withdraw, collection=made["name"])
        made = self.draft_as("นาย ทดสอบ", docnum="IV0000006 ")  # 单号复述多了空白也换回快照里的原样
        self.agreed(made["document"])
        self.assertEqual(self.call(cc_tools.push, document=made["document"])["status"], "queued")
        payload = self.jobs.rows["J-" + made["name"]]["payload"]
        self.assertEqual((payload["customer"], payload["invoices"]), (NBSP, [{"docnum": "IV0000006", "amount": "1000.00"}]))

    def test_two_customers_differing_only_by_spaces_go_back_to_the_user(self):
        T.S.table("ERP Customer")["C10"] = {"name": "C10", "express_company": "CO-1", "express_code": "นาย\u3000ทดสอบ",
                                           "customer_name": "นาย ทดสอบ (2)", "is_active": 1}
        refused = self.draft_as("นาย ทดสอบ")
        self.assertEqual((refused["status"], refused["reason"]), ("not_saved", "code_not_matched"))
        self.assertEqual(sorted(refused["options"]), sorted([NBSP, "นาย\u3000ทดสอบ"]))
        self.assertIn("ask_user", refused["note"])
        self.assertIn("Never say it is missing from Express", refused["note"])
        self.assertEqual(self.draft_as(NBSP)["customer"], NBSP)  # 原样给的照旧认得出
        self.assertEqual(T.S.table("ERP Customer")["C10"]["express_code"], "นาย\u3000ทดสอบ")

    def test_invoices_of_a_sibling_code_are_not_listed(self):
        T.S.table("ERP Customer")["C10"] = {"name": "C10", "express_company": "CO-1", "express_code": "นาย ทดสอบ",
                                           "customer_name": "นาย ทดสอบ (2)", "is_active": 1}
        exact = self.call(cc_tools.invoices, customer="นาย ทดสอบ")  # 逐字有这位：认它，不列另一位（只差空白）的发票
        self.assertEqual((exact["customer"], exact["invoices"]), ("นาย ทดสอบ", []))

    def test_an_unknown_customer_is_not_called_missing_from_express(self):
        refused = self.draft_as("ไม่มีคนนี้")
        self.assertEqual((refused["reason"], refused["options"]), ("code_not_matched", []))
        self.assertNotIn("message", refused)

    def test_a_refusal_before_writing_gets_the_fixed_note(self):
        made = self.draft_as("นาย ทดสอบ")
        self.agreed(made["document"])
        self.call(cc_tools.push, document=made["document"])
        self.jobs.report("J-" + made["name"], "manual", reason="[CUSTOMER_NOT_IN_ARMAS] customer is not in Express")
        status = self.call(cc_tools.status, collection=made["name"])
        self.assertTrue(status["returned"])
        self.assertIn("Do not tell the user to create or change anything in Express", status["note"])
        self.assertIn("ask_user", status["note"])
        self.assertIn("refused_note", self.call(cc_tools.card, collection=made["name"]))


class ReadBack(Base):
    """查发票 / 建草稿时快照里没有那张：先按客户即时读一次、短等，再按读回的几种结果说话（11-15 真机：刚开的发票还没读到，
    CC 说成「已收清，请在 Express 核对」）。快照为空（新开的发票）、快照过期（那张其实已收清）、读回超时、电脑未连接。"""

    KEY = None

    def setUp(self):
        super().setUp()
        frappe.local.pearnly_assistant["may_wait"] = True
        self.KEY = T.snapshot.request_key(T.BOOK["key"], "2100883")
        self.answer = [None]  # 电脑在短等期间读回的那一份（None = 一直没回）
        self.looked = [None]  # 带单号的那种读回里按单号查到的（CC 桌面版 1.12.22 起）
        clock = [0.0]

        def sleep(seconds):
            clock[0] += max(seconds, 0.5)
            for key, made in list(self.requests.made.items()):
                if self.answer[0] is not None and made["status"] == "requested" and key.startswith(self.KEY):
                    self.requests.finish(key, self.answer[0], "2026-11-15T04:00:00+00:00", self.looked[0])

        for patch in (mock.patch.object(cc_tools.time, "sleep", side_effect=sleep),
                      mock.patch.object(cc_tools.time, "monotonic", side_effect=lambda: clock[0])):
            patch.start()
            self.addCleanup(patch.stop)
        self.as_user("a@x")

    @staticmethod
    def row(docnum, remaining, customer="2100883"):
        return {"docnum": docnum, "rectyp": "3", "docdat": "2026-11-15", "customer": customer, "salesman": "",
                "department": "", "net": remaining, "received": "0.00", "remaining": remaining}

    def draft_of(self, docnum, amount):
        return self.call(cc_tools.draft, customer="2100883", invoices=[{"docnum": docnum, "amount": amount}], cash=amount)

    def test_a_new_invoice_not_in_the_snapshot_is_read_then_recorded(self):
        self.answer[0] = [self.row("IV1", "1000.00"), self.row("IV2", "500.00"), self.row("IV691115-003", "572.45")]
        made = self.draft_of("IV691115-003", "572.45")
        self.assertTrue(made.get("ready"), made)
        self.assertEqual([row["docnum"] for row in made["invoices"]], ["IV691115-003"])

    def test_a_stale_snapshot_is_read_again_and_a_collected_invoice_is_said_as_not_open(self):
        self.answer[0] = [self.row("IV2", "500.00")]  # IV1 在 Express 里刚被收清
        listed = self.call(cc_tools.invoices, customer="2100883")
        self.assertEqual((listed["read"], [row["docnum"] for row in listed["invoices"]]), ("ready", ["IV2"]))
        self.assertIn("not open for this customer in Express now", listed["note"])
        self.assertIn("do not ask the user to check Express", listed["note"])
        refused = self.draft_of("IV1", "1000")
        self.assertEqual((refused["status"], refused["reason"], refused["read"], refused["docnum"]),
                         ("not_saved", T.C.INVOICE_NOT_FOUND, "ready", "IV1"))
        self.assertNotIn("message", refused)

    def test_a_read_that_does_not_come_back_in_time_is_never_said_as_collected(self):
        listed = self.call(cc_tools.invoices, customer="2100883")
        self.assertEqual(listed["read"], "reading")
        self.assertEqual([row["docnum"] for row in listed["invoices"]], ["IV1", "IV2"])  # 上一次读到的照给，标明不是最新
        self.assertIn("Never say an invoice is collected, missing or not open", listed["note"])
        refused = self.draft_of("IV691115-003", "572.45")
        self.assertEqual((refused["reason"], refused["read"]), (T.C.INVOICE_NOT_FOUND, "reading"))
        self.assertIn("still reading from Express", refused["note"])
        self.assertNotIn("message", refused)
        self.answer[0] = [self.row("IV691115-003", "572.45")]  # 用户说「再读一次」：读到了照常建
        self.assertTrue(self.draft_of("IV691115-003", "572.45").get("ready"))

    def test_a_number_not_listed_is_looked_up_and_said_as_collected_voided_or_not_in_the_book(self):
        # 11-15 已知限制：读回只报未收清的发票，「收清了」与「账套里没有这张」分不开。1.12.22 起同一次读按单号查
        self.answer[0] = [self.row("IV2", "500.00")]  # IV1 在 Express 里已收清
        self.call(cc_tools.invoices, customer="2100883")
        for docnum, looked, state in (
                ("IV1", [{"docnum": "IV1", "customer": "2100883", "state": "settled", "remaining": "0.00"}], "settled"),
                ("IV7", [{"docnum": "IV7", "customer": "2100883", "state": "cancelled", "remaining": "100.00"}], "cancelled"),
                ("IV9", [], "absent"),
                ("IV8", [{"docnum": "IV8", "customer": "9999", "state": "open", "remaining": "50.00"}], "open")):
            self.looked[0] = looked
            refused = self.draft_of(docnum, "100")
            self.assertEqual((refused["reason"], refused["read"], refused["docnum"], refused.get("invoice_state")),
                             (T.C.INVOICE_NOT_FOUND, "ready", docnum, state), docnum)
            self.assertIn(f"invoice: {state}", refused["invoice_note"])
        self.assertEqual(refused["invoice_customer"], "9999")

    def test_when_every_computer_is_older_than_1_12_22_the_plain_read_is_sent_and_nothing_is_guessed(self):
        sent = []

        def waiting(kind, book_key, actor=None, params=None):
            sent.append(dict(params or {}))
            return {"reason": "update_needed"} if "docnums" in (params or {}) else None

        self.answer[0] = [self.row("IV2", "500.00")]
        self.call(cc_tools.invoices, customer="2100883")
        sent.clear()
        with mock.patch.object(self.requests, "waiting", side_effect=waiting):
            refused = self.draft_of("IV1", "1000")
        self.assertEqual((refused["reason"], refused["read"]), (T.C.INVOICE_NOT_FOUND, "ready"))
        self.assertNotIn("invoice_state", refused)
        self.assertEqual([("docnums" in params) for params in sent[:2]], [True, False])

    def test_an_offline_computer_is_said_as_not_connected(self):
        with mock.patch.object(self.requests, "waiting", side_effect=lambda kind, book_key, actor=None, params=None: {"reason": "offline"}):
            listed = self.call(cc_tools.invoices, customer="2100883")
            self.assertEqual(listed["read"], "offline")
            self.assertIn("not connected", listed["note"])
            refused = self.draft_of("IV691115-003", "572.45")
        self.assertEqual((refused["reason"], refused["read"]), (T.C.INVOICE_NOT_FOUND, "offline"))


class MrErpNotes(unittest.TestCase):
    """账本是 MR.ERP 的收款：给模型的结果定式说 MR.ERP、不提 CC 桌面版；Express 的照旧。"""

    def test_refused_queued_and_reading_notes_follow_the_books(self):
        failed = {"status": T.C.FAILED, "ledger": "mrerp"}
        self.assertEqual(cc_tools._refusal_note({}, failed)["note"], cc_tools.MRERP_REFUSED_NOTE)
        self.assertEqual(cc_tools._refusal_note({}, {"status": T.C.FAILED})["note"], cc_tools.REFUSED_NOTE)
        unknown = {"status": T.C.FAILED, "reason": "mrerp.result_unknown: ERR_TECHNICAL", "ledger": "mrerp"}
        self.assertEqual(cc_tools._refusal_note({}, unknown)["note"], cc_tools.MRERP_UNCONFIRMED_NOTE)
        self.assertNotIn("Express", cc_tools.MRERP_UNCONFIRMED_NOTE)
        for note in (cc_tools.MRERP_REFUSED_NOTE, cc_tools.MRERP_QUEUED_NOTE, cc_tools.MRERP_READING_NOTE):
            self.assertNotIn("Express", note)
            self.assertNotIn("CC Desktop", note)
        read = {"refresh": "ready", "items": [], "customer": "C1"}
        self.assertNotIn("Express", cc_tools._read_view(read, "C1", True)["note"])
        self.assertIn("Express", cc_tools._read_view(read, "C1")["note"])
        reading = {**read, "refresh": "reading"}
        self.assertEqual(cc_tools._read_view(reading, "C1", True)["note"], cc_tools.MRERP_READING_NOTE)


if __name__ == "__main__":
    unittest.main()
