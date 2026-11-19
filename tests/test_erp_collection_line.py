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
TTLS = []
session_module.set_session = lambda uid, state, payload, ttl_minutes=None: (TTLS.append(ttl_minutes), SESSIONS.__setitem__(
    uid, {"state": state, "payload": json.loads(json.dumps(payload))}))
session_module.clear_session = lambda uid, **kw: SESSIONS.pop(uid, None)


def _consume_nonce(uid, expect_state, nonce):
    """同 `line_session.consume_nonce`：态对、号对才原子清号并回原载荷；否则 None（绝不写）。"""
    sess = SESSIONS.get(uid)
    payload = (sess or {}).get("payload") or {}
    if not sess or sess.get("state") != expect_state or not payload.get("nonce") or payload.get("nonce") != nonce:
        return None
    session_module.set_session(uid, expect_state, {**payload, "nonce": None})
    return payload


session_module.consume_nonce = _consume_nonce
session_module.verify_nonce = lambda sess, nonce, expect_state="reviewing": bool(
    sess and sess.get("state") == expect_state and nonce and (sess.get("payload") or {}).get("nonce") == nonce)
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
        # 共用通道的「在等 Express 关闭」（express-wait-display 11-12 的约定，那支未合入前用替身）：缺省不在等
        waits = mock.patch("pearnly_core.cc_desktop.jobs.book_express_wait", create=True,
                           side_effect=lambda key, moment=None: {"waiting": False, "since": None})
        waits.start()
        self.addCleanup(waits.stop)
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
        action = next(item for item in self.buttons() if item["label"] == label or item["label"].endswith(". " + label))
        self.tap(action["data"])

    def card_button(self, label):
        return next(item for item in self.flex_buttons() if item["label"] == label)


