"""Cowork LINE「发单据进 ERP」（用户 2026-09-26：照 ERP 新做法）：快捷回复逐步选、只认当前一步、收单进网页同一条链。"""
import contextlib
import sys
import unittest
from pathlib import Path
from unittest import mock

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "apps" / "pearnly_cowork"))
sys.path.insert(0, str(root / "apps" / "pearnly_core"))

from pearnly_cowork.line import flow, handlers, intake  # noqa: E402
MRERP = {"name": "CON-M", "adapter": "mrerp", "enabled": 1}
EXPRESS = {"name": "CON-X", "adapter": "express", "enabled": 1}
# MR.ERP 一家公司两个年度；Express 两个年度文件夹里三份（其中一份写不进）。
MRERP_BOOKS = [{"source_id": "15:1", "label": "2567", "attributes": {"company": "A", "company_name": "Alpha"}},
               {"source_id": "16:1", "label": "2568", "attributes": {"company": "A", "company_name": "Alpha"}}]
EXPRESS_BOOKS = [
    {"source_id": r"s:\2568\exp68\68acme", "label": "68ACME AUDITED",
     "attributes": {"root_label": "68EXP", "row": 1, "company": "Acme", "writable": True}},
    {"source_id": r"s:\2569\exp69\69acme", "label": "69ACME DATA",
     "attributes": {"root_label": "69EXP", "row": 1, "company": "Acme", "writable": True}},
    {"source_id": r"s:\2569\exp69\69old", "label": "69ACME OLD",
     "attributes": {"root_label": "69EXP", "row": 2, "company": "Acme", "writable": False}},
]


class _Media:
    """LINE 下载服务器的回复替身：流式给 `size` 个字节；`declared` 为假时不带 Content-Length（逐块累加时才发现超了）。"""

    def __init__(self, size, declared=True):
        self.size, self.status_code = size, 200
        self.headers = {"Content-Length": str(size)} if declared else {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk):
        block, left = b"\0" * chunk, self.size
        while left > 0:
            yield block[:min(chunk, left)]
            left -= chunk


