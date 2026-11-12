"""收款第 3 步：ERP 的 LINE 收款入口（富菜单「收款」→ 选公司 → 找客户 → 勾发票 → 方式与金额 → 凭证 → 确认卡 → 放行 → 结果）。

不连库、不出网：沿用第 2 步的 Frappe 替身（`test_erp_collection`：内存表、NLTTEST 收款类型、快照里 2100883 两张发票
IV1 1000 / IV2 500、2100900 一张 IV3 50），LINE 传输层、会话、绑定、不淘汰的状态存储换成记录器。走真实的
`integrations.line_collection` 与 `collection.service`：服务端的权限、预留、版本、撤回都是真的。

覆盖：每一步的状态、客户超过 12 个、发票多选与合计、组合 / 折扣 / 部分（哪张留余额、逐张输入）、超收重输、转账缺凭证、
【确认】按钮与打字确认、卡片版本变了重出卡、撤回、权限（无岗位、伪造按钮）、结果用回复还是推送、富菜单第 4 格。
"""
import contextlib
import json
import pathlib
import sys
import types
import unittest
from unittest import mock
from urllib.parse import parse_qs

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_erp_collection as T  # noqa: E402  装好 Frappe 替身与第 2 步的数据

frappe = T.frappe
frappe.set_user = lambda user: setattr(frappe.session, "user", user)
frappe.local.lang = "zh"
frappe.get_traceback = lambda *a, **k: ""
frappe.db.get_single_value = lambda doctype, field: "th"


class FakeLine(types.ModuleType):
    """`pearnly_core.line` 的记录器：回复、推送、三个点、下载都只记下来。"""

    def __init__(self):
        super().__init__("pearnly_core.line")
        self.USER_LANGUAGES = ("th", "zh", "en", "ja")
        self.reset()

    def reset(self):
        self.replies, self.pushes, self.loading, self.content = [], [], [], b"\x89PNG voucher"

    def reply(self, token, messages):
        self.replies.append((token, messages))
        return True

    def reply_or_push(self, to, messages, reply_token="", **kwargs):
        self.replies.append((reply_token or "push:" + to, messages))
        return True

    def push(self, to, messages, **kwargs):
        self.pushes.append((to, messages))
        return True

    def start_loading(self, user_id, seconds=30):
        self.loading.append(user_id)
        return True

    def download_message_content(self, message_id, **kwargs):
        return self.content

    def user_language(self, user_id, default=None):
        return "zh"

    @contextlib.contextmanager
    def batch(self, to, reply_token=""):
        yield


LINE = FakeLine()
SESSIONS = {}
session_module = types.ModuleType("pearnly_core.line_session")
session_module.get_session = lambda uid: SESSIONS.get(uid)
session_module.set_session = lambda uid, state, payload, ttl_minutes=None: SESSIONS.__setitem__(
    uid, {"state": state, "payload": json.loads(json.dumps(payload))})
session_module.clear_session = lambda uid, **kw: SESSIONS.pop(uid, None)
BOUND = {"U1": "a@x", "U2": "b@x", "U9": "nobody@x"}
binding_module = types.ModuleType("pearnly_core.line_binding")
binding_module.user_of = lambda uid: BOUND.get(uid)
binding_module.binding_of = lambda uid: types.SimpleNamespace(user=BOUND[uid]) if uid in BOUND else None
login_module = types.ModuleType("pearnly_core.account.line_login")
login_module.entry_url = lambda path: "https://testerp.pearnly.com/api/method/enter?to=" + path


class FakeRedis:
    def __init__(self):
        self.values = {}

    def make_key(self, key):
        return "site|" + key

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)


REDIS = FakeRedis()
try:
    import requests  # noqa: F401  富菜单模块顶层导入它；CI 的纯 Python 里可能没有
except ImportError:
    sys.modules["requests"] = types.ModuleType("requests")
state_module = types.ModuleType("pearnly_core.execution.state")
state_module.store = lambda: REDIS
sys.modules.update({"pearnly_core.line": LINE, "pearnly_core.line_session": session_module,
                    "pearnly_core.line_binding": binding_module, "pearnly_core.account.line_login": login_module,
                    "pearnly_core.execution.state": state_module})
import pearnly_core  # noqa: E402