class Steps(Base):
    def test_choosing_the_company_warms_up_its_receivables_after_asking_for_the_customer(self):
        """11-15 真机：首次读发票要点「刷新」。定下公司就请电脑预热（`service.warm`），在问客户之后排，不挡回复。"""
        with mock.patch.object(service, "warm") as warm:
            self.tap("a=col&s=start")
        self.assertEqual(self.step(), "customer")
        warm.assert_called_once_with(self.payload()["company"], None)

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
        # 11-13：方式名开头相同（เงินโอนเข้าบัญชี SCB/…），截到 20 字后原来两两一样；现在序号 + 不同的那段，列表里写全名
        self.assertEqual([item["label"] for item in self.buttons()],
                         ["1. Cash", "2. …SCB/ออมทรัพย์", "3. …BBL/ออมทรัพย์", "4. …BBL/กระแส", "5. …SCB/กระแส",
                          "6. …KB/CA", "Cancel"])
        self.assertIn("2. เงินโอนเข้าบัญชี SCB/ออมทรัพย์", self.last_text())
        self.pick("Cash")
        self.assertEqual((self.step(), self.buttons()[0]["label"]), ("amount", "1,500.00"))
        self.type("1,500")
        self.assertEqual(self.step(), "card")
        name = self.payload()["name"]
        record = T.S.table("ERP Collection")[name]
        self.assertEqual((record["status"], str(record["cash"]), record["source"], record["collector"]),
                         (C.DRAFT, "1500.00", "line", "a@x"))
        self.assertEqual([item["label"] for item in self.flex_buttons()], ["Save", "Edit", "Cancel"])
        self.assertEqual(parse_qs(self.card_button("Save")["data"]),
                         {"a": ["col"], "s": ["ok"], "n": [name], "ver": ["1"], "k": [self.payload()["nonce"]]})
        self.assertTrue(self.card_button("Edit")["uri"].endswith("/desk/pearnly-collections/" + name))  # 11-14 起只有「收款」一页
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
        for state, text in (("offline", "No computer connected"), ("outdated", "CC Desktop needs an update")):
            with mock.patch.object(T.snapshot, "request", return_value=state):
                self.tap("a=col&s=start")
                self.type("99001")
                self.assertIn(text, self.last_text())
                self.assertNotIn("No open invoices", self.last_text())
                self.pick("Cancel")

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
        made = service.listed("CO-1")["items"][0]
        service.release(made["name"], made["version"], "line:other")
        frappe.session.user = "Guest"
        self.tap("a=col&s=start")
        self.type(C1)
        self.assertEqual(self.payload()["due"], {"IV1": "400.00", "IV2": "500.00"})

    def test_stale_buttons_from_another_step_ask_the_current_step_again(self):
        # 卡片有效期 A7：旧步骤的按钮不跳步，吃下、照当前这一步再问
        self.tap("a=col&s=start")
        before = dict(self.payload())
        self.tap("a=col&s=method&v=%40cash")
        self.assertEqual((self.last_text(), self.step()), ("Type the customer code or part of the name", "customer"))
        self.assertEqual(self.payload(), before)
        self.to_invoices(("IV1",))
        self.tap("a=col&s=customer&v=" + C1)  # 方式那一步点了客户按钮：再问方式
        self.assertEqual(self.step(), "method")
        self.assertIn("Choose the collection method", self.last_text())
        self.pick("Cash")
        self.type("1000")
        self.assertEqual(self.step(), "card")
        self.tap("a=col&s=more&v=later")  # 卡片那一步点了旧按钮：重出当前卡
        self.assertEqual(self.step(), "card")
        self.assertIn("Save", [item["label"] for item in self.flex_buttons()])

    def test_an_old_cancel_asks_the_current_step_again(self):
        self.tap("a=col&s=start")
        old_cancel = next(item["data"] for item in self.buttons() if item["label"] == "Cancel")
        self.type(C1)
        self.tap(old_cancel)
        self.assertEqual(self.step(), "invoices")
        self.assertIn("Next step", [item["label"] for item in self.flex_buttons()])

    def test_a_button_after_the_session_ended_says_expired(self):
        self.tap("a=col&s=start")
        SESSIONS.clear()
        self.tap("a=col&s=customer&v=" + C1)
        self.assertEqual(self.last_text(), "This item has expired. Please start again.")

    def test_cancel_clears_the_session(self):
        self.tap("a=col&s=start")
        self.pick("Cancel")
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
        self.assertEqual([item["label"] for item in self.flex_buttons()], ["Edit", "Cancel"])
        self.assertEqual((self.step(), self.payload()["voucher_for"]), ("voucher", "BB"))
        self.tap(f"a=col&s=ok&n={made['name']}&ver=1")  # 伪造的【确认】：服务端照样拒
        self.assertEqual(T.S.table("ERP Collection")[made["name"]]["status"], C.DRAFT)
        flow.receive_voucher("U1", "a@x", {"type": "image", "id": "M2"}, "r-img")
        _path, kwargs = self.enqueued[-1]
        flow.store_voucher(**{key: value for key, value in kwargs.items() if key not in ("queue", "enqueue_after_commit")})
        self.assertEqual(self.step(), "card")
        self.assertEqual(self.payload()["version"], 2)
        self.assertIn("Save", [item["label"] for item in self.flex_buttons()])


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
            self.tap(self.card_button("Save")["data"], event="evt-9")
        text = self.last_text()
        self.assertIn("Already collected. Current balance:", text)
        self.assertIn("IV1 0.00", text)
        self.assertNotIn("More than the amount due", text)
        self.assertEqual(text.count("Already collected"), 1)
        self.assertEqual(T.S.table("ERP Collection")[name]["status"], "draft")

    def test_the_confirm_button_releases_the_shown_version_with_the_event_as_consent(self):
        name = self.card()
        self.tap(self.card_button("Save")["data"], event="evt-9")
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
        self.assertIn("Save", [item["label"] for item in self.flex_buttons()])

    def test_a_card_changed_after_it_was_shown_is_shown_again_not_released(self):
        name = self.card()
        old = self.card_button("Save")["data"]
        frappe.session.user = "a@x"
        service.update_draft(name, 1, [{"docnum": "IV1", "amount": "900"}], cash="900")  # 网页弹窗里改过
        frappe.session.user = "Guest"
        self.tap(old)
        self.assertEqual(T.S.table("ERP Collection")[name]["status"], C.DRAFT)
        self.assertIn("The collection changed. Check it again", self.last_text())
        self.assertEqual(parse_qs(self.card_button("Save")["data"])["ver"], ["2"])
        self.type("ok")  # 打字确认用会话里新卡的版本
        self.assertEqual(T.S.table("ERP Collection")[name]["status"], C.QUEUED)

    def test_withdraw_before_it_is_written(self):
        name = self.card()
        self.tap(self.card_button("Cancel")["data"])
        self.assertEqual(T.S.table("ERP Collection")[name]["status"], C.WITHDRAWN)
        self.assertIn("erp.collection.status.withdrawn", self.last_text())
        self.assertIsNone(SESSIONS.get("U1"))

    def test_withdraw_after_release_from_the_pending_message(self):
        name = self.card()
        self.tap(self.card_button("Save")["data"])
        flow._wait_off(name)
        with mock.patch.object(flow, "WAIT_SECONDS", 0):
            flow.await_result(name, "U1", "a@x", "r-wait")
        self.assertIn("Recorded · pending write", self.last_text())
        self.assertEqual([item["label"] for item in self.buttons()], ["View result", "Withdraw"])
        withdraw = self.buttons()[1]["data"]
        self.tap(withdraw)
        self.assertEqual(T.S.table("ERP Collection")[name]["status"], C.WITHDRAWN)
        before = len(LINE.replies)
        self.tap(withdraw)  # 同一个撤回号再点：不回话
        self.assertEqual(len(LINE.replies), before)


