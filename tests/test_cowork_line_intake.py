"""Cowork LINE「发单据进 ERP」（用户 2026-09-26：照 ERP 新做法）：快捷回复逐步选、只认当前一步、收单进网页同一条链。"""
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


class LineFlowTest(unittest.TestCase):
    def setUp(self):
        self.session, self.replies = {}, []
        store = self.session

        def set_session(user, state, payload, ttl_minutes=None):
            store[user] = {"state": state, "payload": payload}

        patches = [
            mock.patch.object(flow, "_", side_effect=lambda text, *a, **k: text),
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

    def test_mrerp_sales_types_a_word_then_picks_the_account_set(self):
        """用户 2026-09-27：账套上传前由用户选；LINE 放不下全部账套，打几个字从匹配结果里点序号。"""
        flow.start("U1", "tok")
        self.tap("MR.ERP")
        self.assertTrue(self.last()["text"].startswith("Type a few letters"))
        self.assertEqual(self.labels(), ["Cancel"])
        self.assertTrue(self.search("2568"))
        self.assertIn("1. A · Alpha · 2568", self.last()["text"])
        self.assertEqual(self.labels(), ["1", "Cancel"])
        self.tap("1")
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

    def test_last_used_is_one_tap(self):
        self.last_used = "15:1"
        flow.start("U1", "tok")
        self.tap("MR.ERP")
        self.assertIn("Last used: A · Alpha · 2567", self.last()["text"])
        self.tap("Last used")
        self.assertEqual(flow.choices("U1")["account_set_key"], "15:1")

    def test_a_single_account_set_is_taken_without_asking(self):
        self.books = MRERP_BOOKS[:1]
        flow.start("U1", "tok")
        self.tap("MR.ERP")
        self.assertTrue(self.last()["text"].startswith("Purchase or sales?"))
        self.assertEqual(flow.choices("U1")["account_set_key"], "15:1")

    def test_express_search_hides_unwritable_books_then_asks_posting_kind(self):
        with mock.patch.object(flow, "_connections", return_value={"express": EXPRESS}), \
                mock.patch.object(flow, "_state", return_value=(True, "")):
            flow.start("U1", "tok")
            self.tap("Express")
            self.search("acme")
            self.assertEqual(self.last()["text"].splitlines()[1:],
                             ["1. 69EXP · 69ACME DATA（Acme）", "2. 68EXP · 68ACME AUDITED（Acme）"])
            self.tap("2")
            self.tap("intake.dir.purchase")
            self.assertEqual(self.labels(), ["Service", "Stock", "Cancel"])
            self.tap("Stock")
            payload = flow.choices("U1")
        self.assertTrue(flow.ready(payload))
        self.assertEqual((payload["connection"], payload["account_set_key"], payload["posting_kind"]),
                         ("CON-X", r"s:\2568\exp68\68acme", "stock"))
        # 选定 Express 账套就让助手读这份账套的主档（推送前的库存检查用）；MR.ERP 不需要。
        self.assertEqual(self.refresh.call_args.args[0], "CON-X")
        self.assertEqual(self.refresh.call_args.args[2], r"s:\2568\exp68\68acme")

    def express_stock(self):
        with mock.patch.object(flow, "_connections", return_value={"express": EXPRESS}), \
                mock.patch.object(flow, "_state", return_value=(True, "")):
            flow.start("U1", "tok")
            self.tap("Express")
            self.search("acme")
            self.tap("2")
            self.tap("intake.dir.purchase")
            self.tap("Stock")

    def test_express_stock_asks_the_stock_account_group_once_for_the_account_set(self):
        """用户 2026-09-28：合格组两个以上又没选过，点序号选一次，存回这份账套。"""
        groups = [{"acccod": "SA", "label": "SA · 11-04-01-00 · Stock A"},
                  {"acccod": "SB", "label": "SB · 11-04-02-00 · Stock B"}]
        self.stock_view = {"known": True, "groups": groups, "chosen": None}
        self.express_stock()
        self.assertEqual(self.last()["text"].splitlines()[1:],
                         ["1. SA · 11-04-01-00 · Stock A", "2. SB · 11-04-02-00 · Stock B"])
        self.assertEqual(self.labels(), ["1", "2", "Cancel"])
        self.tap("2")
        self.choose.assert_called_once_with("CON-X", r"s:\2568\exp68\68acme", "SB")
        self.assertTrue(flow.ready(flow.choices("U1")))
        self.assertTrue(self.last()["text"].endswith("the text the right way up and the whole page in frame."))

    def test_express_stock_does_not_ask_when_there_is_nothing_to_choose_here(self):
        """选过、只有一个、小助手还没读过这份账套、组多到一屏点不完：LINE 不问（网页提交前再问）。"""
        many = [{"acccod": f"S{i}", "label": f"S{i}"} for i in range(flow.MAX_RESULTS + 1)]
        two = [{"acccod": "SA", "label": "SA"}, {"acccod": "SB", "label": "SB"}]
        for view in ({"known": True, "groups": two, "chosen": "SA"}, {"known": True, "groups": two[:1], "chosen": None},
                     {"known": False, "groups": [], "chosen": None}, {"known": True, "groups": many, "chosen": None}):
            self.stock_view = view
            self.express_stock()
            self.assertTrue(flow.ready(flow.choices("U1")), view)
        self.choose.assert_not_called()

    def test_mrerp_choice_does_not_ask_for_an_account_read(self):
        self.books = MRERP_BOOKS[:1]
        flow.start("U1", "tok")
        self.tap("MR.ERP")
        self.refresh.assert_not_called()

    def test_mrerp_purchase_is_credit_only(self):
        self.books = MRERP_BOOKS[:1]
        flow.start("U1", "tok")
        self.tap("MR.ERP")
        self.tap("intake.dir.purchase")
        self.assertEqual(flow.choices("U1")["payment"], "credit")
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
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0]["action"]["uri"], "https://t.example/desk/pearnly-intake/new/CWH-1")

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


class LineAnnounceTest(unittest.TestCase):
    def test_only_the_first_announcement_is_sent(self):
        cache = {intake._PENDING + "CWS-1": {"line_user_id": "U1", "language": "th", "reply_token": "tok"}}
        fake = mock.Mock(get_value=cache.get, delete_value=lambda key: cache.pop(key, None))
        with mock.patch.object(intake.frappe, "cache", fake), \
                mock.patch.object(intake.frappe, "local", mock.Mock(lang="zh")), \
                mock.patch("pearnly_cowork.line.cards.previews", return_value=[{"type": "flex"}]), \
                mock.patch.object(intake.line, "reply_or_push") as send:
            intake.announce("CWS-1", "saved", ["H1"])
            intake.announce("CWS-1", "saved", ["H1"])
        send.assert_called_once_with("U1", [{"type": "flex"}], "tok")

    def test_uploads_not_from_line_are_ignored(self):
        fake = mock.Mock(get_value=lambda key: None)
        with mock.patch.object(intake.frappe, "cache", fake), \
                mock.patch.object(intake.line, "reply_or_push") as send:
            intake.announce("CWS-2", "saved", ["H1"])
        send.assert_not_called()

    def test_changed_binding_does_not_download_or_charge(self):
        with mock.patch.object(intake.line_binding, "user_of", return_value="other@x"), \
                mock.patch.object(intake.line, "download_message_content") as download:
            intake.store("U1", "a@x", "m1", "a.jpg", {}, "th")
        download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