pearnly_core.line, pearnly_core.line_session, pearnly_core.line_binding = LINE, session_module, binding_module

from pearnly_erp_portal.collection import contracts as C, service  # noqa: E402
from pearnly_erp_portal.integrations import line_collection as flow, line_collection_cards as cards  # noqa: E402

C1 = "2100883"


class Base(T.Base):
    def setUp(self):
        super().setUp()
        LINE.reset()
        SESSIONS.clear()
        REDIS.values.clear()
        self.enqueued = []
        enqueue = mock.patch.object(frappe, "enqueue", side_effect=lambda path, **kw: self.enqueued.append((path, kw)))
        enqueue.start()
        self.addCleanup(enqueue.stop)
        T.ALLOWED["a@x"] = ["CO-1"]
        frappe.session.user = "Guest"  # webhook 是 Guest，按绑定的账号调服务
        T.S.table("Pearnly LINE Binding").update({"B1": {"name": "B1", "user": "a@x", "status": "active",
                                                          "line_user_id": "U1"}})

    # ── 假事件 ──
    def tap(self, data, uid="U1", event="evt-1"):
        flow.answer(uid, BOUND.get(uid), data, "r-" + event, event)

    def type(self, text, uid="U1", event="evt-2"):
        return flow.text(uid, BOUND.get(uid), text, "r-" + event, event)

    def last(self):
        return LINE.replies[-1][1]

    def last_text(self):
        return "\n".join(message.get("text", "") for message in self.last() if message.get("type") == "text")

    def buttons(self):
        for message in reversed(self.last()):
            if message.get("quickReply"):
                return [item["action"] for item in message["quickReply"]["items"]]
        return []

    def flex_buttons(self):
        out = []

        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "button":
                    out.append(node["action"])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk([message for message in self.last() if message.get("type") == "flex"])
        return out

    def step(self, uid="U1"):
        return (SESSIONS.get(uid) or {}).get("payload", {}).get("step")

    def payload(self, uid="U1"):
        return SESSIONS[uid]["payload"]

    def to_invoices(self, picks=("IV1", "IV2")):
        self.tap("a=col&s=start")
        self.type(C1)
        for docnum in picks:
            self.tap(f"a=col&s=inv&v={docnum}")
        self.tap("a=col&s=invoices&v=done")

    def pick(self, label):
        action = next(item for item in self.buttons() if item["label"] == label)
        self.tap(action["data"])

    def card_button(self, label):
        return next(item for item in self.flex_buttons() if item["label"] == label)