class Rights(Base):
    def test_no_collector_post_gets_no_steps(self):
        self.tap("a=col&s=start", uid="U9")
        self.assertEqual(self.last_text(), "This account cannot record collections")
        self.assertIsNone(SESSIONS.get("U9"))

    def test_a_forged_button_on_someone_else_s_collection_is_refused_by_the_server(self):
        name = Release.card(self)
        nonce = self.payload()["nonce"]
        for data in (f"a=col&s=ok&n={name}&ver=1", f"a=col&s=wd&n={name}", f"a=col&s=ok&n={name}&ver=1&k={nonce}",
                     f"a=col&s=wd&n={name}&k={nonce}", f"a=col&s=wd&n={name}&p=1&k={nonce}"):
            self.tap(data, uid="U2")  # 别人的会话里没有这张卡：不认；不是他录的，旧卡 / 伪造的点了也不作废它
            self.assertIn(self.last_text(), ("This item has expired. Please start again.",
                                             "This account cannot record collections"))
        self.assertEqual(T.S.table("ERP Collection")[name]["status"], C.DRAFT)
        self.assertEqual(self.payload()["nonce"], nonce)

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
        self.tap(self.card_button("Save")["data"])
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

    def test_written_after_the_wait_is_not_pushed_and_is_told_by_reply(self):
        # 11-13 用户定：写成不推送。「已记录 · 待写入」带【查看结果】；下次进「收款」先用回复列出上次之后写成 / 失败的
        name = self.released()
        with mock.patch.object(flow, "WAIT_SECONDS", 0):
            flow.await_result(name, "U1", "a@x", "r-wait")
        self.assertIn("Recorded · pending write", self.last_text())
        self.assertFalse(flow.waiting(name))
        view_result = self.buttons()[0]
        self.assertEqual((view_result["label"], parse_qs(view_result["data"])["s"]), ("View result", ["res"]))
        self.tap(view_result["data"], event="evt-r1")  # 还没写成：照实再说一遍待写入
        self.assertIn("Recorded · pending write", self.last_text())
        self.jobs.report("J-" + name, "success", docnum="RE2403081")
        flow.notify(name)
        self.assertEqual(LINE.pushes, [])  # 成功路径 0 条推送
        self.tap(view_result["data"], event="evt-r2")
        self.assertEqual(LINE.replies[-1][0], "r-evt-r2")
        self.assertIn("Written to Express: RE2403081", self.last_text())
        self.tap("a=col&s=start", event="evt-m1")  # 看过了：进菜单不再列
        self.assertNotIn("RE2403081", self.last_text())
        self.assertEqual(LINE.pushes, [])

    def test_the_menu_lists_what_was_written_or_failed_since_last_time_once(self):
        name = self.released()
        with mock.patch.object(flow, "WAIT_SECONDS", 0):
            flow.await_result(name, "U1", "a@x", "r-wait")
        self.jobs.report("J-" + name, "success", docnum="RE2403082")
        flow.notify(name)
        self.tap("a=col&s=start", event="evt-m1")
        self.assertEqual(LINE.replies[-1][0], "r-evt-m1")  # 用回复，不推送
        text = self.last_text()
        self.assertIn("Written to Express: RE2403082", text)
        self.assertIn("Type the customer code or part of the name", text)  # 接着照常问客户
        self.tap("a=col&s=start", event="evt-m2")
        self.assertNotIn("RE2403082", self.last_text())
        self.assertEqual(LINE.pushes, [])

    def test_a_failure_is_pushed_once_and_not_after_it_was_seen(self):
        name = self.released()
        with mock.patch.object(flow, "WAIT_SECONDS", 0):
            flow.await_result(name, "U1", "a@x", "r-wait")
        self.jobs.report("J-" + name, "manual", reason="[WRITE_FAILED] BB has no account")
        flow.notify(name)
        flow.notify(name)
        self.assertEqual(len(LINE.pushes), 1)  # 失败路径最多 1 条
        self.assertIn("Not written to Express: BB has no account", LINE.pushes[0][1][0]["text"])
        self.tap("a=col&s=start", event="evt-m1")
        self.assertNotIn("BB has no account", self.last_text())  # 推过了：菜单不再列
        other = Release.card(self)
        self.tap(self.card_button("Save")["data"], event="evt-c2")
        with mock.patch.object(flow, "WAIT_SECONDS", 0):
            flow.await_result(other, "U1", "a@x", "r-wait2")
        self.tap(self.buttons()[0]["data"], event="evt-r")
        self.jobs.report("J-" + other, "manual", reason="[WRITE_FAILED] seen first")
        self.tap(self.buttons()[0]["data"], event="evt-r2")  # 收款员先点【查看结果】看到了失败
        self.assertIn("seen first", self.last_text())
        flow.notify(other)
        self.assertEqual(len(LINE.pushes), 1)  # 看过的不推

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