class LineFlowTest(unittest.TestCase):
    def setUp(self):
        self.session, self.replies = {}, []
        store = self.session

        def set_session(user, state, payload, ttl_minutes=None):
            store[user] = {"state": state, "payload": payload}

        patches = [
            mock.patch.object(flow, "_", side_effect=lambda text, *a, **k: text),
            mock.patch.object(flow, "locked", lambda user: contextlib.nullcontext()),
            mock.patch.object(flow.line_session, "get_session", side_effect=lambda user: store.get(user)),
            mock.patch.object(flow.line_session, "set_session", side_effect=set_session),
            mock.patch.object(flow.line_session, "clear_session", side_effect=lambda user: store.pop(user, None)),
            mock.patch.object(flow.line, "reply", side_effect=lambda token, messages: self.replies.append(messages[0])),
            mock.patch.object(flow, "_connections", return_value={"mrerp": MRERP}),
            mock.patch("pearnly_cowork.connections.service.targets", side_effect=lambda name: {
                "account_sets": EXPRESS_BOOKS if name == "CON-X" else self.books}),
            mock.patch.object(flow, "_last_used", side_effect=lambda payload, rows: self.last_used),
        ]
        refresh = mock.patch("pearnly_cowork.connections.service.request_refresh")
        self.refresh = refresh.start()
        self.addCleanup(refresh.stop)
        # 选定账套后请电脑读这份账套：同一时刻只排一个、不打断正在读的（`ensure_account_masters`）
        read = mock.patch("pearnly_cowork.connections.service.ensure_account_masters")
        self.read = read.start()
        self.addCleanup(read.stop)
        # 存货科目组：默认小助手还没读过这份账套（不问）；各测试按需改。
        self.stock_view = {"known": False, "groups": [], "chosen": None}
        stock = mock.patch("pearnly_cowork.connections.service.stock_acc_group",
                           side_effect=lambda name, key: self.stock_view)
        choose = mock.patch("pearnly_cowork.connections.service.choose_stock_acc_group")
        stock.start()
        self.choose = choose.start()
        self.addCleanup(stock.stop)
        self.addCleanup(choose.stop)
        self.books, self.last_used = MRERP_BOOKS, ""
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def last(self):
        return self.replies[-1]

    def labels(self):
        return [item["action"]["label"] for item in self.last()["quickReply"]["items"]]

    def tap(self, label):
        items = self.last()["quickReply"]["items"]
        data = next(item["action"]["data"] for item in items if item["action"]["label"] == label[:20])
        flow.answer("U1", data, "tok")

    def test_target_lists_ready_ones_and_says_why_others_are_not(self):
        flow.start("U1", "tok")
        self.assertEqual(self.labels(), ["MR.ERP", "intake.target.recognizeOnly"[:20], "Cancel"])
        self.assertIn("• Express: connect.state.notConnected", self.last()["text"])

    def search(self, text):
        return flow.search("U1", text, "tok")

    def test_mrerp_sales_picks_one_of_a_few_account_sets_by_number(self):
        """用户 10-24：账套不超过 12 份直接列出来按序号点，不出关键字提示。"""
        flow.start("U1", "tok")
        self.tap("MR.ERP")
        self.assertEqual(self.last()["text"].splitlines(), ["Choose the company", "1. A · Alpha · 2567", "2. A · Alpha · 2568"])
        self.assertEqual(self.labels(), ["1", "2", "Cancel"])
        self.tap("2")
        self.assertTrue(self.last()["text"].startswith("Purchase or sales?"))
        self.tap("intake.dir.sales")
        self.tap("Credit")
        payload = flow.choices("U1")
        self.assertTrue(flow.ready(payload))
        self.assertEqual((payload["connection"], payload["account_set_key"], payload["direction"], payload["payment"]),
                         ("CON-M", "16:1", "sales", "credit"))
        self.assertIn("MR.ERP · A · Alpha · 2568 · intake.dir.sales · Credit", self.last()["text"])

    def test_no_match_asks_again_and_words_outside_the_step_are_not_taken(self):
        self.assertFalse(self.search("2568"))  # 还没开始选
        flow.start("U1", "tok")
        self.tap("MR.ERP")
        self.assertTrue(self.search("zzz"))
        self.assertIn("No account set matches", self.last()["text"])
        self.assertNotIn("account_set_key", flow.choices("U1"))

    def many_books(self, count=13):
        return [{"source_id": f"{index}:1", "label": f"25{index:02d}", "attributes": {"company": "A", "company_name": "Alpha"}}
                for index in range(1, count + 1)]

    def test_more_than_twelve_account_sets_ask_for_a_word_with_last_used_as_a_button(self):
        """超过 12 份：先请用户打名称关键字（不提编号），上次用的一开始就给按钮。"""
        self.books, self.last_used = self.many_books(), "3:1"
        flow.start("U1", "tok")
        self.tap("MR.ERP")
        self.assertTrue(self.last()["text"].startswith("Type a few letters of the company or account set name"))
        self.assertIn("Last used: A · Alpha · 2503", self.last()["text"])
        self.assertEqual(self.labels(), ["Last used", "Cancel"])
        self.tap("Last used")
        self.assertEqual(flow.choices("U1")["account_set_key"], "3:1")

    def test_typed_matches_are_listed_up_to_twelve_more_ask_for_more_letters_none_asks_again(self):
        self.books = self.many_books()
        flow.start("U1", "tok")
        self.tap("MR.ERP")
        self.assertTrue(self.search("Alpha"))   # 13 份都匹配：请多打几个字，不列
        self.assertEqual(self.last()["text"], "Too many matches; type a few more letters to narrow it down")
        self.assertEqual(self.labels(), ["Cancel"])
        self.assertTrue(self.search("251"))   # 2510–2513 → 4 份，列序号
        self.assertEqual(self.labels(), ["1", "2", "3", "4", "Cancel"])
        self.assertTrue(self.search("zzz"))
        self.assertIn("No account set matches", self.last()["text"])
        self.assertTrue(self.last()["text"].splitlines()[1].startswith("Type a few letters"))

    def test_a_single_account_set_is_taken_without_asking(self):
        self.books = MRERP_BOOKS[:1]
        flow.start("U1", "tok")
        self.tap("MR.ERP")
        self.assertTrue(self.last()["text"].startswith("Purchase or sales?"))
        self.assertEqual(flow.choices("U1")["account_set_key"], "15:1")

    def test_express_search_hides_unwritable_books_then_goes_straight_to_upload(self):
        """11-06 按行选物料：Express 上传前不再问整批服务 / 存货（也就没有存货科目组一步），识别后按行追问（`line.items`）。"""
        with mock.patch.object(flow, "_connections", return_value={"express": EXPRESS}), \
                mock.patch.object(flow, "_state", return_value=(True, "")):
            flow.start("U1", "tok")
            self.tap("Express")
            self.search("acme")
            self.assertEqual(self.last()["text"].splitlines()[1:],
                             ["1. 69EXP · 69ACME DATA（Acme）", "2. 68EXP · 68ACME AUDITED（Acme）"])
            self.tap("2")
            self.tap("intake.dir.purchase")
            payload = flow.choices("U1")
        self.assertTrue(flow.ready(payload))
        self.assertTrue(self.last()["text"].endswith("the text the right way up and the whole page in frame."))
        self.assertEqual((payload["connection"], payload["account_set_key"], payload.get("posting_kind")),
                         ("CON-X", r"s:\2568\exp68\68acme", None))
        # 选定 Express 账套就让助手读这份账套的主档（推送前的库存检查用）；MR.ERP 不需要。
        self.assertEqual(self.read.call_args.args, ("CON-X", r"s:\2568\exp68\68acme"))
        self.refresh.assert_not_called()
        self.choose.assert_not_called()

    def test_mrerp_choice_does_not_ask_for_an_account_read(self):
        self.books = MRERP_BOOKS[:1]
        flow.start("U1", "tok")
        self.tap("MR.ERP")
        self.refresh.assert_not_called()
        self.read.assert_not_called()

    def test_mrerp_purchase_asks_cash_or_credit(self):
        # 11-14 起 MR.ERP 现购照写：进项与销项一样问现金 / 赊账
        self.books = MRERP_BOOKS[:1]
        flow.start("U1", "tok")
        self.tap("MR.ERP")
        self.tap("intake.dir.purchase")
        self.assertNotIn("payment", flow.choices("U1"))
        self.tap("Cash")
        self.assertEqual(flow.choices("U1")["payment"], "cash")
        self.assertTrue(flow.ready(flow.choices("U1")))

    def test_recognize_only_asks_direction_only(self):
        flow.start("U1", "tok")
        self.tap("intake.target.recognizeOnly")
        self.tap("intake.dir.purchase")
        payload = flow.choices("U1")
        self.assertTrue(flow.ready(payload))
        self.assertNotIn("connection", payload)

    def test_old_button_is_rejected(self):
        flow.start("U1", "tok")
        stale = self.last()["quickReply"]["items"][0]["action"]["data"]
        self.tap("MR.ERP")
        flow.answer("U1", stale, "tok")
        self.assertEqual(self.last()["text"], "This option is no longer current")
        flow.answer("U1", "a=cowork&s=year&v=9", "tok")
        self.assertEqual(self.last()["text"], "This option is no longer current")

    def test_cancel_clears_the_session(self):
        flow.start("U1", "tok")
        self.tap("Cancel")
        self.assertEqual(flow.choices("U1"), {})

    def test_questions_in_progress_survive_a_restart_and_a_cancel_of_the_upload_choices(self):
        """识别后在问的题（`line.ask`，11-19）也在这个会话里：重新点菜单、选目标、取消上传前的选择都不打断它们。"""
        asking = {"tickets": {"current": {"name": "H1"}, "queue": []}, "precheck_ask": {"current": {"id": "t1"}},
                  "items": {"history": "H1"}}
        self.session["U1"] = {"state": flow.STATE, "payload": {"target": "mrerp", "connection": "CON-M", **asking}}
        flow.start("U1", "tok")
        self.tap("MR.ERP")
        payload = flow.choices("U1")
        self.assertEqual({key: payload[key] for key in flow.ASKING_KEYS}, asking)
        self.assertEqual(payload["target"], "mrerp")
        self.tap("Cancel")
        self.assertEqual(flow.choices("U1"), asking)

    def test_postback_data_parsing(self):
        self.assertTrue(flow.is_start("action=cowork_erp_start"))
        self.assertTrue(flow.is_step("a=cowork&s=target&v=0"))
        self.assertFalse(flow.is_step("a=work&c=home"))