class Steps(Base):
    def test_each_step_is_asked_in_order_and_a_cash_collection_reaches_the_card(self):
        self.tap("a=col&s=start")
        self.assertEqual(self.step(), "customer")  # 只有一家公司：跳过
        self.assertIn("Type the customer code or part of the name", self.last_text())
        self.type("ร้าน")
        self.assertEqual([item["label"] for item in self.buttons()], ["1. ร้านป้าแดง", "2. ร้านลุงชัย", "Cancel"])
        self.tap("a=col&s=customer&v=" + C1)
        self.assertEqual((self.step(), self.payload()["order"]), ("invoices", ["IV1", "IV2"]))
        self.assertEqual([item["label"] for item in self.flex_buttons()], ["Select", "Select", "Next step"])
        self.tap("a=col&s=invoices&v=done")
        self.assertIn("Choose at least one invoice", self.last_text())
        self.tap("a=col&s=inv&v=IV1")
        self.tap("a=col&s=inv&v=IV2")
        self.tap("a=col&s=invoices&v=done")
        self.assertEqual(self.step(), "method")
        self.assertEqual([item["label"] for item in self.buttons()],
                         ["Cash", "เงินโอนเข้าบัญชี SCB/ออมทรัพย์"[:20], "เงินโอนเข้าบัญชี BBL/ออมทรัพย์"[:20],
                          "เงินโอนเข้าบัญชี BBL/กระแส"[:20], "เงินโอนเข้าบัญชี SCB/กระแส"[:20],
                          "เงินโอนเข้าบัญชี KB/CA"[:20], "Cancel"])
        self.pick("Cash")
        self.assertEqual((self.step(), self.buttons()[0]["label"]), ("amount", "1,500.00"))
        self.type("1,500")
        self.assertEqual(self.step(), "card")
        name = self.payload()["name"]
        record = T.S.table("ERP Collection")[name]
        self.assertEqual((record["status"], str(record["cash"]), record["source"], record["collector"]),
                         (C.DRAFT, "1500.00", "line", "a@x"))
        self.assertEqual([item["label"] for item in self.flex_buttons()], ["Confirm", "Edit", "Withdraw"])
        self.assertEqual(parse_qs(self.card_button("Confirm")["data"]),
                         {"a": ["col"], "s": ["ok"], "n": [name], "ver": ["1"]})
        self.assertTrue(self.card_button("Edit")["uri"].endswith("/desk/pearnly-my-collections/" + name))
        self.assertEqual(frappe.session.user, "Guest")  # 做完还原

    def test_more_than_twelve_customers_asks_for_more_letters_and_an_exact_code_picks_directly(self):
        for index in range(15):
            T.S.table("ERP Customer")[f"X{index}"] = {"name": f"X{index}", "express_company": "CO-1",
                                                      "express_code": f"99{index:03d}", "customer_name": f"shop {index}",
                                                      "is_active": 1}
        self.tap("a=col&s=start")
        self.type("shop")
        self.assertEqual(self.last_text(), "Too many matches; type a few more letters to narrow it down")
        self.assertEqual(self.step(), "customer")
        self.type("nothing like it")
        self.assertIn("No match for “nothing like it”", self.last_text())
        self.type("99007")  # 整串编号：直接选上（快照里还没有这位客户的发票：请电脑读，给【刷新】再看）
        self.assertEqual((self.step(), self.payload()["customer"]), ("customer", "99007"))
        self.assertEqual(self.last_text(), "Reading from Express")
        self.assertEqual(self.buttons()[0]["data"], "a=col&s=customer&v=99007")

    def test_a_customer_that_cannot_be_read_now_says_why_not_no_open_invoices(self):
        # 11-12 三星实测：读不到（电脑没连 / 要更新）时说「没有未收清的发票」会误导收款员
        T.S.table("ERP Customer")["X1"] = {"name": "X1", "express_company": "CO-1", "express_code": "99001",
                                          "customer_name": "shop one", "is_active": 1}
        for state, text in (("offline", "CC Desktop not connected"), ("outdated", "CC Desktop needs an update")):
            with mock.patch.object(T.snapshot, "request", return_value=state):
                self.tap("a=col&s=start")
                self.type("99001")
                self.assertIn(text, self.last_text())
                self.assertNotIn("No open invoices", self.last_text())
                self.tap("a=col&s=cancel")

    def test_picking_invoices_toggles_and_shows_the_running_total(self):
        self.tap("a=col&s=start")
        self.type(C1)
        self.tap("a=col&s=inv&v=IV2")
        footer = json.dumps(self.last(), ensure_ascii=False)
        self.assertIn("1 selected · total 500.00", footer)
        self.tap("a=col&s=inv&v=IV1")
        self.assertIn("2 selected · total 1,500.00", json.dumps(self.last(), ensure_ascii=False))
        self.assertEqual([item["label"] for item in self.flex_buttons()], ["Selected", "Selected", "Next step"])
        self.tap("a=col&s=inv&v=IV2")
        self.assertEqual(self.payload()["picked"], ["IV1"])
        self.tap("a=col&s=inv&v=IV3")  # 别的客户的发票：不是这一步给的
        self.assertEqual(self.last_text(), "This option is no longer current")

    def test_invoice_balances_already_reserved_by_another_collector_are_shown_net(self):
        T.Base.draft(self, "600", user="b@x")
        frappe.session.user = "b@x"
        made = service.mine()["items"][0]
        service.release(made["name"], made["version"], "line:other")
        frappe.session.user = "Guest"
        self.tap("a=col&s=start")
        self.type(C1)
        self.assertEqual(self.payload()["due"], {"IV1": "400.00", "IV2": "500.00"})

    def test_stale_buttons_from_another_step_do_nothing(self):
        self.tap("a=col&s=start")
        self.tap("a=col&s=method&v=%40cash")
        self.assertEqual((self.last_text(), self.step()), ("This option is no longer current", "customer"))

    def test_cancel_clears_the_session(self):
        self.tap("a=col&s=start")
        self.tap("a=col&s=cancel")
        self.assertEqual((self.last_text(), SESSIONS.get("U1")), ("Cancelled", None))
        self.assertFalse(self.type("anything"))  # 不在收款里：交给别的处理