class Returned(Base):
    """写前被电脑拒（一行没写，主会话 11-14 审定 7 个码）：原卡带原因还回、换新确认号（卡片有效期规则 A9）。"""

    def released(self):
        name = Release.card(self)
        self.old = self.card_button("Save")["data"]
        self.tap(self.old)
        return name

    def refuse(self, name, reason="[already_collected] invoice paid", job=None):
        flow._wait_off(name)
        self.jobs.report(job or "J-" + name, "manual", reason=reason,
                         meta={"invoices": [{"docnum": "IV1", "remaining": "200.00"}]})
        flow.notify(name)

    def test_the_card_comes_back_with_each_current_balance_and_a_new_confirm(self):
        name = self.released()
        self.refuse(name)
        self.assertEqual(LINE.replies[-1][0], "push:U1")
        self.assertIn("Already collected. Current balance:", self.last_text())  # CC 的小写码对上了（11-14 前对不上）
        self.assertIn("IV1 200.00", self.last_text())
        self.assertEqual((self.step(), self.payload()["name"], self.payload()["version"]), ("card", name, 2))
        # 余额不够这次收：卡上照规则不出【确认】，改了（卡片【编辑】/ 网页 / CC）再确认；确认号已换新
        self.assertEqual([item for item in self.flex_buttons() if item["label"] == "Save"], [])
        self.assertNotIn(self.payload()["nonce"], self.old)
        replies = len(LINE.replies)
        self.tap(self.old, event="evt-old")  # 用过的旧卡：不回话、不放行
        self.assertEqual((len(LINE.replies), T.S.table("ERP Collection")[name]["status"]), (replies, C.DRAFT))
        flow.notify(name)
        self.assertEqual(len(LINE.replies), replies)  # 同一次退回只还一次

    def test_a_returned_card_can_be_released_again_as_a_new_write(self):
        name = self.released()
        self.refuse(name, "[receipt_type_invalid] TR has no account")  # Express 里把方式设好了：原样再确认
        self.assertIn("Not written to Express: TR has no account", self.last_text())
        self.tap(self.card_button("Save")["data"], event="evt-new")
        self.assertEqual(T.S.table("ERP Collection")[name]["status"], C.QUEUED)
        self.assertEqual(self.jobs.rows["J-" + name + "-1"]["request_key"], "erp:receipt:" + name + ":1")
        self.refuse(name, job="J-" + name)  # 上一件迟到的回执：不改、不还回
        self.assertEqual(T.S.table("ERP Collection")[name]["status"], C.QUEUED)
        self.refuse(name, job="J-" + name + "-1")  # 第二次被拒（这一件）：照样还回
        self.assertEqual((self.step(), self.payload()["version"]), ("card", 3))

    def test_a_collector_busy_with_another_collection_only_hears_the_reason(self):
        name = self.released()
        self.tap("a=col&s=start", event="evt-m")  # 已经在录下一笔
        self.type(C1, event="evt-c")
        before = (self.step(), self.payload().get("name"))
        self.refuse(name)
        self.assertIn("Already collected. Current balance:", self.last_text())
        self.assertEqual((self.step(), self.payload().get("name")), before)  # 正在录的那笔不动
        self.assertEqual(self.flex_buttons(), [])
        self.assertEqual(T.S.table("ERP Collection")[name]["status"], C.DRAFT)  # 草稿留着：网页、CC 照样能放

    def test_while_waiting_the_returned_card_is_the_reply(self):
        name = Release.card(self)
        self.tap(self.card_button("Save")["data"])
        self.jobs.report("J-" + name, "manual", reason="[cash_account_missing] no cash account")
        flow.notify(name)
        self.assertEqual(LINE.pushes, [])  # 后台还在等：交给它
        flow.await_result(name, "U1", "a@x", "r-wait")
        self.assertEqual(LINE.replies[-1][0], "r-wait")
        self.assertIn("Not written to Express: no cash account", self.last_text())
        self.assertEqual(self.step(), "card")

    def test_the_same_draft_is_seen_alike_in_line_the_web_list_and_cc(self):
        """网页「收款」页、CC 对话、LINE 三处看到的是同一条草稿、同一个状态与版本（主会话 11-14）。"""
        from pearnly_erp_portal.collection import api, cc_tools

        name = self.released()
        self.refuse(name)
        frappe.session.user = "a@x"
        web = api.api_collection(name)  # 「收款」页点开那一笔
        opened = api.api_collection_card(name)["card"]
        chat = json.loads(cc_tools.status({"collection": name}))
        seen = {(row["status"], row["version"], row["returned"], row["reason"]) for row in (web, opened, chat)}
        self.assertEqual(seen, {(C.DRAFT, 2, True, "[already_collected] invoice paid")})
        self.assertEqual(self.payload()["version"], 2)  # LINE 新卡上的也是这个版本

    def test_a_plain_failure_says_the_reason(self):
        name = self.released()
        flow._wait_off(name)
        self.jobs.report("J-" + name, "manual", reason="[WRITE_FAILED] BB has no account")
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


EXPIRED = "This item has expired. Please start again."