class LineHandlerTest(unittest.TestCase):
    def setUp(self):
        self.replies = []
        for patch in [
            mock.patch.object(handlers, "_", side_effect=lambda text, *a, **k: text),
            mock.patch.object(handlers, "_reply", side_effect=lambda token, text: self.replies.append(text)),
            mock.patch.object(handlers.binding, "enabled", return_value=True),
            mock.patch.object(handlers.flow, "allowed", return_value=True),
            mock.patch.object(handlers.menu, "request_sync"),
            mock.patch.object(handlers, "_as", side_effect=lambda user, work: work()),
        ]:
            patch.start()
            self.addCleanup(patch.stop)

    def event(self, **extra):
        return {"type": "message", "replyToken": "tok", **extra}

    def test_words_typed_while_choosing_the_account_set_go_to_the_search(self):
        with mock.patch.object(handlers.line_binding, "user_of", return_value="a@x"), \
                mock.patch.object(handlers.flow, "search", return_value=True) as search:
            handlers._handle(self.event(message={"type": "text", "text": "LJ land"}), "U1")
        search.assert_called_once_with("U1", "LJ land", "tok")
        self.assertEqual(self.replies, [])

    def test_follow_asks_for_the_binding_code_and_unfollow_unbinds(self):
        self.assertTrue(handlers._handle({"type": "follow", "replyToken": "tok"}, "U1"))
        self.assertEqual(self.replies, ["Send the code from the Cowork page to bind"])
        with mock.patch.object(handlers.line_binding, "unbind") as unbind:
            self.assertTrue(handlers._handle({"type": "unfollow"}, "U1"))
        unbind.assert_called_once_with("U1")
        handlers.menu.request_sync.assert_called_with("U1")

    def test_japanese_unbind_word_is_understood(self):
        # 11-08 补日文：DMS 早就认「連携解除」，Cowork 同一组
        self.assertIn("連携解除", handlers.UNBIND_WORDS)
        self.assertIn("メニュー", handlers.flow.MENU_WORDS)

    def test_item_buttons_and_words_go_to_the_item_questions_first(self):
        """11-06 按行选物料：识别后按行追问的按钮（a=cowork_item）交出卡前的提问（`line.ask`，11-19 起编排按行追问与写账前置判定）；
        在「选已有存货」这一步打的字给它找存货——没重新点菜单（不在选账套这一步）时选账套那一步不接。"""
        from pearnly_cowork.line import ask

        with mock.patch.object(handlers.line_binding, "user_of", return_value="a@x"), \
                mock.patch.object(ask, "step") as answer, \
                mock.patch.object(ask, "text", return_value=True) as search, \
                mock.patch.object(handlers.flow, "search", return_value=False) as account_search:
            self.assertTrue(handlers._handle({"type": "postback", "replyToken": "tok",
                                              "postback": {"data": "a=cowork_item&v=each"}}, "U1"))
            handlers._handle(self.event(message={"type": "text", "text": "ice"}), "U1")
        answer.assert_called_once_with("U1", "a=cowork_item&v=each", "tok")
        search.assert_called_once_with("U1", "ice", "tok")
        account_search.assert_called_once_with("U1", "ice", "tok")
        self.assertEqual(self.replies, [])

    def test_menu_tap_needs_binding(self):
        with mock.patch.object(handlers.line_binding, "user_of", return_value=None):
            handlers._handle({"type": "postback", "replyToken": "tok",
                              "postback": {"data": flow.START}}, "U1")
        self.assertEqual(self.replies, ["Bind this account first"])

    def test_menu_tap_starts_flow(self):
        with mock.patch.object(handlers.line_binding, "user_of", return_value="a@x"), \
                mock.patch.object(handlers.flow, "start") as start:
            handlers._handle({"type": "postback", "replyToken": "tok", "postback": {"data": flow.START}}, "U1")
        start.assert_called_once_with("U1", "tok")

    def test_old_coordination_tile_answers(self):
        with mock.patch.object(handlers.line_binding, "user_of", return_value="a@x"):
            self.assertTrue(handlers._handle({"type": "postback", "replyToken": "tok",
                                              "postback": {"data": "a=work&c=home"}}, "U1"))
        self.assertEqual(self.replies, ["This channel is not open yet"])

    def test_document_before_choices_asks_for_the_menu(self):
        with mock.patch.object(handlers.line_binding, "user_of", return_value="a@x"), \
                mock.patch.object(handlers.flow, "choices", return_value={}), \
                mock.patch.object(handlers.intake, "receive") as receive:
            handlers._handle(self.event(message={"type": "image", "id": "m1"}), "U1")
        receive.assert_not_called()
        self.assertEqual(self.replies, ["Tap Send documents to ERP in the menu first"])

    def test_document_after_choices_is_queued_as_the_bound_user(self):
        ready = {"target": "none", "direction": "purchase"}
        with mock.patch.object(handlers.line_binding, "user_of", return_value="a@x"), \
                mock.patch.object(handlers.flow, "choices", return_value=ready), \
                mock.patch.object(handlers.intake, "receive") as receive:
            handlers._handle(self.event(message={"type": "file", "id": "m2", "fileName": "a.pdf"}), "U1")
        self.assertEqual(receive.call_args.kwargs["bound"], "a@x")
        self.assertEqual(receive.call_args.kwargs["choices"], ready)