class Amounts(Base):
    def test_overcollection_is_refused_and_asked_again(self):
        self.to_invoices()
        self.pick("Cash")
        self.type("2000")
        self.assertIn("More than the amount due 1,500.00. Enter the amount again", self.last_text())
        self.type("abc")
        self.assertIn("Enter a valid amount", self.last_text())
        self.type("1.234")
        self.assertIn("Enter a valid amount", self.last_text())
        self.assertEqual((self.step(), self.payload()["cash"]), ("amount", "0"))

    def test_cash_plus_transfer_with_a_voucher_and_a_discount(self):
        self.to_invoices()
        self.pick("Cash")
        self.type("1000")
        self.assertEqual(self.step(), "more")
        self.assertIn("Received 1,000.00 · due 500.00", self.last_text())
        self.assertEqual([item["label"] for item in self.buttons()], ["Add a method", "Discount", "Collect later", "Cancel"])
        self.tap("a=col&s=more&v=method")
        self.assertNotIn("Cash", [item["label"] for item in self.buttons()])  # 现金已用过
        self.tap("a=col&s=method&v=BB")
        self.type("400")
        self.assertEqual((self.step(), self.last_text()), ("voucher", "Send a photo of the transfer slip"))
        self.type("ok")  # 等凭证时打字：再请发照片
        self.assertEqual(self.last_text(), "Send a photo of the transfer slip")
        flow.receive_voucher("U1", "a@x", {"type": "image", "id": "M1"}, "r-img")
        path, kwargs = self.enqueued[-1]
        self.assertEqual((path, kwargs["prefix"], LINE.loading), ("pearnly_erp_portal.integrations.line_collection.store_voucher",
                                                                  "BB", ["U1"]))
        flow.store_voucher(**{key: value for key, value in kwargs.items() if key not in ("queue", "enqueue_after_commit")})
        self.assertEqual(self.step(), "more")
        voucher = self.payload()["methods"][0]["voucher"]
        self.assertTrue(voucher)
        self.assertEqual((T.S.table("File")[voucher]["owner"], T.S.table("File")[voucher]["is_private"]), ("a@x", 1))
        self.tap("a=col&s=more&v=discount")
        self.type("100")
        self.assertEqual(self.step(), "card")
        frappe.session.user = "a@x"
        card = service.card(self.payload()["name"])
        frappe.session.user = "Guest"
        self.assertTrue(card["ready"])
        self.assertEqual((card["cash"], card["transfer"], card["discount"], card["total"]),
                         ("1000.00", "400.00", "100.00", "1500.00"))
        self.assertEqual([(row["prefix"], row["amount"], bool(row["voucher"])) for row in card["methods"]],
                         [("BB", "400.00", True), ("DC", "100.00", False)])

    def test_collect_later_with_the_balance_on_a_chosen_invoice(self):
        self.to_invoices()
        self.pick("Cash")
        self.type("1200")
        self.tap("a=col&s=more&v=later")
        self.assertEqual(self.step(), "split")
        self.assertEqual([item["label"] for item in self.buttons()], ["Oldest first", "IV1", "IV2", "Enter each", "Cancel"])
        self.tap("a=col&s=split&v=IV1")
        self.assertEqual(self.step(), "card")
        rows = {row["docnum"]: str(row["amount"]) for row in T.S.table("ERP Collection Invoice").values()}
        self.assertEqual(rows, {"IV1": "700.00", "IV2": "500.00"})

    def test_collect_later_oldest_first(self):
        self.to_invoices()
        self.pick("Cash")
        self.type("1200")
        self.tap("a=col&s=more&v=later")
        self.tap("a=col&s=split&v=%40oldest")
        rows = {row["docnum"]: str(row["amount"]) for row in T.S.table("ERP Collection Invoice").values()}
        self.assertEqual(rows, {"IV1": "1000.00", "IV2": "200.00"})

    def test_collect_later_each_invoice_typed_and_a_wrong_total_starts_again(self):
        self.to_invoices()
        self.pick("Cash")
        self.type("1200")
        self.tap("a=col&s=more&v=later")
        self.tap("a=col&s=split&v=%40each")
        self.assertIn("Enter the amount for IV1", self.last_text())
        self.type("100")
        self.type("100")
        self.assertIn("Total 200.00 does not match 1,200.00", self.last_text())
        self.assertEqual((self.step(), self.payload()["at"]), ("each", 0))
        self.type("1100")
        self.assertIn("More than the amount due 1,000.00", self.last_text())
        self.type("900")
        self.assertIn("300.00", [item["label"] for item in self.buttons()])  # 最后一张给出剩下的那个数
        self.type("300")
        self.assertEqual(self.step(), "card")
        rows = {row["docnum"]: str(row["amount"]) for row in T.S.table("ERP Collection Invoice").values()}
        self.assertEqual(rows, {"IV1": "900.00", "IV2": "300.00"})

    def test_a_single_invoice_partly_collected_goes_straight_to_the_card(self):
        self.to_invoices(("IV1",))
        self.pick("Cash")
        self.type("250")
        self.tap("a=col&s=more&v=later")
        self.assertEqual(self.step(), "card")
        self.assertEqual([str(row["amount"]) for row in T.S.table("ERP Collection Invoice").values()], ["250.00"])

    def test_a_transfer_without_a_voucher_has_no_confirm_and_asks_for_the_photo(self):
        frappe.session.user = "a@x"
        made = service.create_draft("CO-1", C1, [{"docnum": "IV2", "amount": "500"}],
                                    methods=[{"prefix": "BB", "amount": "500"}])
        frappe.session.user = "Guest"
        self.assertFalse(made["ready"])
        flow._show_card("U1", {}, made, "r-x")
        self.assertEqual([item["label"] for item in self.flex_buttons()], ["Edit", "Withdraw"])
        self.assertEqual((self.step(), self.payload()["voucher_for"]), ("voucher", "BB"))
        self.tap(f"a=col&s=ok&n={made['name']}&ver=1")  # 伪造的【确认】：服务端照样拒
        self.assertEqual(T.S.table("ERP Collection")[made["name"]]["status"], C.DRAFT)
        flow.receive_voucher("U1", "a@x", {"type": "image", "id": "M2"}, "r-img")
        _path, kwargs = self.enqueued[-1]
        flow.store_voucher(**{key: value for key, value in kwargs.items() if key not in ("queue", "enqueue_after_commit")})
        self.assertEqual(self.step(), "card")
        self.assertEqual(self.payload()["version"], 2)
        self.assertIn("Confirm", [item["label"] for item in self.flex_buttons()])