class AccountLabels(Base):
    """收款方式按钮换成「银行简称 + 账号末 4 位」（CC 桌面版上报 receipt_types[].bank_account / uses，契约
    `tests/test_companion_contract.py::ReceiptContract::test_receipt_types_carry_their_bank_account_and_uses`，11-13）。"""

    ACCOUNTS = {"TR": ({"code": "S1", "bank": "ธนาคารไทยพาณิชย์", "short": "SCB/SA", "number": "123-4-56789", "via": "shortnam"}, 40),
                "SC": ({"code": "S1", "bank": "ธนาคารไทยพาณิชย์", "short": "SCB/SA", "number": "123-4-56789", "via": "account"}, 900),
                "BB": ({"code": "B1", "bank": "กรุงเทพ", "short": "", "number": "", "via": "shortnam"}, 0),
                "TB": ({"code": "B2", "bank": "", "short": "", "number": "777-0-01234", "via": "shortnam"}, 0)}

    def setUp(self):
        super().setUp()
        rows = []
        for row in T.TYPES:
            found = self.ACCOUNTS.get(row["prefix"])
            rows.append(dict(row, bank_account=found[0], uses=found[1]) if found else dict(row, uses=0))
        patcher = mock.patch.dict(T.FACTS, {"receipt_types": rows})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_method_buttons_show_the_bank_and_the_last_four_digits(self):
        self.to_invoices(("IV1",))
        labels = [item["label"] for item in self.buttons()]
        # TR、SC 进同一户头 S1：只列原生用得多的 SC；BB 没账号：只写银行名；TB 户头没名字：照旧方式名；KB 没报户头：方式名
        self.assertEqual(labels, ["1. Cash", "2. กรุงเทพ", "3. …BBL/กระแส", "4. SCB/SA ·6789", "5. …KB/CA", "Cancel"])
        self.assertIn("4. SCB/SA ·6789", self.last_text())
        self.assertNotIn("TR", [parse_qs(item["data"]).get("v", [""])[0] for item in self.buttons()])
        self.pick("SCB/SA ·6789")
        self.assertIn("Enter the amount for SCB/SA ·6789", self.last_text())
        self.type("1000")
        flow.receive_voucher("U1", "a@x", {"type": "image", "id": "M1"}, "r-img")
        _path, kwargs = self.enqueued[-1]
        flow.store_voucher(**{key: value for key, value in kwargs.items() if key not in ("queue", "enqueue_after_commit")})
        card = json.dumps(self.last(), ensure_ascii=False)
        self.assertIn("SCB/SA ·6789", card)  # 确认卡上的方式行同一个名字（11-13 真机：卡上仍是方式名）
        self.assertNotIn("เงินโอนเข้าบัญชี SCB/กระแส", card)

    def test_the_edit_dialog_still_lists_every_method(self):
        frappe.session.user = "a@x"
        made = service.create_draft("CO-1", C1, [{"docnum": "IV2", "amount": "500"}], methods=[{"prefix": "TR", "amount": "500"}])
        prefixes = [row["prefix"] for row in service.edit_choices(made["name"])["methods"]["transfers"]]
        frappe.session.user = "Guest"
        self.assertIn("TR", prefixes)  # 草稿里用了被收起的那种：改草稿的弹窗照样有它
        self.assertIn("SC", prefixes)

    def test_label_rules(self):
        label = C.account_label
        self.assertEqual(label({"name": "x", "bank_account": {"short": " กรุงศรี ", "bank": "กรุงศรีอยุธยา", "number": "151-1-43016"}}),
                         "กรุงศรี ·3016")
        self.assertEqual(label({"name": "x", "bank_account": {"short": "", "bank": "K Bank", "number": "12"}}), "K Bank ·12")
        self.assertEqual(label({"name": "เงินโอน", "bank_account": None}), "เงินโอน")
        tie = C.listed([{"code": "12", "prefix": "B", "name": "b", "uses": 3, "bank_account": {"code": "A", "short": "X"}},
                        {"code": "05", "prefix": "A", "name": "a", "uses": 3, "bank_account": {"code": "A", "short": "X"}}])
        self.assertEqual([row["prefix"] for row in tie], ["A"])  # 次数平了取编号小的