class LineCardTest(unittest.TestCase):
    """卡片照 ERP：进项绿、只列卖方、明细逐行、合计用主题色、只有一个复核按钮。"""

    def card(self, direction="purchase"):
        from pearnly_cowork.line import cards

        fields = {"direction": direction, "invoice_number": "IV6906-00001", "date_raw": "1/6/2569",
                  "date": "2026-06-01", "document_type": "tax_invoice", "seller_name": "S Co", "seller_tax": "0105527028626",
                  "buyer_name": "B Co", "subtotal": "1370.00", "vat": "95.90", "total_amount": "1465.90",
                  "items": [{"name": "ข้อต่อ", "qty": "2", "price": "60.00", "subtotal": "120.00"}]}
        record = mock.Mock(pages=__import__("json").dumps([{"fields": fields}]), total_amount="1465.90")
        record.name = "CWH-1"
        with mock.patch.object(cards, "_", side_effect=lambda text, *a, **k: text), \
                mock.patch.object(cards.frappe.utils, "get_url", return_value="https://t.example"):
            return cards.preview(record, {"target": "识别后导出", "direction": direction, "mode": ""})

    def texts(self, card):
        found = []

        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "text":
                    found.append(node["text"])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(card)
        return found

    def test_purchase_card(self):
        card = self.card()
        bubble = card["contents"]
        self.assertEqual(bubble["header"]["backgroundColor"], "#16873E")
        texts = self.texts(card)
        self.assertIn("S Co", texts)
        self.assertNotIn("B Co", texts)
        self.assertIn("1. ข้อต่อ", texts)
        self.assertIn("1,465.90", texts)
        self.assertIn("01/06/2569", texts)
        self.assertIn("Transaction type", texts)
        self.assertIn("Tax Invoice", texts)
        buttons = bubble["footer"]["contents"]
        self.assertEqual(len(buttons), 3)  # 保存 / 编辑 / 取消（11-19）
        # 经免密入口：LINE 里未登录直接走 LINE 登录（2026-09-30 Cowork 接入）。
        self.assertEqual(buttons[1]["action"]["uri"],
                         "https://t.example/api/method/pearnly_core.account.line_login.enter"
                         "?to=%2Fdesk%2Fpearnly-intake%2Fnew%2FCWH-1")

    def test_menu_web_pages_open_through_the_line_sign_in_entry(self):
        from pearnly_cowork.line import menu

        with mock.patch.object(menu, "_", side_effect=lambda text, *a, **k: text), \
                mock.patch.object(menu.frappe.utils, "get_url", return_value="https://t.example"):
            areas = menu.payload("intake+stocktake+work", "th")["areas"]
        uris = [area["action"].get("uri") for area in areas if area["action"]["type"] == "uri"]
        entry = "https://t.example/api/method/pearnly_core.account.line_login.enter?to="
        self.assertEqual(uris, [entry + "%2Fdesk%2Fpearnly-stocktake", entry + "%2Fdesk%2Fpearnly-work"])

    def test_sales_card_lists_the_buyer(self):
        texts = self.texts(self.card("sales"))
        self.assertIn("B Co", texts)
        self.assertNotIn("S Co", texts)

    def test_erp_card_shows_the_chosen_account_set(self):
        """推到 ERP 的票在卡片上写上传前选定的账套（票上记的那份）。"""
        from pearnly_cowork.line import cards

        record = mock.MagicMock(pages="[]", total_amount="1.00")
        record.name = "CWH-2"
        record.get.side_effect = lambda key, default=None: {"intake_target": "express", "push_connection": "CON-X",
                                                            "push_account_set": r"s:\2569\exp69\6902asc"}.get(key, default)
        with mock.patch.object(cards, "_", side_effect=lambda text, *a, **k: text), \
                mock.patch.object(cards.frappe.utils, "get_url", return_value="https://t.example"), \
                mock.patch("pearnly_cowork.connections.directory.account_label",
                           return_value="69EXP · 6902 ASIA-SPORT") as label:
            texts = self.texts(cards.preview(record, {"target": "Express"}))
        self.assertIn("intake.target.accountSet", texts)
        self.assertIn("69EXP · 6902 ASIA-SPORT", texts)
        label.assert_called_once_with("CON-X", r"s:\2569\exp69\6902asc")