class Wording(Base):
    def test_the_discount_prompt_and_the_next_button_read_naturally(self):
        # 11-12 三星实测：「输入折扣金额的金额」重复，「下一个」应为「下一步」
        self.tap("a=col&s=start")
        self.type(C1)
        self.tap("a=col&s=inv&v=IV1")
        self.assertIn("Next step", [item["label"] for item in self.flex_buttons()])
        self.tap("a=col&s=invoices&v=done")
        self.pick("Cash")
        self.type("900")
        self.pick("Discount")
        self.assertIn("Enter the discount amount", self.last_text())
        self.assertNotIn("Enter the amount for", self.last_text())


class Release(Base):
    def card(self):
        self.to_invoices(("IV1",))
        self.pick("Cash")
        self.type("1000")
        return self.payload()["name"]

    def test_an_invoice_another_collector_took_first_says_already_collected_once(self):
        # 11-12 三星实测：别的收款员先收了同一张，点【确认】回了两遍「超过未收金额，请重新输入」
        name = self.card()
        with mock.patch.object(T.service, "reserved", return_value={"IV1": T.Decimal("1000")}):
            self.tap(self.card_button("Confirm")["data"], event="evt-9")
        text = self.last_text()
        self.assertIn("Already collected. Current balance:", text)
        self.assertIn("IV1 0.00", text)
        self.assertNotIn("More than the amount due", text)
        self.assertEqual(text.count("Already collected"), 1)
        self.assertEqual(T.S.table("ERP Collection")[name]["status"], "draft")

    def test_the_confirm_button_releases_the_shown_version_with_the_event_as_consent(self):
        name = self.card()
        self.tap(self.card_button("Confirm")["data"], event="evt-9")
        record = T.S.table("ERP Collection")[name]
        self.assertEqual((record["status"], record["consent_ref"]), (C.QUEUED, "line:evt-9"))
        self.assertIsNone(SESSIONS.get("U1"))
        path, kwargs = self.enqueued[-1]
        self.assertEqual((path, kwargs["name"], kwargs["reply_token"], kwargs["user"]),
                         ("pearnly_erp_portal.integrations.line_collection.await_result", name, "r-evt-9", "a@x"))
        self.assertTrue(flow.waiting(name))
        self.assertEqual(LINE.loading, ["U1"])

    def test_typed_confirm_words_release_too(self):
        for word in (" OK ", "ยืนยัน", "确认", "確認", "o k"):
            with self.subTest(word=word):
                SESSIONS.clear()
                T.S.table("ERP Collection").clear()
                T.S.table("ERP Collection Invoice").clear()
                T.S.table("ERP Collection Payment").clear()
                name = self.card()
                self.type(word, event="evt-t")
                self.assertEqual(T.S.table("ERP Collection")[name]["status"], C.QUEUED)
        self.assertFalse(flow.is_confirm("okay"))

    def test_other_text_on_the_card_shows_the_card_again(self):
        name = self.card()
        self.type("what")
        self.assertEqual(T.S.table("ERP Collection")[name]["status"], C.DRAFT)
        self.assertIn("Confirm", [item["label"] for item in self.flex_buttons()])

    def test_a_card_changed_after_it_was_shown_is_shown_again_not_released(self):
        name = self.card()
        old = self.card_button("Confirm")["data"]
        frappe.session.user = "a@x"
        service.update_draft(name, 1, [{"docnum": "IV1", "amount": "900"}], cash="900")  # 网页弹窗里改过
        frappe.session.user = "Guest"
        self.tap(old)
        self.assertEqual(T.S.table("ERP Collection")[name]["status"], C.DRAFT)
        self.assertIn("The collection changed. Check it again", self.last_text())
        self.assertEqual(parse_qs(self.card_button("Confirm")["data"])["ver"], ["2"])
        self.type("ok")  # 打字确认用会话里新卡的版本
        self.assertEqual(T.S.table("ERP Collection")[name]["status"], C.QUEUED)

    def test_withdraw_before_it_is_written(self):
        name = self.card()
        self.tap(self.card_button("Withdraw")["data"])
        self.assertEqual(T.S.table("ERP Collection")[name]["status"], C.WITHDRAWN)
        self.assertIn("erp.collection.status.withdrawn", self.last_text())
        self.assertIsNone(SESSIONS.get("U1"))

    def test_withdraw_after_release_from_the_pending_message(self):
        name = self.card()
        self.tap(self.card_button("Confirm")["data"])
        flow._wait_off(name)
        with mock.patch.object(flow, "WAIT_SECONDS", 0):
            flow.await_result(name, "U1", "a@x", "r-wait")
        self.assertIn("Recorded · pending write", self.last_text())
        self.tap(self.buttons()[0]["data"])
        self.assertEqual(T.S.table("ERP Collection")[name]["status"], C.WITHDRAWN)