class CardLife(Base):
    """确认卡的有效期（共享笔记「卡片有效期规则-对照与整改-11-13」B 节，照 DMS：一卡一个一次性确认号、出新卡旧卡作废、120 分钟）。"""

    def card(self, docnum="IV1", amount="1000"):
        self.tap("a=col&s=start")
        self.type(C1)
        self.tap(f"a=col&s=inv&v={docnum}")
        self.tap("a=col&s=invoices&v=done")
        self.pick("Cash")
        self.type(amount)
        self.assertEqual(self.step(), "card")
        return self.payload()["name"], self.card_button("Save")["data"], self.card_button("Cancel")["data"]

    def status(self, name):
        return T.S.table("ERP Collection")[name]["status"]

    def test_an_old_card_cannot_write_again_after_a_new_collection_was_started(self):
        # B1：重开一笔后点旧卡【确认】——版本对得上、余额够，原来会再写一张
        old, old_ok, old_wd = self.card()
        new, _ok, _wd = self.card(docnum="IV2", amount="500")
        self.assertEqual(self.status(old), C.WITHDRAWN)  # 旧草稿作废
        before = dict(SESSIONS["U1"]["payload"])
        self.tap(old_ok, event="evt-old")
        self.assertEqual(self.last_text(), EXPIRED)
        self.assertEqual((self.status(old), self.status(new)), (C.WITHDRAWN, C.DRAFT))
        self.assertEqual(SESSIONS["U1"]["payload"], before)  # 会话不被改回旧草稿
        self.tap(old_wd, event="evt-old-wd")
        self.assertEqual((self.last_text(), self.status(new)), (EXPIRED, C.DRAFT))  # B3：旧卡撤不掉新的
        self.assertNotIn("pearnly_erp_portal.integrations.line_collection.await_result",
                         [path for path, _kw in self.enqueued])

    def test_an_old_card_does_not_erase_a_collection_being_entered(self):
        # B5：新收款录到一半点旧卡，进度不丢
        old, old_ok, _wd = self.card()
        self.tap("a=col&s=start")
        self.type(C1)
        self.tap("a=col&s=inv&v=IV2")
        self.assertEqual(self.status(old), C.WITHDRAWN)
        before = json.loads(json.dumps(SESSIONS["U1"]))
        self.tap(old_ok, event="evt-old")
        self.assertEqual(self.last_text(), EXPIRED)
        self.assertEqual(SESSIONS["U1"], before)

    def test_tapping_confirm_twice_writes_once_and_the_second_tap_gets_no_reply(self):
        # B7：连点【确认】
        name, ok, wd = self.card()
        self.tap(ok, event="evt-a")
        self.assertEqual(self.status(name), C.QUEUED)
        replies = len(LINE.replies)
        self.tap(ok, event="evt-b")
        self.tap(wd, event="evt-c")
        self.assertEqual(len(LINE.replies), replies)
        self.assertEqual(self.status(name), C.QUEUED)
        self.assertEqual([path for path, _kw in self.enqueued].count(
            "pearnly_erp_portal.integrations.line_collection.await_result"), 1)

    def test_a_card_shown_again_retires_the_earlier_card_of_the_same_draft(self):
        name, old_ok, old_wd = self.card()
        self.type("what")  # 卡片那一步打了别的字：重出卡，换新号
        new_ok = self.card_button("Save")["data"]
        self.assertNotEqual(new_ok, old_ok)
        self.tap(old_ok, event="evt-old")
        self.assertEqual((self.last_text(), self.status(name)), (EXPIRED, C.DRAFT))  # 同一笔的新卡还在：草稿不作废
        self.tap(old_wd, event="evt-old-wd")
        self.assertEqual(self.status(name), C.DRAFT)
        self.tap(new_ok, event="evt-new")
        self.assertEqual(self.status(name), C.QUEUED)

    def test_the_withdraw_on_the_current_card_works(self):
        name, _ok, wd = self.card()
        self.tap(wd)
        self.assertEqual(self.status(name), C.WITHDRAWN)
        self.assertIsNone(SESSIONS.get("U1"))

    def test_a_card_is_valid_for_120_minutes_and_then_its_draft_is_discarded(self):
        name, ok, _wd = self.card()
        self.assertEqual(TTLS[-1], 120)  # 卡片那一步的会话活 120 分钟（其余步 30 分钟续期照旧）
        issued = T.datetime.fromisoformat(self.payload()["issued"])
        with mock.patch.object(frappe.utils, "now_datetime", return_value=issued + T.timedelta(minutes=121)):
            self.tap(ok, event="evt-late")
        self.assertEqual((self.last_text(), self.status(name)), (EXPIRED, C.WITHDRAWN))
        self.assertIsNone(SESSIONS.get("U1"))
        self.type("ok")  # 打字确认也不行
        self.assertEqual(self.status(name), C.WITHDRAWN)

    def test_an_expired_session_retires_the_card_and_its_draft(self):
        name, ok, _wd = self.card()
        SESSIONS.clear()  # 会话过期
        self.tap(ok, event="evt-late")
        self.assertEqual((self.last_text(), self.status(name)), (EXPIRED, C.WITHDRAWN))

    def test_the_document_date_is_the_day_it_is_released(self):
        # B2：隔天确认，单据日期原来是建草稿那天
        name, ok, _wd = self.card()
        self.assertEqual(str(T.S.table("ERP Collection")[name]["doc_date"]), "2026-11-11")
        with mock.patch.object(service, "nowdate", return_value="2026-11-12"):
            self.tap(ok, event="evt-next-day")
        record = T.S.table("ERP Collection")[name]
        self.assertEqual((record["status"], str(record["doc_date"])), (C.QUEUED, "2026-11-12"))

    def test_cancel_only_from_the_current_step_and_it_discards_the_draft(self):
        # B6：【取消】原来清整个会话、不撤草稿；旧消息上的【取消】也会抹掉正在录的
        self.tap("a=col&s=start")
        old_cancel = next(item["data"] for item in self.buttons() if item["label"] == "Cancel")
        self.type(C1)
        self.tap("a=col&s=inv&v=IV1")
        self.tap("a=col&s=invoices&v=done")
        self.tap(old_cancel)
        self.assertEqual(self.step(), "method")  # 旧的【取消】不动当前这一步，照它再问
        self.assertIn("Choose the collection method", self.last_text())
        frappe.session.user = "a@x"
        made = service.create_draft("CO-1", C1, [{"docnum": "IV2", "amount": "500"}],
                                    methods=[{"prefix": "BB", "amount": "500"}])
        frappe.session.user = "Guest"
        flow._show_card("U1", {}, made, "r-x")  # 缺凭证：卡上等照片
        self.assertEqual(self.step(), "voucher")
        self.pick("Cancel")
        self.assertEqual(self.last_text(), "Cancelled")
        self.assertIsNone(SESSIONS.get("U1"))
        self.assertEqual(self.status(made["name"]), C.WITHDRAWN)  # 没放行的草稿一并作废

    def test_the_card_color_is_its_own(self):
        from pearnly_erp_portal.integrations import line_cards

        self.card()
        header = next(m for m in self.last() if m.get("type") == "flex")["contents"]["header"]
        colors = line_cards.COLORS
        self.assertEqual(header["backgroundColor"], colors["collection"])
        self.assertEqual(len({colors["purchase"], colors["sales"], colors["collection"]}), 3)