class LineCardSaveTest(unittest.TestCase):
    """卡片「保存」（用户 11-18）：推往 ERP 的票在「编辑」上方多一个「保存」，点了直接推送（同录入页「提交」）；
    要人定的项不推，回现有原因 + 快捷回复「编辑」。"""

    def setUp(self):
        from pearnly_cowork.line import save as line_save
        from pearnly_cowork.push.application import card_save as save

        self.line_save, self.save = line_save, save
        self.record = mock.MagicMock(pages="[]", total_amount="1.00", record_version=3, posting_kind="")
        self.record.name = "CWH-3"
        self.values = {"intake_target": "express", "push_connection": "CON-X", "push_account_set": "ACC-1",
                       "last_push_status": "", "last_push_record": ""}
        self.record.get.side_effect = lambda key, default=None: self.values.get(key, default)
        self.record.push_connection = "CON-X"
        self.after = {"last_push_status": "pending", "last_push_reason": ""}
        self.view = {"needed": []}
        self.exists = True
        for patch in [
            mock.patch.object(save, "_", side_effect=lambda text, *a, **k: text),
            mock.patch.object(save.storage, "read", side_effect=lambda name: self.record),
            mock.patch.object(save.connections, "adapter_of", return_value="express"),
            mock.patch.object(save.connections, "stock_acc_group", return_value=None),
            mock.patch.object(save.application, "choices_view", side_effect=lambda *a, **k: self.view),
            mock.patch.object(save.export_facts, "reason_text", side_effect=lambda reason: "T:" + str(reason)),
            mock.patch.object(save.conflicts, "retried", side_effect=lambda function, *a, **k: function(*a, **k)),
            mock.patch.object(save.storage, "complete"),
            mock.patch.object(save.removal, "delete"),
            mock.patch.object(save.frappe, "db", mock.Mock(**{"get_value.side_effect": self.get_value,
                                                              "exists.side_effect": lambda *a, **k: self.exists}), create=True),
        ]:
            patch.start()
            self.addCleanup(patch.stop)
        self.submit = mock.patch.object(save.application, "submit",
                                        return_value={"status": "prepared", "name": "PR-1", "revision": 1}).start()
        self.confirm = mock.patch.object(save.application, "confirm", return_value={"status": "pending"}).start()
        self.addCleanup(mock.patch.stopall)

    def get_value(self, doctype, name, fields=None, as_dict=False):
        if doctype == self.save.HISTORY:
            return self.after
        # 连接的版本号按真实 DocType 名取（11-19 真机：写成不存在的「Cowork Connection」，保存直接抛错、LINE 不回话）
        self.assertEqual(doctype, "Cowork ERP Connection")
        return 7

    def test_push_and_recognize_only_cards_have_save_edit_cancel(self):
        from pearnly_cowork.line import cards

        with mock.patch.object(cards, "_", side_effect=lambda text, *a, **k: text), \
                mock.patch.object(cards.frappe.utils, "get_url", return_value="https://t.example"), \
                mock.patch("pearnly_cowork.connections.directory.account_label", return_value="69EXP"):
            for target in ("express", "none"):
                self.values["intake_target"] = target
                buttons = cards.preview(self.record, {"target": "x"})["contents"]["footer"]["contents"]
                self.assertEqual((buttons[0]["action"]["data"], buttons[0]["style"], buttons[0]["color"]),
                                 ("a=cowork_save&n=CWH-3", "primary", "#16873E"))
                self.assertEqual((buttons[1]["style"], buttons[1]["action"]["type"]), ("secondary", "uri"))
                self.assertEqual((buttons[2]["style"], buttons[2]["action"]["data"]),
                                 ("secondary", "a=cowork_cancel&n=CWH-3"))

    def test_save_pushes_and_confirms_like_the_intake_page(self):
        self.assertEqual(self.save.save("CWH-3"), {"sent": True})
        args = self.submit.call_args
        self.assertEqual(args.args[:6], ("CWH-3", "CON-X", args.args[2], 3, 7, "ACC-1"))
        self.assertTrue(args.kwargs["manual"])
        self.assertEqual(self.confirm.call_args.args[:2], ("PR-1", 1))
        self.assertEqual(self.confirm.call_args.args[2], "confirm:" + args.args[2])

    def test_save_stops_when_the_stock_group_is_not_chosen_the_same_rule_the_questions_use(self):
        """兜底（旧卡、题没答完）：有库存行、合格的存货科目组不止一个、还没选 → 不推，回原因；出卡前的提问用同一个判据
        （`stock_group_open`，`assistant.precheck_source`）。"""
        view = {"account_set_key": "acc-1", "known": True, "chosen": None, "groups": [{"acccod": "1"}, {"acccod": "2"}]}
        self.record.pages = '[{"fields": {"invoice_number": "IV1", "total_amount": "1", "items": [{"name": "Box", "posting_kind": "stock"}]}}]'
        with mock.patch.object(self.save.connections, "stock_acc_group", return_value=view):
            self.assertEqual(self.save.stock_group_open(self.record), view)
            self.assertEqual(self.save.save("CWH-3"), {"stop": "Inventory account group"})
            self.record.pages = "[]"  # 没有库存行：不问
            self.assertIsNone(self.save.stock_group_open(self.record))
            self.record.pages = '[{"fields": {"invoice_number": "IV1", "total_amount": "1", "items": [{"name": "Box", "posting_kind": "stock"}]}}]'
            view["chosen"] = "1"
            self.assertIsNone(self.save.stock_group_open(self.record))
        self.submit.assert_not_called()

    def test_save_stops_when_a_choice_is_needed(self):
        self.view = {"needed": [{"kind": "account"}]}
        self.assertEqual(self.save.save("CWH-3"), {"stop": "T:choice_needed"})
        self.submit.assert_not_called()

    def test_save_stops_without_an_account_set_and_on_a_failed_push(self):
        self.values["push_account_set"] = ""
        self.assertEqual(self.save.save("CWH-3"), {"stop": "Choose the year and account set first."})
        self.values["push_account_set"] = "ACC-1"
        self.submit.return_value = {"status": "failed", "reason": "push.disabled"}
        self.assertEqual(self.save.save("CWH-3"), {"stop": "T:push.disabled"})
        self.confirm.assert_not_called()

    def test_recognize_only_save_completes_the_record(self):
        self.values["intake_target"] = "none"
        self.assertEqual(self.save.save("CWH-3"), {"saved": True})
        self.save.storage.complete.assert_called_once_with([{"name": "CWH-3", "record_version": 3}])
        self.submit.assert_not_called()

    def test_cancel_deletes_an_untouched_draft_and_a_later_save_only_says_cancelled(self):
        self.values["staged"] = 1
        self.assertEqual(self.save.cancel("CWH-3"), {"cancelled": True})
        self.save.removal.delete.assert_called_once_with(["CWH-3"])
        self.exists = False
        self.assertEqual(self.save.save("CWH-3"), {"cancelled": True})
        self.assertEqual(self.save.cancel("CWH-3"), {"cancelled": True})
        self.submit.assert_not_called()
        self.assertEqual(self.save.removal.delete.call_count, 1)

    def test_cancel_after_save_or_push_keeps_the_record(self):
        self.values.update(staged=0)
        self.assertEqual(self.save.cancel("CWH-3"), {"kept": True})
        self.values.update(staged=1, last_push_status="pending")
        self.assertEqual(self.save.cancel("CWH-3"), {"kept": True})
        self.save.removal.delete.assert_not_called()

    def test_a_pushed_card_is_not_pushed_twice(self):
        self.values["last_push_status"] = "success"
        self.assertEqual(self.save.save("CWH-3")["stop"], "T:already_success")
        self.submit.assert_not_called()

    def test_line_reaches_push_only_through_card_save(self):
        """架构登记 line → push（11-19 主会话批）只为卡片「保存」「取消」：line 域只许导入 `push.application.card_save`、只调它的
        `save` / `cancel`。"""
        import ast

        base = root / "apps" / "pearnly_cowork" / "pearnly_cowork" / "line"
        found = set()
        for path in base.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("pearnly_cowork.push"):
                    found |= {(node.module, alias.name) for alias in node.names}
                elif isinstance(node, ast.Import):
                    found |= {(alias.name, "") for alias in node.names if alias.name.startswith("pearnly_cowork.push")}
                elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "card_save":
                    found.add(("card_save.", node.attr))
        self.assertEqual(found, {("pearnly_cowork.push.application", "card_save"), ("card_save.", "save"),
                                 ("card_save.", "cancel")})

    def test_an_unexpected_error_answers_failed(self):
        replies = []
        with mock.patch.object(self.save, "save", side_effect=RuntimeError("boom")), \
                mock.patch.object(self.line_save, "_", side_effect=lambda text, *a, **k: text), \
                mock.patch.object(self.line_save.frappe, "log_error", create=True) as log, \
                mock.patch.object(self.line_save.frappe, "get_traceback", return_value="t", create=True), \
                mock.patch.object(self.line_save.frappe, "clear_messages", create=True), \
                mock.patch("pearnly_core.line.reply", side_effect=lambda token, messages: replies.append(messages)):
            self.line_save.reply("CWH-3", "tok")
        self.assertEqual(replies, [[{"type": "text", "text": "Failed"}]])
        log.assert_called_once()

    def test_handler_routes_the_save_button(self):
        with mock.patch.object(handlers.line_binding, "user_of", return_value="a@x"), \
                mock.patch.object(handlers.binding, "enabled", return_value=True), \
                mock.patch.object(handlers.flow, "allowed", return_value=True), \
                mock.patch.object(handlers, "_as", side_effect=lambda user, work: work()), \
                mock.patch.object(self.line_save, "reply") as reply:
            self.assertTrue(handlers._handle({"type": "postback", "replyToken": "tok",
                                              "postback": {"data": "a=cowork_save&n=CWH-3"}}, "U1"))
        reply.assert_called_once_with("CWH-3", "tok", cancel=False)