class Rights(Base):
    def test_no_collector_post_gets_no_steps(self):
        self.tap("a=col&s=start", uid="U9")
        self.assertEqual(self.last_text(), "This account cannot record collections")
        self.assertIsNone(SESSIONS.get("U9"))

    def test_a_forged_button_on_someone_else_s_collection_is_refused_by_the_server(self):
        name = Release.card(self)
        self.tap(f"a=col&s=ok&n={name}&ver=1", uid="U2")
        self.assertEqual(self.last_text(), "This account cannot record collections")
        self.tap(f"a=col&s=wd&n={name}", uid="U2")
        self.assertEqual(self.last_text(), "This account cannot record collections")
        self.assertEqual(T.S.table("ERP Collection")[name]["status"], C.DRAFT)

    def test_losing_the_post_mid_flow_stops_typed_answers(self):
        self.tap("a=col&s=start")
        T.ROLES["a@x"] = set()
        try:
            self.assertTrue(self.type(C1))
            self.assertEqual(self.last_text(), "This account cannot record collections")
        finally:
            T.ROLES["a@x"] = {"collection.write"}


class Results(Base):
    def released(self):
        name = Release.card(self)
        self.tap(self.card_button("Confirm")["data"])
        return name

    def test_written_within_the_wait_is_replied_with_the_receipt_number_and_not_pushed(self):
        name = self.released()
        self.jobs.report("J-" + name, "success", docnum="RE2403080",
                         meta={"invoices": [{"docnum": "IV1", "remaining_after": "0.00"}]})
        self.assertIn(("pearnly_erp_portal.integrations.line_collection.notify", name),
                      [(path, kw.get("name")) for path, kw in self.enqueued])
        flow.notify(name)  # 回执后的推送任务：后台还在等，交给它
        self.assertEqual(LINE.pushes, [])
        flow.await_result(name, "U1", "a@x", "r-wait")
        self.assertEqual(LINE.replies[-1][0], "r-wait")
        self.assertIn("Written to Express: RE2403080", self.last_text())
        flow.notify(name)
        self.assertEqual(LINE.pushes, [])  # 同一结果只说一次

    def test_not_written_within_the_wait_says_pending_then_pushes_the_result_to_the_collector(self):
        name = self.released()
        with mock.patch.object(flow, "WAIT_SECONDS", 0):
            flow.await_result(name, "U1", "a@x", "r-wait")
        self.assertIn("Recorded · pending write", self.last_text())
        self.assertFalse(flow.waiting(name))
        self.jobs.report("J-" + name, "success", docnum="RE2403081")
        flow.notify(name)
        self.assertEqual(len(LINE.pushes), 1)
        to, messages = LINE.pushes[0]
        self.assertEqual(to, "U1")
        self.assertIn("Written to Express: RE2403081", messages[0]["text"])
        flow.notify(name)
        self.assertEqual(len(LINE.pushes), 1)

    def test_waiting_for_express_is_said_in_the_pending_reply_only(self):
        # 等 Express 关闭（约定：`cc_desktop.jobs.book_express_wait`，express-wait-display 11-12）：当场的回复（免费）带现成那句，
        # 不另推送说原因；排队里的原因码不抄到收款记录上
        name = self.released()
        book = T.S.table("ERP Collection")[name]["book_key"]
        self.jobs.report("J-" + name, "queued", reason="ACCOUNT_BUSY_LOCKED")
        self.assertEqual((T.S.table("ERP Collection")[name]["status"], T.S.table("ERP Collection")[name]["reason"]),
                         (C.QUEUED, ""))
        for waiting in (True, False):
            with mock.patch("pearnly_core.cc_desktop.jobs.book_express_wait", create=True,
                            side_effect=lambda key, moment=None: {"waiting": waiting and key == book, "since": None}), \
                    mock.patch.object(flow, "WAIT_SECONDS", 0):
                flow._wait_on(name)
                flow.await_result(name, "U1", "a@x", "r-wait")
            self.assertIn("Recorded · pending write", self.last_text())
            self.assertEqual("express.error.ACCOUNT_BUSY_LOCKED" in self.last_text(), waiting)
        self.assertEqual(LINE.pushes, [])

    def test_already_collected_is_pushed_with_each_current_balance(self):
        name = self.released()
        flow._wait_off(name)
        self.jobs.report("J-" + name, "manual", reason="[ALREADY_COLLECTED] invoice paid",
                         meta={"invoices": [{"docnum": "IV1", "remaining": "200.00"}]})
        flow.notify(name)
        text = LINE.pushes[0][1][0]["text"]
        self.assertIn("Already collected. Current balance:", text)
        self.assertIn("IV1 200.00", text)

    def test_a_plain_failure_says_the_reason(self):
        name = self.released()
        flow._wait_off(name)
        self.jobs.report("J-" + name, "manual", reason="[RECEIPT_TYPE_INVALID] BB has no account")
        flow.notify(name)
        self.assertIn("Not written to Express: BB has no account", LINE.pushes[0][1][0]["text"])

    def test_cc_records_are_not_pushed_to_line(self):
        frappe.session.user = "a@x"
        made = service.create_draft("CO-1", C1, [{"docnum": "IV2", "amount": "500"}], cash="500", source="cc")
        service.release(made["name"], made["version"], "cc:7")
        self.jobs.report("J-" + made["name"], "success", docnum="RE1")
        self.assertNotIn("pearnly_erp_portal.integrations.line_collection.notify", [path for path, _kw in self.enqueued])
        flow.notify(made["name"])
        self.assertEqual(LINE.pushes, [])