class VoucherLanguage(Base):
    """11-13 真机：发凭证图后的那条回复变泰文。后台任务的默认语言是站点语言（泰文）：语言要在换身份之后设，没带就按 LINE 现取。"""

    def setUp(self):
        super().setUp()
        tagged = lambda text, *a, **k: f"[{frappe.local.lang}]{text}"  # noqa: E731
        for module in (flow, cards):
            patcher = mock.patch.object(module, "_", tagged)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_voucher(self, language):
        self.to_invoices(("IV2",))
        self.tap(next(item["data"] for item in self.buttons() if "BB" in item["data"]))
        self.type("500")
        self.assertEqual(self.step(), "voucher")
        flow.receive_voucher("U1", "a@x", {"type": "image", "id": "M1"}, "r-img")
        _path, kwargs = self.enqueued[-1]
        kwargs = {key: value for key, value in kwargs.items() if key not in ("queue", "enqueue_after_commit")}
        kwargs["language"] = language
        worker = mock.patch.object(frappe, "set_user", side_effect=lambda user: (
            setattr(frappe.session, "user", user), setattr(frappe.local, "lang", "th")))
        with worker:  # 后台任务：换身份时语言回到站点语言
            frappe.local.lang = "th"
            if getattr(self, "job_user", None):  # 后台任务的 job（排任务时的会话用户，这一任务里还没定过语言）
                frappe.local.job = types.SimpleNamespace(user=self.job_user, lang_resolved=False)
            try:
                flow.store_voucher(**kwargs)
            finally:
                frappe.local.__dict__.pop("job", None)
        texts = json.dumps(self.last(), ensure_ascii=False)
        self.assertIn("[zh]", texts)
        self.assertNotIn("[th]", texts)

    def test_the_reply_after_the_voucher_keeps_the_user_language(self):
        self.run_voucher("zh")

    def test_without_a_language_it_asks_line_not_the_site_default(self):
        self.run_voucher("")

    def test_the_first_translation_in_a_job_does_not_reset_the_language(self):
        # 11-13 真机取证（共享笔记「收款-凭证后语言-取证-11-13」）：锁定版 Frappe 的 `_()` 在后台任务里第一次调用时按排任务的会话用户
        # （webhook 是 Guest → 站点语言泰文）重设 local.lang，盖掉我们设的语言。这里照它的行为模拟
        def frappe_like(text, *a, **k):
            job = getattr(frappe.local, "job", None)
            if job is not None and not job.lang_resolved:
                frappe.local.lang = "th"
                job.lang_resolved = True
            return f"[{frappe.local.lang}]{text}"

        for module in (flow, cards):
            patcher = mock.patch.object(module, "_", frappe_like)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.job_user = "Guest"
        self.run_voucher("zh")


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
        # 同目录名同年度（副本与真账）：按钮上显示位置里不同的那段，序号后面互不相同（11-13 真机：截到 20 字后两个一样）
        self.assertTrue(labels[0].startswith("1. ") and labels[1].startswith("2. "))
        self.assertNotEqual(labels[0][3:], labels[1][3:])
        self.assertTrue(all(len(label) <= 20 for label in labels))
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