class LineAnnounceTest(unittest.TestCase):
    def announce(self, pending, arrive=None, user="a@x"):
        """识别完一份：回 (交给出卡前提问的参数, 发出去的)。"""
        cache = {intake._PENDING + "CWS-1": pending}
        fake = mock.Mock(get_value=cache.get, delete_value=lambda key: cache.pop(key, None))
        sent = []
        with mock.patch.object(intake.frappe, "cache", fake), mock.patch("pearnly_core.execution.state.store", return_value=fake), \
                mock.patch.object(intake.frappe, "local", mock.Mock(lang="zh")), \
                mock.patch.object(intake.frappe, "session", mock.Mock(user="Guest"), create=True), \
                mock.patch.object(intake.frappe, "set_user", create=True), \
                mock.patch.object(intake.frappe, "db", mock.Mock(), create=True), \
                mock.patch.object(intake.frappe, "clear_messages", create=True), \
                mock.patch.object(intake.frappe, "log_error", create=True), \
                mock.patch.object(intake.frappe, "get_traceback", return_value="t", create=True), \
                mock.patch.object(intake.line_binding, "user_of", return_value=user), \
                mock.patch("pearnly_cowork.line.cards.previews", side_effect=lambda records, pending: [
                    {"type": "flex", "card": name} for name in records]), \
                mock.patch("pearnly_cowork.line.ask.arrive", side_effect=arrive or (lambda user, records, pending: [
                    {"type": "text", "text": "question"}])) as asked, \
                mock.patch.object(intake.line, "reply_or_push", side_effect=lambda to, messages, token="": sent.append(
                    (to, messages, token))):
            intake.announce("CWS-1", "saved", ["H1"])
            intake.announce("CWS-1", "saved", ["H1"])  # 只通知一次
        return asked.call_args_list, sent

    def test_only_the_first_announcement_is_sent(self):
        calls, sent = self.announce({"line_user_id": "U1", "language": "th", "reply_token": "tok", "push": False})
        self.assertEqual(sent, [("U1", [{"type": "text", "text": "question"}], "tok")])
        self.assertEqual(len(calls), 1)

    def test_a_push_document_goes_to_the_questions_and_its_card_comes_last(self):
        """11-19「所有问题问完 … 一气呵成」：推往 ERP 的票交出卡前的提问（`line.ask.arrive`），不再「卡片 + 追问一起发」。"""
        pending = {"line_user_id": "U1", "language": "th", "reply_token": "tok", "push": True, "target": "Express",
                   "direction": "purchase", "mode": ""}
        calls, sent = self.announce(pending)
        self.assertEqual(calls[0].args[:2], ("U1", ["H1"]))
        self.assertTrue(calls[0].args[2]["push"])
        self.assertEqual(sent, [("U1", [{"type": "text", "text": "question"}], "tok")])

    def test_a_failing_question_step_or_an_unbound_user_still_gets_the_cards(self):
        pending = {"line_user_id": "U1", "language": "th", "reply_token": "tok", "target": "Express"}  # 部署前记下的（没有 push）
        calls, sent = self.announce(pending, arrive=mock.Mock(side_effect=RuntimeError("boom")))
        self.assertTrue(calls[0].args[2]["push"])
        self.assertEqual(sent, [("U1", [{"type": "flex", "card": "H1"}], "tok")])
        calls, sent = self.announce(dict(pending), user=None)
        self.assertEqual((calls, sent), ([], [("U1", [{"type": "flex", "card": "H1"}], "tok")]))

    def test_uploads_not_from_line_are_ignored(self):
        fake = mock.Mock(get_value=lambda key: None)
        with mock.patch.object(intake.frappe, "cache", fake), mock.patch("pearnly_core.execution.state.store", return_value=fake), \
                mock.patch.object(intake.line, "reply_or_push") as send:
            intake.announce("CWS-2", "saved", ["H1"])
        send.assert_not_called()

    def _admit_line_file(self, size, declared=True):
        """LINE 发来一份 `size` 字节的文件走到 `_admit`：返回 (存原件收到的字节数或 None, 回给用户的话)。"""
        stored, said = [], []
        with mock.patch("pearnly_core.line.requests.get", return_value=_Media(size, declared)), \
                mock.patch("pearnly_core.line._headers", return_value={}), \
                mock.patch.object(intake.frappe, "_", side_effect=lambda text, *a, **k: text, create=True), \
                mock.patch.object(intake.frappe, "db", mock.Mock(), create=True), \
                mock.patch.object(intake.frappe, "log_error", create=True), \
                mock.patch.object(intake, "_", side_effect=lambda text, *a, **k: text), \
                mock.patch.object(intake, "_say", side_effect=lambda user, text, token: said.append(text)), \
                mock.patch.object(intake, "_remember"), \
                mock.patch("pearnly_cowork.access.scope.default_workspace", return_value="WS-1"), \
                mock.patch("pearnly_cowork.files.service.create",
                           side_effect=lambda ws, name, content, key: stored.append(len(content)) or {"name": "CWS-1"}), \
                mock.patch("pearnly_cowork.ocr.execution.service.submit", return_value={"state": "queued"}):
            intake._admit("U1", "m1", "a.pdf", {}, "th", "tok")
        return (stored[0] if stored else None), said

    def test_a_line_file_of_exactly_the_shared_upload_limit_is_stored(self):
        # 11-11：与网页、CC 对话、CC 桌面版文件夹同一个上限（原来按下载函数默认的 20MB）
        from pearnly_core.originals.rules import UPLOAD_MAX_BYTES

        self.assertEqual(UPLOAD_MAX_BYTES, 40 * 1024 * 1024)
        self.assertEqual(self._admit_line_file(UPLOAD_MAX_BYTES), (UPLOAD_MAX_BYTES, []))

    def test_a_line_file_over_the_limit_is_not_stored_and_says_how_large(self):
        # 原来 20MB 起只回「没能保存这份文件」；现在超 40MB 回四语词条 upload.tooLarge（「文件超过 40 MB」）
        from pearnly_core.originals.rules import UPLOAD_MAX_BYTES

        for declared in (True, False):
            self.assertEqual(self._admit_line_file(UPLOAD_MAX_BYTES + 1, declared), (None, ["upload.tooLarge"]), declared)

    def test_changed_binding_does_not_download_or_charge(self):
        with mock.patch.object(intake.line_binding, "user_of", return_value="other@x"), \
                mock.patch.object(intake.line, "download_message_content") as download:
            intake.store("U1", "a@x", "m1", "a.jpg", {}, "th")
        download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