class Cards(Base):
    def test_the_confirm_card_repeats_company_book_customer_each_invoice_methods_and_total(self):
        frappe.session.user = "a@x"
        made = service.create_draft("CO-1", C1, [{"docnum": "IV1", "amount": "1000"}, {"docnum": "IV2", "amount": "300"}],
                                    cash="1000", methods=[{"prefix": "DC", "amount": "300"}])
        text = json.dumps(cards.confirm(made, edit_url="https://x"), ensure_ascii=False)
        for part in ("นิภา", "NLTTEST", "2100883 ร้านป้าแดง", "IV1", "1,000.00", "IV2", "300.00", "ส่วนลดเงินสด",
                     "1,300.00"):
            self.assertIn(part, text)
        self.assertLess(len(json.dumps(cards.invoices({"order": [f"IV{i}" for i in range(50)], "picked": [],
                                                        "due": {f"IV{i}": "1000.00" for i in range(50)},
                                                        "dates": {}, "customer": C1}))), 50 * 1024)


class RichMenu(unittest.TestCase):
    def test_the_fourth_tile_is_collections_for_collectors(self):
        from pearnly_erp_portal.integrations import line_menu

        self.assertEqual(line_menu.FUNCTIONS[3], "collection")
        self.assertEqual(len(line_menu.VARIANTS), 15)
        with mock.patch("frappe.utils.get_url", create=True, return_value="https://x"), \
                mock.patch.object(line_menu, "_", lambda text, lang=None: text):
            areas = line_menu.payload("collection", "th")["areas"]
        self.assertEqual(len(areas), 1)
        self.assertEqual((areas[0]["bounds"]["x"], areas[0]["bounds"]["y"]), (0, 843))
        self.assertEqual(areas[0]["action"]["data"], "a=col&s=start")
        roles = {"integration.read", "collection.write"}
        with mock.patch("pearnly_erp_portal.access.guard.has", side_effect=lambda capability, user=None: capability in roles):
            self.assertEqual(line_menu.wanted_variant("a@x"), "collection")
            roles.discard("collection.write")
            self.assertEqual(line_menu.wanted_variant("a@x"), "")
        tiles = pathlib.Path(line_menu.__file__).resolve().parents[1] / "public/images/line-menu/tiles"
        for language in line_menu.LANGUAGES:
            self.assertTrue((tiles / f"collection-{language}.png").is_file())