class DayStep(Base, T.TwoBooks):
    """今天不在这家任何一本账套的期间里（11-15 线上 นิว ลิงค์）：选公司后问收款日期（佛历，同卡片），快捷回复是各本期间的最后一天，
    也可以打日期；账套跟着日期走，确认卡写出收款日期，写入用这个日期。"""

    def setUp(self):
        super().setUp()
        self.books = [T.OLD]
        T.snapshot.replace("CO-1", T.OLD["key"], T.OPEN, "2026-11-11T03:00:00+00:00")

    def test_the_date_is_asked_and_carried_to_the_card(self):
        self.tap("a=col&s=start")
        self.assertEqual(self.step(), "day")
        self.assertIn("Collection Date", self.last_text())
        self.assertIn("Accounting period: 01/01/2567–31/12/2568 OLD", self.last_text())
        self.assertEqual([item["label"] for item in self.buttons()], ["31/12/2567 OLD", "Cancel"])
        self.tap(self.buttons()[0]["data"])
        self.assertEqual((self.step(), self.payload()["day"]), ("customer", "2024-12-31"))
        self.type(C1)
        self.assertEqual(self.step(), "invoices")
        self.assertIn(T.snapshot.request_key(T.OLD["key"], C1), self.requests.made)
        self.tap("a=col&s=inv&v=IV1")
        self.tap("a=col&s=invoices&v=done")
        self.tap("a=col&s=method&v=@cash")
        self.type("1000")
        self.assertEqual(self.step(), "card")
        row = T.S.table("ERP Collection")[self.payload()["name"]]
        self.assertEqual((str(row["doc_date"]), row["book_key"]), ("2024-12-31", T.OLD["key"]))
        self.assertIn("31/12/2567", str(self.last()))

    def test_a_typed_date_is_read_and_a_wrong_one_is_asked_again(self):
        self.tap("a=col&s=start")
        self.type("abc")
        self.assertEqual(self.step(), "day")
        self.assertIn("No match for “abc”", self.last_text())
        self.type("2026-01-05")  # 期间外
        self.assertEqual(self.step(), "day")
        self.type("15/06/2567")
        self.assertEqual((self.step(), self.payload()["day"]), ("customer", "2024-06-15"))
        self.assertNotIn("No match", self.last_text())  # 认对了的日期：问客户那一步不带「没找到」


class BookButtons(Base, T.TwoBooks):
    def test_books_in_different_folders_show_the_folder_name(self):
        self.books = [T.TEST26, dict(T.TEST26, key="e" * 40, path=r"\\Accserver\d$\ACCOUNT\69EXP\TEST2", label="TEST")]
        self.tap("a=col&s=start")
        self.assertEqual([item["label"] for item in self.buttons()], ["1. TEST", "2. TEST2", "Cancel"])


class MrErpLedger(Base):
    """账本是 MR.ERP 的公司：收款导入菜单在账套之后按按钮问一次；一笔只收一种钱（选过现金或一个银行就不再加），折扣照旧。"""

    def test_several_receipt_menus_are_asked_once_and_remembered(self):
        chosen = []
        menus = {"options": [{"code": "169", "name": "รับชำระหนี้"}, {"code": "171", "name": "รับชำระ-2"}]}
        with mock.patch.object(service, "menu_options", side_effect=lambda c, d=None: None if chosen else menus), \
                mock.patch.object(service, "choose_menu", side_effect=lambda c, v, d=None: chosen.append(v) or {"chosen": v}):
            self.tap("a=col&s=start")
            self.assertEqual(self.step(), "menu")
            self.assertIn("Choose the MR.ERP collection menu", self.last_text())
            self.tap("a=col&s=menu&v=999")
            self.assertEqual((self.step(), chosen), ("menu", []))  # 没给的不收
            self.tap("a=col&s=menu&v=171")
            self.assertEqual((self.step(), chosen), ("customer", ["171"]))

    def test_one_money_method_then_no_more_methods(self):
        payload = {"catalog": {"cash": True, "transfers": [{"prefix": "1112-01", "name": "กรุงเทพ"}], "discount": None,
                               "single": True}, "cash": "100.00", "methods": []}
        self.assertEqual(flow._unused(payload), [])
        payload["cash"] = "0"
        self.assertEqual([key for key, _label in flow._unused(payload)], [flow.CASH, "1112-01"])
        payload["methods"] = [{"prefix": "1112-01", "role": C.TRANSFER, "amount": "1.00"}]
        self.assertEqual(flow._unused(payload), [])
        payload["catalog"].pop("single")  # Express：照旧还能加现金
        self.assertEqual([key for key, _label in flow._unused(payload)], [flow.CASH])


class MrErpResultLines(unittest.TestCase):
    """账本是 MR.ERP 的收款（view 带 ``ledger``）：LINE 的结果句不说 Express，用状态词 + 单号 / 原因；Express 的照旧。"""

    def test_written_and_failed_name_no_express_on_mrerp(self):
        written = {"status": C.WRITTEN, "docnum": "FR0001", "ledger": "mrerp"}
        self.assertEqual(flow._status_text(written), "erp.collection.status.written · FR0001")
        self.assertEqual(flow._status_text({**written, "ledger": None}), "Written to Express: FR0001")
        failed = {"status": C.FAILED, "reason": "mrerp.importing", "ledger": "mrerp"}
        text = flow._status_text(failed)
        self.assertTrue(text.startswith("erp.collection.status.failed · "), text)
        self.assertNotIn("Express", text)


if __name__ == "__main__":
    unittest.main()