class BookStep(Base, T.TwoBooks):
    """同期两本账套（A10，11-12）：LINE 没有对话，选完公司问一次用哪本，按公司 × 会计年度记住（与 ERP 单据同一份记忆）。"""

    def test_two_books_are_asked_once_after_the_company_and_the_answer_is_remembered(self):
        self.tap("a=col&s=start")
        self.assertEqual(self.step(), "book")
        self.assertIn("Choose the account set", self.last_text())
        self.assertIn("1. TEST · " + T.TEST26["path"], self.last_text())  # 同名两本：名称 + 位置
        labels = [item["label"] for item in self.buttons()]
        self.assertEqual((len(labels), labels[-1]), (3, "Cancel"))
        self.assertTrue(labels[0].startswith("1. ") and labels[1].startswith("2. "))
        self.tap(self.buttons()[1]["data"])
        self.assertEqual(self.step(), "customer")
        self.type(C1)
        self.assertEqual(self.step(), "invoices")
        self.assertIn(T.snapshot.request_key(T.TEST26_COPY["key"], C1), self.requests.made)
        SESSIONS.clear()
        self.tap("a=col&s=start")
        self.assertEqual(self.step(), "customer")  # 记住了：不再问

    def test_a_book_that_is_not_offered_is_refused(self):
        self.tap("a=col&s=start")
        self.tap("a=col&s=book&v=" + T.TEST25["key"])
        self.assertEqual(self.step(), "book")
        self.assertIn("This option is no longer current", self.last_text())
        self.type("TEST")
        self.assertEqual(self.step(), "book")  # 打字不算选


if __name__ == "__main__":
    unittest.main()
