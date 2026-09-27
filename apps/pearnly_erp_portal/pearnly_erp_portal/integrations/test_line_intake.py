"""ERP LINE intake regressions (user-approved flow of 2026-09-25).

Runs in the pinned Frappe runtime via unittest. No Site, database, credentials, OCR,
outbound LINE or DMS call is used: collaborators are replaced and network is blocked.
"""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import frappe

from pearnly_erp_portal.integrations import handlers, intake, line_flow


def _same(text, *args, **kwargs):
    return text


class Base(unittest.TestCase):
    def setUp(self):
        self.replies = []
        self.sessions = {}
        self.roles = {"buyer": {"purchase.write", "integration.read"}}
        self.enterContext(patch.object(frappe, "_", _same, create=True))
        for module in (handlers, intake, line_flow):
            self.enterContext(patch.object(module, "_", _same))
        self.enterContext(patch.object(frappe, "session", SimpleNamespace(user="Guest"), create=True))
        self.enterContext(patch.object(frappe, "db", Mock(**{"get_value.return_value": None}), create=True))
        self.enterContext(patch.object(frappe, "throw", side_effect=self.reject))
        self.enterContext(patch.object(frappe, "set_user", lambda user: setattr(frappe.session, "user", user)))
        self.enterContext(patch("pearnly_core.line.reply", side_effect=lambda token, messages: self.replies.append(messages) or True))
        self.enterContext(patch("pearnly_core.line.reply_or_push",
                                side_effect=lambda to, messages, token="", **kw: self.replies.append(messages) or True))
        self.enterContext(patch("pearnly_core.line_session.get_session", side_effect=lambda uid: self.sessions.get(uid)))
        self.enterContext(patch("pearnly_core.line_session.set_session",
                                side_effect=lambda uid, state, payload, ttl_minutes=None: self.sessions.__setitem__(uid, {"state": state, "payload": dict(payload)})))
        self.enterContext(patch("pearnly_core.line_session.clear_session", side_effect=lambda uid, **kw: self.sessions.pop(uid, None)))
        self.enterContext(patch("pearnly_erp_portal.access.guard.has",
                                side_effect=lambda capability, user=None: capability in self.roles.get(user or frappe.session.user, set())))
        self.network = self.enterContext(patch("requests.sessions.Session.request",
                                               side_effect=AssertionError("External network forbidden")))

    @staticmethod
    def reject(message, exception=frappe.ValidationError, **kwargs):
        raise exception(message)

    def last_text(self):
        return self.replies[-1][0]["text"]

    def buttons(self):
        return [item["action"] for item in self.replies[-1][0]["quickReply"]["items"]]


class Flow(Base):
    def setUp(self):
        super().setUp()
        self.companies = ["CO-1"]
        self.enterContext(patch("pearnly_erp_portal.masters.company.allowed_companies",
                                side_effect=lambda user=None: list(self.companies)))
        self.enterContext(patch.object(line_flow, "_options", side_effect=self.options))
        self.enterContext(patch.object(line_flow, "_default_warehouse", return_value="WH-1"))
        self.enterContext(patch("frappe.utils.get_url", return_value="https://testerp.pearnly.com"))

    def options(self, step, payload, user):
        return {"entry": [("upload", "Upload and recognise")],
                "company": [("CO-1", "Alpha"), ("CO-2", "Beta")],
                "party": [(line_flow.FROM_DOCUMENT, "From the document"), (line_flow.FALLBACK, "General supplier"),
                          ("SUP-1", "Acme")],
                "paid": [("1", "Paid"), ("0", "Unpaid")],
                "method": [("cash", "Cash"), ("transfer", "Transfer")],
                "kind": [("goods", "Stock item"), ("service", "Service")],
                "warehouse": [("WH-1", "Main")]}[step]

    def tap(self, action):
        handlers.handle({"type": "postback", "source": {"userId": "U1"}, "replyToken": "r",
                         "postback": {"data": action["data"]}})

    def test_steps_run_in_order_and_skip_single_company_and_default_warehouse(self):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=mode:purchase"})
            self.tap(self.buttons()[0])                        # Upload and recognise
            self.assertEqual(self.buttons()[0]["label"], "1. From the document")
            self.assertEqual(self.buttons()[-1]["data"], "a=erp&s=cancel")
            self.assertEqual(self.buttons()[1]["label"], "2. General supplier")
            self.tap(self.buttons()[2])                        # Acme
            self.tap(self.buttons()[0])                        # Paid
            self.tap(next(b for b in self.buttons() if "transfer" in b["data"]))
            self.tap(self.buttons()[0])                        # Stock item
        payload = self.sessions["U1"]["payload"]
        self.assertEqual(payload, {"direction": "purchase", "entry": "upload", "company": "CO-1", "party": "SUP-1",
                                   "is_paid": 1, "payment_method": "transfer", "item_kind": "goods",
                                   "warehouse": "WH-1"})
        self.assertTrue(line_flow.ready(payload))
        self.assertIn("Please upload a PDF or image", self.last_text())

    def test_several_companies_are_offered_as_numbered_buttons(self):
        self.companies = ["CO-1", "CO-2"]
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=mode%3Apurchase"})           # the old menu's encoding
            self.tap({"data": "a=erp&s=entry&v=upload"})
        self.assertEqual([b["label"] for b in self.buttons()], ["1. Alpha", "2. Beta", "Cancel"])

    def test_similar_company_names_stay_distinguishable_within_the_20_character_buttons(self):
        """录制问题 #26：按钮去掉通用词，提问里列出带编号的全名。"""
        self.companies = ["CO-1", "CO-2"]
        names = [("CO-1", "บริษัท เดโม่ เทรดดิ้ง จำกัด"), ("CO-2", "บริษัท เดโม่ เทคโนโลยี จำกัด (มหาชน)")]
        with patch.object(line_flow, "_options", side_effect=lambda step, payload, user:
                          names if step == "company" else self.options(step, payload, user)), \
                patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=mode:purchase"})
            self.tap({"data": "a=erp&s=entry&v=upload"})
        self.assertEqual([b["label"] for b in self.buttons()], ["1. เดโม่ เทรดดิ้ง", "2. เดโม่ เทคโนโลยี", "Cancel"])
        self.assertEqual(self.last_text().splitlines()[1:],
                         ["1. บริษัท เดโม่ เทรดดิ้ง จำกัด", "2. บริษัท เดโม่ เทคโนโลยี จำกัด (มหาชน)"])
        self.assertEqual(line_flow.short_name("Acme Co., Ltd."), "Acme")
        self.assertEqual(line_flow.short_name("บริษัท จำกัด"), "บริษัท จำกัด")

    def test_stale_or_forged_answers_do_not_advance(self):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=mode:purchase"})
            self.tap({"data": "a=erp&s=kind&v=goods"})         # not the current step
            self.assertEqual(self.last_text(), "This option is no longer current")
            self.tap({"data": "a=erp&s=party&v=SUP-404"})      # not an offered value
            self.assertEqual(self.last_text(), "This option is no longer current")
        self.assertNotIn("party", self.sessions["U1"]["payload"])

    def test_unpaid_skips_method_and_service_skips_warehouse(self):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            for data in ("a=mode:purchase", "a=erp&s=entry&v=upload", "a=erp&s=party&v=%40doc", "a=erp&s=paid&v=0",
                         "a=erp&s=kind&v=service"):
                self.tap({"data": data})
        payload = self.sessions["U1"]["payload"]
        self.assertEqual((payload["is_paid"], payload["payment_method"], payload.get("warehouse")), (0, "", None))
        self.assertTrue(line_flow.ready(payload))

    def test_manual_entry_is_a_link_to_the_blank_native_page(self):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=mode:purchase"})
        upload, manual, cancel = self.buttons()
        self.assertEqual((upload["label"], upload["data"]), ("Upload and recognise", "a=erp&s=entry&v=upload"))
        self.assertEqual(manual, {"type": "uri", "label": "Enter manually",
                                  "uri": "https://testerp.pearnly.com/desk/pearnly-purchase/new"})
        self.assertEqual(cancel["data"], "a=erp&s=cancel")

    def test_role_and_binding_are_checked_before_any_step(self):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=mode:sales"})
        self.assertEqual(self.last_text(), "This account cannot send documents")
        with patch("pearnly_core.line_binding.user_of", return_value=None):
            self.tap({"data": "a=mode:purchase"})
        self.assertEqual(self.last_text(), "Bind this account first")
        self.assertNotIn("U1", self.sessions)

    def test_cancel_clears_the_choices(self):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=mode:purchase"})
            self.tap({"data": "a=erp&s=cancel"})
        self.assertNotIn("U1", self.sessions)
        self.assertEqual(self.last_text(), "Cancelled")


class Documents(Base):
    def event(self):
        return {"type": "message", "source": {"userId": "U1"}, "replyToken": "r",
                "message": {"type": "image", "id": "M1"}}

    def test_document_before_choices_is_refused_without_download(self):
        download = self.enterContext(patch("pearnly_core.line.download_message_content"))
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            handlers.handle(self.event())
        self.assertEqual(self.last_text(), "Choose purchase or sales first")
        download.assert_not_called()

    def test_ready_document_is_received_as_the_bound_user_and_guest_restored(self):
        self.sessions["U1"] = {"state": line_flow.STATE, "payload": {"direction": "purchase", "entry": "upload", "company": "CO-1",
            "party": "@doc", "is_paid": 0, "payment_method": "", "item_kind": "service"}}
        download = self.enterContext(patch("pearnly_core.line.download_message_content", return_value=b"file"))
        seen = {}
        receive = self.enterContext(patch.object(intake, "receive",
                                    side_effect=lambda **kw: seen.update(user=frappe.session.user, **kw)))
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            handlers.handle(self.event())
        receive.assert_called_once()
        self.assertEqual((seen["user"], seen["sent_by"], seen["choices"]["company"]), ("buyer", "buyer", "CO-1"))
        self.assertEqual(frappe.session.user, "Guest")
        # 回调里不下载（09-25 发图 499）：只登记排队，识别任务里再取。
        download.assert_not_called()
        self.assertEqual((seen["content"], seen["line_message_id"]), (None, "M1"))

    def test_the_recognition_job_fetches_the_document_once(self):
        from pearnly_core import line

        doc = _doc(status=intake.QUEUED, language="th", source_file_name=None, source_file=None, line_message_id="M1")
        # 与 Frappe `Document.update(d)` 同一签名（传字典）：09-26 用关键字参数写错，测试替身没拦住。
        doc.update = lambda values: [setattr(doc, key, value) for key, value in values.items()]
        doc.get = lambda key, default=None: getattr(doc, key, default)
        doc.save = lambda **kw: None
        doc.reload = lambda: None
        stored = SimpleNamespace(name="FILE-9", file_url="/private/files/x.jpg")
        self.enterContext(patch.object(frappe, "get_doc", side_effect=lambda *a, **kw: doc if a and a[0] == intake.DOCTYPE
                                       else SimpleNamespace(insert=lambda **k: stored), create=True))
        self.enterContext(patch("pearnly_core.line_binding.user_of", return_value="buyer"))
        download = self.enterContext(patch.object(line, "download_message_content", return_value=b"img"))
        seen = {}
        self.enterContext(patch.object(intake, "_recogniser", return_value=SimpleNamespace(
            recognise=lambda files, **kw: seen.setdefault("files", files) and {"lines": []})))
        self.enterContext(patch.object(intake, "totals", return_value={}))
        self.enterContext(patch("pearnly_erp_portal.integrations.line_cards.preview", return_value={}))
        intake.run("LI-1", "U1", filename="line-M1.jpg")
        download.assert_called_once_with("M1")
        self.assertEqual((doc.source_file_name, seen["files"]), ("FILE-9", [{"file": "FILE-9", "kind": "bill"}]))

    def test_each_document_in_a_file_gets_its_own_record_and_card(self):
        from pearnly_core import line

        doc = _doc(status=intake.QUEUED, language="th")
        doc.reload = lambda: None
        doc.save = lambda **kw: None
        inserted = []

        def get_doc(value, name=None):
            if isinstance(value, dict) and value.get("doctype") == "File":
                return SimpleNamespace(insert=lambda **kw: SimpleNamespace(name="FILE-X", file_url="/private/files/x.jpg"))
            if isinstance(value, dict):
                return SimpleNamespace(insert=lambda **kw: inserted.append(value) or SimpleNamespace(
                    **value, get=value.get, name=f"NEW-{len(inserted)}"))
            if value == "File":
                return SimpleNamespace(get_content=lambda: b"img")
            return doc

        self.enterContext(patch.object(frappe, "get_doc", side_effect=get_doc, create=True))
        self.enterContext(patch("pearnly_core.line_binding.user_of", return_value="buyer"))
        self.enterContext(patch.object(intake, "totals", return_value={}))
        self.enterContext(patch.object(intake, "_recogniser", return_value=SimpleNamespace(
            recognise=lambda files, **kw: {"doc_no": "A", "lines": [], "additional": [{"doc_no": "B", "lines": []},
                                                                                 {"doc_no": "C", "lines": []}]})))
        self.enterContext(patch("pearnly_erp_portal.integrations.line_cards.preview",
                                side_effect=lambda card, data, totals: data["doc_no"]))
        intake.run("LI-1", "U1")
        siblings = [row for row in inserted if row.get("doctype") == intake.DOCTYPE]
        self.assertEqual([json.loads(row["result"])["doc_no"] for row in siblings], ["B", "C"])
        self.assertEqual({row["status"] for row in siblings}, {intake.READY})
        self.assertEqual(self.replies[-1], ["A", "B", "C"])
        self.assertNotIn("additional", json.loads(doc.result))

    def test_a_document_that_cannot_be_fetched_fails_and_says_so(self):
        from pearnly_core import line

        doc = _doc(status=intake.QUEUED, language="th", source_file_name=None, line_message_id="M1")
        self.enterContext(patch.object(frappe, "get_doc", return_value=doc, create=True))
        self.enterContext(patch("pearnly_core.line_binding.user_of", return_value="buyer"))
        self.enterContext(patch.object(line, "download_message_content", side_effect=RuntimeError("expired")))
        self.enterContext(patch.object(frappe, "log_error", create=True))
        dropped = self.enterContext(patch.object(intake, "_drop"))
        self.assertEqual(intake.run("LI-1", "U1"), {"failed": "download_failed", "notified": True})
        dropped.assert_called_once_with(doc, intake.FAILED, "download_failed")
        self.assertEqual(self.last_text(), "Could not store this document")


def _raise(message, exc=Exception):
    raise exc(message)


def _doc(**values):
    base = dict(name="LI-1", direction="purchase", company="CO-1", party="SUP-1", is_paid=1,
                payment_method="transfer", item_kind="goods", warehouse="WH-1", status="ready",
                purchase=None, sales_document=None, line_user_id="U1", source_file_name="FILE-1", sent_by="buyer", source_file="/private/files/a.jpg",
                result=json.dumps({"doc_no": "INV-1", "doc_date": "2026-09-25", "supplier_name": "Acme",
                                   "lines": [{"description": "Soap", "qty": 2, "unit_price": 10}]}))
    base.update(values)
    return SimpleNamespace(**base, get=lambda key, default=None: base.get(key, default))


class Payload(Base):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(intake, "_file_name", return_value="FILE-1"))

    def test_purchase_payload_uses_the_chosen_masters_and_stock_choice(self):
        payload = intake.build_payload(_doc())
        self.assertEqual((payload["company"], payload["supplier"], payload["is_paid"], payload["payment_method"]),
                         ("CO-1", "SUP-1", 1, "transfer"))
        self.assertEqual((payload["update_stock"], payload["auto_create_product"]), (1, 1))
        self.assertEqual(payload["lines"][0]["item_type"], "goods")
        self.assertEqual(payload["lines"][0]["warehouse"], "WH-1")
        self.assertEqual(payload["attachments"], [{"file": "FILE-1", "kind": "bill"}])
        self.assertNotIn("company", json.loads(_doc().result))  # OCR never chooses the company

    def test_purchase_from_document_and_service_does_not_touch_stock(self):
        payload = intake.build_payload(_doc(party="@doc", item_kind="service", warehouse=None))
        self.assertNotIn("supplier", payload)
        self.assertEqual(payload["supplier_name"], "Acme")
        self.assertEqual((payload["update_stock"], payload["lines"][0]["item_type"]), (0, "service"))
        self.assertNotIn("warehouse", payload["lines"][0])

    def test_fallback_parties_ignore_the_document_party(self):
        purchase = intake.build_payload(_doc(party="@any", result=json.dumps({"doc_no": "INV-1",
            "supplier_name": "Shop", "supplier_tax_id": "0105", "lines": []})))
        self.assertEqual((purchase["supplier_name"], "supplier" in purchase, "supplier_tax_id" in purchase),
                         ("General supplier", False, False))
        sales = intake.build_payload(_doc(direction="sales", party="@any", result=json.dumps(
            {"buyer_name": "Somchai", "address": "Bangkok", "lines": []})))
        self.assertEqual((sales["buyer_name"], sales["party_type"], "customer" in sales, "address" in sales),
                         ("Walk-in customer", "anonymous", False, False))

    def test_sales_payload_is_unpaid_and_names_an_unknown_buyer(self):
        doc = _doc(direction="sales", party="@doc", is_paid=0, payment_method="",
                   result=json.dumps({"doc_date": "2026-09-25", "lines": [{"description": "Soap", "qty": 1, "unit_price": 5}]}))
        payload = intake.build_payload(doc)
        self.assertEqual((payload["is_paid"], payload["paid_amount"], payload["buyer_name"]), (0, 0, "Unknown buyer"))
        self.assertEqual(payload["lines"][0]["warehouse"], "WH-1")


    def test_paid_sales_takes_the_document_date_and_vat_mode(self):
        # 定·09-25：付款日期取单据日期（09-26 实测记成了当天）；含税小票不再加一次 VAT。
        doc = _doc(direction="sales", party="@any", payment_method="cash", result=json.dumps(
            {"doc_date": "2026-05-27", "price_includes_vat": True,
             "lines": [{"description": "Tweezers", "qty": 1, "unit_price": 79}]}))
        payload = intake.build_payload(doc)
        self.assertEqual((payload["payment_date"], payload["price_includes_vat"]), ("2026-05-27", True))

    def test_fallback_names_are_stored_in_the_channel_language(self):
        seen = []
        self.enterContext(patch.object(intake, "channel_language", return_value="th"))
        self.enterContext(patch.object(intake, "_", lambda text, lang=None: seen.append(lang) or text))
        intake.build_payload(_doc(direction="sales", party="@any", result=json.dumps({"lines": []})))
        self.assertEqual(seen, ["th"])


class Language(Base):
    def test_replies_follow_the_phone_language_and_restore_the_site_language(self):
        from pearnly_core import line

        frappe.local.lang = "th"
        seen = []
        self.enterContext(patch.object(line, "user_language", return_value="zh"))
        self.enterContext(patch.object(handlers, "_handle", side_effect=lambda event: seen.append(frappe.local.lang)))
        handlers.handle({"type": "follow", "source": {"userId": "U1"}})
        self.assertEqual((seen, frappe.local.lang), (["zh"], "th"))

    def test_phone_language_is_read_from_the_line_profile(self):
        from pearnly_core import line

        cache = {}
        self.enterContext(patch.object(frappe, "cache", Mock(**{"get_value.side_effect": cache.get,
            "set_value.side_effect": lambda key, value, expires_in_sec=None: cache.__setitem__(key, value)}), create=True))
        def reply(status, body):
            return Mock(status_code=status, json=Mock(return_value=body))

        self.enterContext(patch.object(line, "_headers", return_value={}))
        for tag, expected in (("zh-Hant", "zh"), ("ja", "ja"), ("ko", "th"), (None, "th")):
            cache.clear()
            with patch("requests.get", return_value=reply(200, {"language": tag} if tag else {})) as profile:
                self.assertEqual(line.user_language("U1", default="th"), expected)
                self.assertEqual(line.user_language("U1", default="th"), expected)
                profile.assert_called_once()  # 第二次走缓存
        cache.clear()
        with patch("requests.get", return_value=reply(429, {})) as profile:
            self.assertEqual(line.user_language("U1", default="th"), "th")
            self.assertEqual(line.user_language("U1", default="th"), "th")
            self.assertEqual(profile.call_count, 2)  # 没问到不记缓存

    def test_the_card_uses_the_language_recorded_when_the_document_arrived(self):
        from pearnly_core import line

        seen = {}
        doc = _doc(status=intake.QUEUED, language="zh")
        doc.reload = lambda: None
        doc.save = lambda **kw: None
        self.enterContext(patch.object(frappe, "get_doc", return_value=doc, create=True))
        self.enterContext(patch("pearnly_core.line_binding.user_of", return_value="buyer"))
        self.enterContext(patch.object(line, "user_language", side_effect=AssertionError("must not ask LINE")))
        self.enterContext(patch.object(intake, "_file_name", return_value="FILE-1"))
        self.enterContext(patch.object(intake, "totals", return_value={}))
        self.enterContext(patch.object(intake, "_recogniser", return_value=SimpleNamespace(
            recognise=lambda files, **kw: seen.setdefault("lang", frappe.local.lang) and {"lines": []})))
        self.enterContext(patch("pearnly_erp_portal.integrations.line_cards.preview", return_value={}))
        self.enterContext(patch.object(frappe.db, "commit", create=True))
        intake.run("LI-1", "U1")
        self.assertEqual(seen["lang"], "zh")


class RichMenu(Base):
    def test_tiles_follow_permissions_and_stocktake_opens_the_native_page(self):
        from pearnly_erp_portal.integrations import line_menu

        self.enterContext(patch.object(line_menu, "_", _same))
        self.enterContext(patch("frappe.utils.get_url", return_value="https://testerp.pearnly.com"))
        self.assertEqual(len(line_menu.VARIANTS), 7)
        areas = line_menu.payload("purchase+stocktake", "zh")["areas"]
        self.assertEqual([area["bounds"]["x"] for area in areas], [0, 1666])  # 销售那格是锁、不可点
        self.assertEqual(areas[0]["action"]["data"], "a=mode:purchase")
        self.assertEqual(areas[1]["action"], {"type": "uri", "label": "Stock count",
                         "uri": "https://testerp.pearnly.com/api/method/pearnly_core.account.line_login.enter?to=%2Fdesk%2Fpearnly-stock-count"})
        self.roles["buyer"] = {"integration.read", "purchase.write", "count.write"}
        self.assertEqual(line_menu.wanted_variant("buyer"), "purchase+stocktake")
        self.roles["buyer"] = {"purchase.write"}
        self.assertEqual(line_menu.wanted_variant("buyer"), "")

    def test_menu_image_is_composed_from_the_tiles_for_each_language(self):
        import io
        from pathlib import Path

        from PIL import Image

        from pearnly_erp_portal.integrations import line_menu

        root = Path(line_menu.__file__).resolve().parents[1]
        self.enterContext(patch.object(frappe, "get_app_path", create=True,
                                       side_effect=lambda app, *parts: str(root.joinpath(*parts))))
        for language in line_menu.LANGUAGES:
            image = line_menu._image("purchase+sales+stocktake", language)
            self.assertEqual(Image.open(io.BytesIO(image)).size, (2500, 1686))
        self.assertEqual(line_menu._image("sales", "th"), line_menu._image("sales", "th"))  # 同内容同散列，重发复用

    def test_each_user_gets_the_menu_of_their_phone_language(self):
        from pearnly_erp_portal.integrations import line_menu

        menus = {line_menu.key(language, variant): f"{language}/{variant}"
                 for language in line_menu.LANGUAGES for variant in line_menu.VARIANTS}
        self.enterContext(patch.object(frappe, "conf", {line_menu.CONFIG: menus}, create=True))
        self.enterContext(patch("pearnly_core.line_binding.binding_of", return_value=SimpleNamespace(user="buyer")))
        self.roles["buyer"] = {"integration.read", "sales.write"}
        self.assertEqual(line_menu.wanted_menu("U1", "ja")[1], "ja/sales")
        self.enterContext(patch.object(frappe, "conf", {line_menu.CONFIG: {"both": "old", "sales": "old"}}, create=True))
        self.assertIsNone(line_menu.wanted_menu("U1", "ja")[0])  # 旧的三套泰文发布：不碰官方号


class Recognition(unittest.TestCase):
    def test_percent_discounts_become_amounts(self):
        # 09-26 采购票面「20.00%」被整个丢掉，合计多记 16,963.20。
        from pearnly_erp_portal.purchasing import recognition

        clean = recognition.clean_result({"lines": [
            {"description": "A", "qty": 48, "unit_price": 589, "discount": "20.00%"},
            {"description": "B", "qty": "60", "unit_price": "589.00", "discount": "1,000"},
            {"description": "C", "qty": 1, "unit_price": 10, "discount": "n/a"}],
            "discount_total": "10%"})
        self.assertEqual([row["discount"] for row in clean["lines"]], [5654.4, 1000.0, 0])
        self.assertEqual(clean["discount_total"], round((48 * 589 * 0.8 + 60 * 589 - 1000 + 10) * 0.1, 2))

    def test_vat_included_sales_prices_are_not_taxed_again(self):
        from pearnly_erp_portal.sales import entry

        base = {"has_vat": 1, "vat_rate": 7, "lines": [{"description": "Cool Betty Inliner", "qty": 1, "unit_price": 290},
                {"description": "Eyelash Curler", "qty": 1, "unit_price": 120},
                {"description": "Tweezers", "qty": 1, "unit_price": 79}]}
        included = entry._calculate({**base, "price_includes_vat": 1})
        self.assertEqual((included["grand_total"], included["vat_amount"]), ("489.00", "31.99"))
        self.assertEqual(entry._calculate(base)["grand_total"], "523.23")

    def test_sideways_photo_with_orientation_is_turned_upright_locally(self):
        import io

        from PIL import Image

        from pearnly_core import ocr

        def jpeg(orientation=None):
            out, image = io.BytesIO(), Image.new("RGB", (40, 20), "white")
            exif = image.getexif()
            if orientation:
                exif[0x0112] = orientation
            image.save(out, "JPEG", exif=exif)
            return out.getvalue()

        turned, mime = ocr.upright(jpeg(6), "image/jpeg")
        self.assertEqual((Image.open(io.BytesIO(turned)).size, mime), ((20, 40), "image/jpeg"))
        plain = jpeg()
        self.assertIs(ocr.upright(plain, "image/jpeg")[0], plain)
        self.assertEqual(ocr.upright(b"%PDF-1.4", "application/pdf"), (b"%PDF-1.4", "application/pdf"))


class Amounts(unittest.TestCase):
    def test_amount_only_lines_get_a_unit_price(self):
        # 09-26：小票只印「数量 + 金额」，单价全成了 0，整张单金额为 0。
        from pearnly_erp_portal.purchasing import recognition
        from pearnly_erp_portal.sales import contracts

        mapped = contracts.map_recognised({"doc_no": "02000137", "lines": [
            {"description": "Clinicare", "qty": 2, "amount": "940.00"},
            {"description": "Tweezers", "qty": 1, "unit_price": 79, "amount": 79},
            {"description": "Gift", "qty": 1}]})
        self.assertEqual([row["unit_price"] for row in mapped["lines"]], [470.0, 79.0, 0])
        self.assertEqual(mapped["doc_no"], "02000137")
        clean = recognition.clean_result({"lines": [{"description": "Tyre", "qty": 2, "unit_price": None, "amount": "1,500"}]})
        self.assertEqual(clean["lines"][0]["unit_price"], 750.0)
        self.assertNotIn("amount", clean["lines"][0])


class Checks(unittest.TestCase):
    """旧线上发票硬闸 / 软标注（`recognition.checks`），数字取自 09-26 真机那几张。"""

    def test_consistent_documents_pass(self):
        from pearnly_erp_portal.recognition import checks

        # กิจสยามยานยนต์ 税票：含税 7,500，税前 7,009.35 + VAT 490.65。
        printed = {"subtotal": "7,009.35", "discount": "0.00", "vat": "490.65", "total": "7,500.00"}
        self.assertEqual(checks.printed_issues(printed, [6400, 750, 350], seller_tax="0105542027023",
                                               buyer_tax="0105546015062"), [])
        self.assertEqual(checks.computed_issues(printed, "7500.00"), [])
        # Sister Makeup 含税小票（ABB）：明细和 2,768 = 合计，大于税前小计也放行。
        receipt = {"subtotal": "2,586.92", "vat": "181.08", "total": "2,768.00"}
        self.assertEqual(checks.printed_issues(receipt, [390, 390, 940, 79, 189, 780]), [])

    def test_a_vat_inclusive_amount_read_as_subtotal_is_not_a_mismatch(self):
        from pearnly_erp_portal.recognition import checks

        # 09-26 实测：模型把「รวมเป็นเงิน 7,500」当小计，VAT 490.65、合计 7,500 —— 票面本身是平的。
        self.assertEqual(checks.printed_issues({"subtotal": 7500, "discount": 0, "vat": 490.65, "total": 7500},
                                               [6400, 750, 350]), [])
        self.assertEqual(checks.printed_issues({"subtotal": 67852.8, "discount": 0, "vat": 4438.97,
                                                "total": 67852.8}, [22617.6, 28272, 16963.2]), [])

    def test_a_printed_line_amount_below_qty_times_price_is_a_discount(self):
        from pearnly_erp_portal.purchasing import recognition

        clean = recognition.clean_result({"lines": [
            {"description": "A", "qty": 6, "unit_price": 589, "amount": "2,827.20"},
            {"description": "B", "qty": 2, "unit_price": 589},
            {"description": "C", "qty": 1, "unit_price": 100, "amount": 100}]})
        self.assertEqual([row.get("discount") for row in clean["lines"]], [706.8, None, None])

    def test_percent_discount_column_becomes_an_amount(self):
        from pearnly_erp_portal.purchasing import recognition

        clean = recognition.clean_result({"lines": [{"description": "A", "qty": 48, "unit_price": 589,
                                                     "discount": None, "discount_percent": 20}]})
        self.assertEqual(clean["lines"][0]["discount"], 5654.4)

    def test_the_two_real_failures_are_caught(self):
        from pearnly_erp_portal.recognition import checks

        # NBC：票面合计 67,852.80，丢了 20% 折扣算出 84,816.00。
        self.assertEqual(checks.computed_issues({"total": "67,852.80"}, "84816.00"), ["computed_total"])
        # กิจสยามยานยนต์：合计对上了，但读成「无 VAT」—— 入账会少记 490.65 的税。
        self.assertEqual(checks.computed_issues({"total": "7500", "vat": "490.65"}, "7500.00", "0.00"), ["computed_vat"])
        self.assertEqual(checks.computed_issues({"total": "7500", "vat": "490.65"}, "7500.00", "490.65"), [])
        # 小票只印金额：单价全 0，算出 0，票面 2,768。
        self.assertEqual(checks.computed_issues({"total": "2768"}, "0.00"), ["computed_total"])

    def test_old_hard_gates(self):
        from pearnly_erp_portal.recognition import checks

        self.assertIn("total_missing", checks.printed_issues({"subtotal": "100"}, []))
        self.assertIn("printed_math", checks.printed_issues({"subtotal": "100", "vat": "7", "total": "120"}, []))
        self.assertIn("vat_ratio", checks.printed_issues({"subtotal": "100", "vat": "10", "total": "110"}, []))
        self.assertIn("negative", checks.printed_issues({"subtotal": "-100", "vat": "-7", "total": "-107"}, []))
        self.assertIn("same_tax_id", checks.printed_issues({"total": "1"}, [], seller_tax="0105555167627",
                                                           buyer_tax="0105-555-167627"))
        self.assertIn("tax_id_checksum", checks.printed_issues({"total": "1"}, [], seller_tax="0105555167628"))
        self.assertIn("total_below_line", checks.printed_issues({"total": "50"}, [100]))
        self.assertIn("line_sum", checks.printed_issues({"subtotal": "100", "total": "100"}, [80, 80]))
        # 折扣印在表尾：折前明细和 − 折扣 = 小计，不误杀（旧线上 f003 实案）。
        self.assertEqual(checks.printed_issues({"subtotal": "110", "discount": "5", "vat": "7.35",
                                                "total": "112.35"}, [60, 55]), [])

    def test_vat_mode_comes_from_the_printed_numbers(self):
        from pearnly_erp_portal.purchasing import recognition
        from pearnly_erp_portal.recognition import checks

        printed = {"subtotal": 7009.35, "vat": 490.65, "total": 7500}
        self.assertEqual(checks.vat_mode(printed, 7500), "inclusive")
        self.assertEqual(checks.vat_mode(printed, "7009.35"), "exclusive")
        self.assertIsNone(checks.vat_mode(printed, 1000))
        self.assertIsNone(checks.vat_mode({"total": 100}, 100))
        # 模型读成「无 VAT、不含税」，票面数字说它是含税的 —— 用票面的。
        result = recognition._structured({"has_vat": False, "price_mode": "exclusive", "printed": printed,
                                          "document_type": "tax_invoice", "buyer_tax_id": "0105546015062", "lines": [
            {"description": "Tyre", "qty": 1, "unit_price": 6400, "vat_rate": None, "discount": None},
            {"description": "Tube", "qty": 1, "unit_price": 750},
            {"description": "Flap", "qty": 1, "unit_price": 350}]})
        self.assertEqual((result["has_vat"], result["price_mode"]), (True, "inclusive"))
        self.assertEqual(recognition.review(result), [])

    def test_abbreviated_tax_invoices_do_not_claim_input_vat(self):
        from pearnly_erp_portal.purchasing import entry, recognition

        # 09-26 真机 7-Eleven 小票（ใบกำกับภาษีอย่างย่อ，VAT Included）：记成 122.43 + 进项税 8.57。
        lines = [{"description": "ข้าวผัดกะเพราหมู", "qty": 1, "unit_price": 47},
                 {"description": "LPขนมปังฟักทองลูกเก", "qty": 1, "unit_price": 35},
                 {"description": "Hซินเซน น้ำส้มคั้น", "qty": 1, "unit_price": 49}]
        raw = {"doc_kind": "purchase_invoice", "has_vat": True, "price_mode": "inclusive",
               "document_type": "simplified_tax_invoice", "lines": lines,
               "printed": {"subtotal": 122.43, "vat": 8.57, "total": 131}}
        result = recognition._structured(raw)
        self.assertIs(result["has_vat"], False)
        self.assertEqual(recognition.review(result), [])
        totals = entry._calculate({"lines": result["lines"], "has_vat": result["has_vat"],
                                   "price_mode": result["price_mode"]})
        self.assertEqual((str(totals["vat_amount"]), str(totals["grand_total"])), ("0.00", "131.00"))
        self.assertEqual([str(row["line_total"]) for row in totals["lines"]], ["47.00", "35.00", "49.00"])
        # 票面标题认得出就以标题为准：模型说是完整税票，标题写着「อย่างย่อ」照样不抵。
        titled = recognition._structured({**raw, "document_type": "tax_invoice",
                                          "document_title": "ใบเสร็จรับเงิน/ใบกำกับภาษีอย่างย่อ"})
        self.assertEqual((titled["document_type"], titled["document_type_basis"], titled["has_vat"]),
                         ("simplified_tax_invoice", "title", False))
        # 完整税票：印了 VAT、写明买方才记进项税；缺买方不记，并在卡上红字说明原因。
        full = recognition._structured({**raw, "document_type": "tax_invoice", "buyer_tax_id": "0105546015062"})
        self.assertIs(full["has_vat"], True)
        self.assertEqual(recognition.review(full), [])
        anonymous = recognition._structured({**raw, "document_type": "tax_invoice"})
        self.assertIs(anonymous["has_vat"], False)
        self.assertIn("tax_invoice_without_buyer", recognition.review(anonymous))
        # 认不出票种：不记进项税，交人看；这类原因只标注，不触发换模型重读（不多花识别费）。
        unknown = recognition._structured({**raw, "document_type": None})
        self.assertEqual((unknown["document_type"], unknown["has_vat"]), ("other", False))
        self.assertIn("document_type_unknown", recognition.review(unknown))
        from pearnly_erp_portal.recognition import checks

        self.assertEqual(checks.hard(recognition.review(unknown)), [])

    def test_corpus_receipts_without_quantities_or_with_discount_rows_still_add_up(self):
        from pearnly_erp_portal.purchasing import recognition

        base = {"document_type": "simplified_tax_invoice", "document_title": "ใบกำกับภาษีอย่างย่อ"}
        # 语料 9c2c…：小票不印数量 —— 当 1 件，合计与票面一致。
        no_qty = recognition._structured({**base, "printed": {"subtotal": 204.67, "vat": 14.33, "total": 219}, "lines": [
            {"description": "น้ำเปล่า", "qty": 2, "unit_price": 7}, {"description": "ข้าวกะเพรา", "qty": None, "unit_price": 75},
            {"description": "กาแฟดำ", "unit_price": 50}, {"description": "มักกะโรนี", "amount": 80}]})
        self.assertEqual([row["qty"] for row in no_qty["lines"]], [2, 1, 1, 1])
        self.assertEqual(recognition.review(no_qty), [])
        # 语料 fd54…：折扣行没有单价、只有折扣 —— 并进整单折扣，不算商品。
        coupon = recognition._structured({**base, "printed": {"total": 278}, "lines": [
            {"description": "มันหวาน", "qty": 1, "unit_price": 29}, {"description": "น้ำดื่ม", "qty": 2, "unit_price": 7},
            {"description": "นมโปร", "qty": 3, "unit_price": 49}, {"description": "อเมริกาโน่", "qty": 2, "unit_price": 45},
            {"description": "ส่วนลดโปรเทีนโปรคืนสูง", "qty": 1, "unit_price": None, "discount": 2}]})
        self.assertEqual((len(coupon["lines"]), coupon["discount_total"]), (4, 2.0))
        self.assertEqual(recognition.review(coupon), [])
        # 语料 2470…：模型把同一笔折扣既读成一行、又读进整单折扣 —— 不重复扣。
        twice = recognition._structured({**base, "discount_total": 5, "printed": {"total": 110}, "lines": [
            {"description": "อเมริกาโน่เย็น", "qty": 2, "unit_price": 45}, {"description": "ข้าวโพด", "qty": 1, "unit_price": 25},
            {"description": "ส่วนลดข้าวโพด", "qty": 1, "unit_price": 5}]})
        self.assertEqual((len(twice["lines"]), twice["discount_total"]), (2, 5))
        self.assertEqual(recognition.review(twice), [])

    def test_withholding_on_the_printed_total_is_carried_to_the_lines(self):
        from pearnly_erp_portal.purchasing import entry, recognition
        from pearnly_erp_portal.recognition import checks

        # 语料 v2_007：1,946 + VAT 136.22 − 3% 预扣 58.38 = 实付 2,023.84，模型没读出预扣税率。
        raw = {"document_type": "tax_invoice", "buyer_tax_id": "0105546015062",
               "printed": {"subtotal": 1946, "vat": 136.22, "total": 2023.84}, "lines": [
                   {"description": "ค่าซ่อมเครื่องพิมพ์", "qty": 4, "unit_price": 154},
                   {"description": "กระดาษ A4", "qty": 1, "unit_price": 183},
                   {"description": "หมึกพิมพ์", "qty": 2, "unit_price": 212},
                   {"description": "กาแฟ", "qty": 3, "unit_price": 241}]}
        result = recognition._structured(raw)
        self.assertEqual({row["wht_rate"] for row in result["lines"]}, {3.0})
        totals = entry._calculate({"lines": result["lines"], "has_vat": True, "price_mode": result["price_mode"]})
        self.assertEqual((str(totals["grand_total"]), str(totals["net_payable"])), ("2082.22", "2023.84"))
        self.assertNotIn("printed_math", recognition.review(result))
        # 等式不成立（或几档都说得通）就不猜。
        self.assertIsNone(checks.withholding_rate(1000, 70, 1051))
        self.assertEqual(checks.withholding_rate(1000, 70, 1000, wht_amount=30), 3)
        self.assertEqual(checks.money("(183.82)"), -checks.money("183.82"))
        # 语料 v2_006：5% 折扣与 5% 预扣的算式凑巧相同 —— 扣了折扣已经平，就不是预扣税。
        discounted = recognition._structured({**raw, "discount_total": 63.5,
            "printed": {"subtotal": 1270, "discount": 63.5, "vat": 84.46, "total": 1290.96},
            "lines": [{"description": "a", "qty": 3, "unit_price": 137}, {"description": "b", "qty": 4, "unit_price": 166},
                      {"description": "c", "qty": 1, "unit_price": 195}]})
        self.assertFalse(any(row.get("wht_rate") for row in discounted["lines"]))
        # 语料 v2_005：整张单只有一行 0 元「ส่วนลดคูปอง」—— 照常留作一行。
        zero = recognition._structured({"document_type": "tax_invoice", "printed": {"total": 0},
                                        "lines": [{"description": "ส่วนลดคูปองเต็มจำนวน", "qty": 1, "unit_price": 0}]})
        self.assertEqual(len(zero["lines"]), 1)

    def test_a_missed_bill_discount_is_derived_only_when_the_printed_vat_confirms_it(self):
        from pearnly_erp_portal.purchasing import entry, recognition

        # 语料 Giffarine（ADR-013）：3.1 把整单折扣 3,162 读成 0；票面 VAT 631.18 = 9,648 × 7/107 印证折后金额。
        lines = [{"description": "Giffarine A", "qty": 40, "unit_price": 300}, {"description": "Giffarine B", "qty": 6,
                                                                                    "unit_price": 135}]
        raw = {"document_type": "tax_invoice", "buyer_tax_id": "0105567178203", "discount_total": 0,
               "printed": {"subtotal": 9016.82, "discount": 0, "vat": 631.18, "total": 9648}, "lines": lines}
        result = recognition._structured(raw)
        self.assertEqual(result["discount_total"], 3162.0)
        totals = entry._calculate({"lines": result["lines"], "has_vat": True, "price_mode": result["price_mode"],
                                   "discount_total": result["discount_total"]})
        self.assertEqual((str(totals["vat_amount"]), str(totals["grand_total"])), ("631.18", "9648.00"))
        self.assertEqual(recognition.review(result), [])
        # 票面 VAT 对不上折后金额：不猜，照常标红。
        unproven = recognition._structured({**raw, "printed": {"subtotal": 9016.82, "vat": 700, "total": 9648}})
        self.assertFalse(unproven.get("discount_total"))
        self.assertIn("computed_total", recognition.review(unproven))

    def test_ocr_text_confirms_our_company_as_buyer_and_recovers_a_misread_tax_id(self):
        from pearnly_erp_portal.purchasing import recognition

        # 语料 SM 采购税票：3.1 没读到买方 → 判成「没写买方、不抵进项」；Vision 原文里印着本公司税号。
        raw = {"document_type": "tax_invoice", "supplier_tax_id": "0107542000017",
               "printed": {"subtotal": 2407.48, "vat": 168.52, "total": 2576},
               "lines": [{"description": "อุปกรณ์สำนักงาน", "qty": 1, "unit_price": 2576}]}
        result = recognition._structured(raw)
        issues = recognition.review(result)
        self.assertEqual((result["has_vat"], "tax_invoice_without_buyer" in issues, "tax_id_checksum" in issues),
                         (False, True, True))
        text = "ใบกำกับภาษี\nเลขประจำตัวผู้เสียภาษี 0107542000011\nลูกค้า บริษัท ซิสเตอร์ เมคอัพ\nTax ID 0105567178203\n2,576.00"
        fixed, found = recognition._anchor(result, issues, text, "0105567178203")
        self.assertEqual((fixed["buyer_tax_id"], fixed["supplier_tax_id"], fixed["has_vat"], found),
                         ("0105567178203", "0107542000011", True, []))
        # 买卖读反：本公司税号被读进卖方 —— 挪到买方，卖方取原文里另一个合法税号。
        swapped = recognition._structured({**raw, "supplier_tax_id": "0105567178203"})
        fixed, found = recognition._anchor(swapped, recognition.review(swapped), text, "0105567178203")
        self.assertEqual((fixed["buyer_tax_id"], fixed["supplier_tax_id"], fixed["has_vat"]),
                         ("0105567178203", "0107542000011", True))
        # 原文里没有本公司税号：不改。
        same, found = recognition._anchor(result, issues, "no tax id here 2,576.00", "0105567178203")
        self.assertIs(same, result)

    def test_vision_is_only_called_when_the_checks_ask_for_it(self):
        from pearnly_core import ocr
        from pearnly_erp_portal.purchasing import recognition

        clean = [({"lines": []}, [], "m")]
        with patch.object(ocr, "text_from_image") as vision:
            self.assertIs(recognition._anchor_with_ocr(clean, [(b"x", "image/jpeg")], "CO-1"), clean)
            flagged = [({"lines": []}, ["computed_total"], "m")]
            recognition._anchor_with_ocr(flagged, [(b"x", "image/jpeg")], "CO-1")
            pdf = [({"lines": []}, ["tax_id_checksum"], "m")]
            recognition._anchor_with_ocr(pdf, [(b"%PDF", "application/pdf")], "CO-1")
        vision.assert_not_called()

    def test_credit_notes_are_not_recorded_as_purchases(self):
        from pearnly_erp_portal.purchasing import recognition
        from pearnly_erp_portal.recognition import policy

        result = recognition._structured({"document_type": "credit_note", "document_title": "ใบลดหนี้",
                                          "printed": {"total": "(349.89)", "vat": "(22.89)"},
                                          "lines": [{"description": "น้ำดื่ม", "qty": 2, "unit_price": -48}]})
        with patch.object(recognition.guard, "require"), \
                patch("pearnly_core.ocr.is_configured", return_value=True), \
                patch.object(recognition.files, "upload", return_value=frappe._dict(file_name="a.jpg",
                             get_content=lambda: b"x")), \
                patch.object(recognition.billing, "pages", return_value=1), \
                patch.object(recognition.billing, "gate"), patch.object(recognition.billing, "charge") as charge, \
                patch.object(policy, "read_all", return_value=[(result, [], "m")]), \
                patch("pearnly_core.ocr.orient", side_effect=lambda content, mime: (content, mime)), \
                patch.object(recognition.frappe, "throw", side_effect=_raise), patch.object(recognition, "_", str):
            with self.assertRaises(policy.UnsupportedDocument):
                recognition.recognise([{"file": "F", "kind": "bill"}])
        charge.assert_not_called()

    def test_non_claimable_vat_added_on_top_is_part_of_the_cost(self):
        from pearnly_erp_portal.integrations import intake
        from pearnly_erp_portal.purchasing import entry, recognition

        # 语料 e402…：自助餐收据（不能抵进项），票面 2,544 + VAT 178.08 = 2,722。
        # 行名按站点语言翻译：离线容器没有翻译缓存，这里不测翻译本身。
        translate = patch.object(recognition, "_", lambda message, lang=None: message)
        translate.start()
        self.addCleanup(translate.stop)
        raw = {"document_type": "receipt", "document_title": "ใบเสร็จรับเงิน", "rounding": -0.08,
               "printed": {"subtotal": 2544, "vat": 178.08, "total": 2722}, "lines": [
                   {"description": "Buffet Premium 799", "qty": 3, "unit_price": 799},
                   {"description": "เครื่องดื่มรีฟิล", "qty": 3, "unit_price": 49}]}
        result = recognition._structured(raw)
        recognition._finish(result)  # 归组后会再跑一次：不重复加
        vat_rows = [row for row in result["lines"] if row.get(recognition.NON_CLAIMABLE_VAT)]
        self.assertEqual((result["has_vat"], len(vat_rows), vat_rows[0]["unit_price"], vat_rows[0]["item_type"]),
                         (False, 1, 178.08, "service"))
        totals = entry._calculate({"lines": result["lines"], "has_vat": False, "price_mode": result.get("price_mode"),
                                   "rounding": result.get("rounding")})
        self.assertEqual(str(totals["grand_total"]), "2722.00")
        self.assertEqual(recognition.review(result), [])
        # LINE 里选了「库存商品」：这一行仍是服务、不进仓库。
        doc = _doc(item_kind="goods", result=json.dumps(result))
        with patch.object(intake, "_fallback_name", return_value="x"):
            rows = intake.build_payload(doc)["lines"]
        self.assertEqual([(row["item_type"], "warehouse" in row) for row in rows],
                         [("goods", True), ("goods", True), ("service", False)])
        # 含税价的简式税票明细里已含 VAT：不加这一行。
        abb = recognition._structured({"document_type": "simplified_tax_invoice", "printed": {"vat": 8.57, "total": 131},
                                       "lines": [{"description": "x", "qty": 1, "unit_price": 131}]})
        self.assertFalse(any(row.get(recognition.NON_CLAIMABLE_VAT) for row in abb["lines"]))

    def test_printed_dates_are_converted_by_code(self):
        from datetime import date

        from pearnly_erp_portal.recognition import dates

        today = date(2026, 9, 26)
        self.assertEqual(dates.printed("10/05/2569", today), "2026-05-10")   # 佛历四位
        self.assertEqual(dates.printed("07/05/2026", today), "2026-05-07")   # 公历四位
        self.assertEqual(dates.printed("19/05/69", today), "2026-05-19")     # 两位：只有佛历说得通
        self.assertEqual(dates.printed("07.05.26", today), "2026-05-07")     # 两位：只有公历说得通
        self.assertEqual(dates.printed("2569-05-27", today), "2026-05-27")
        self.assertIsNone(dates.printed("10 พ.ค. 69", today))               # 泰文月份：交给模型
        self.assertIsNone(dates.printed("31/02/2026", today))
        # 到期日：模型照抄了票面「19/05/69」—— 按单据日期之后换算；已是 ISO 原样；换不出不填。
        self.assertEqual(dates.due("19/05/69", "2026-05-19"), "2026-05-19")
        self.assertEqual(dates.due("18/06/69", "2026-05-19"), "2026-06-18")
        self.assertEqual(dates.due("2026-06-18", "2026-05-19"), "2026-06-18")
        self.assertIsNone(dates.due("30 วัน", "2026-05-19"))

    def test_dates_are_soft(self):
        from datetime import date

        from pearnly_erp_portal.recognition import checks

        today = date(2026, 9, 26)
        self.assertEqual(checks.date_issues("2026-09-28", today), ["date_future"])
        self.assertEqual(checks.date_issues("2016-05-07", today), ["date_old"])  # 佛历年份读错十年
        self.assertEqual(checks.date_issues("2026-05-07", today), [])
        self.assertEqual(checks.hard(["date_old", "computed_total"]), ["computed_total"])


class Escalation(unittest.TestCase):
    """ADR-013：3.1 读 → 金额 / 结构对不上才 3.1 带提示复核 → 站点开关打开才升级（默认关）。"""

    def read(self, answers, review, upgrade=None):
        from pearnly_core import ocr

        from pearnly_erp_portal.recognition import policy

        calls = []

        self.last_prompts = []

        def fake(prompt, images, *, model, thinking=None, **kw):
            calls.append((model, thinking, "failed these checks" in prompt))
            self.last_prompts.append(prompt)
            self.assertEqual(kw.get("max_tokens"), policy.MAX_OUTPUT_TOKENS)
            return answers[len(calls) - 1]

        vision = patch.object(ocr, "text_from_image", return_value=None)
        if not isinstance(ocr.text_from_image, Mock):  # 调用方自己替换了原文时不覆盖
            vision.start()
            self.addCleanup(vision.stop)
        with patch.object(ocr, "json_from_images", side_effect=fake), \
                patch.object(policy, "escalation", return_value=upgrade), \
                patch.object(frappe, "logger", return_value=Mock(), create=True):
            result = policy.read("P", [(b"x", "image/jpeg")], structured=lambda raw: raw if "lines" in raw else None,
                                 review=review, label="t")
        return result, calls

    def test_a_clean_first_read_is_not_read_again(self):
        (result, issues, model), calls = self.read([{"lines": [], "n": 1}], lambda r: [])
        self.assertEqual(calls, [("gemini-3.1-flash-lite", {"thinkingLevel": "MINIMAL"}, False)])
        self.assertEqual((result["n"], issues, model), (1, [], "gemini-3.1-flash-lite"))

    def test_amounts_that_do_not_add_up_get_a_hinted_recheck_with_the_same_cheap_model(self):
        answers = [{"lines": [], "n": 1, "printed": {"total": 10}}, {"lines": [], "n": 2}]
        (result, issues, model), calls = self.read(answers, lambda r: ["computed_total"] if r["n"] == 1 else ["date_old"])
        self.assertEqual(calls, [("gemini-3.1-flash-lite", {"thinkingLevel": "MINIMAL"}, False),
                                 ("gemini-3.1-flash-lite", {"thinkingLevel": "LOW"}, True)])
        self.assertEqual((result["n"], issues, model), (2, ["date_old"], "gemini-3.1-flash-lite"))
        # 复核没有改善：保留第一次。
        (result, _issues, _model), _calls = self.read(answers, lambda r: ["computed_total"])
        self.assertEqual(result["n"], 1)

    def test_problems_a_reread_does_not_fix_are_only_flagged(self):
        # 语料实测：税号校验位不过，3.8 重读多半还是错 —— 只标红（采购识别另用 OCR 原文核对）；软标注同样不重读。
        for issues in (["tax_id_checksum"], ["date_old"], ["credit_note"], ["tax_invoice_without_buyer"]):
            with self.subTest(issues):
                (_result, got, _model), calls = self.read([{"lines": [], "n": 1}], lambda r, i=issues: i)
                self.assertEqual((len(calls), got), (1, issues))

    def test_an_unreadable_photo_goes_straight_to_the_ocr_text_read(self):
        from pearnly_core import ocr

        # 语料实测：严重退化的照片让模型重复输出、写满上限；看图复核会同样失控，直接「原图 + 原文」读。
        with patch.object(ocr, "text_from_image", return_value="TOTAL 98.00"):
            (result, issues, model), calls = self.read([None, {"lines": [], "n": 2}], lambda r: [])
        self.assertEqual((len(calls), calls[1][2], result["n"]), (2, False, 2))
        self.assertIn("OCR transcript", self.last_prompts[1])
        (result, _issues, _model), calls = self.read([None], lambda r: [])  # 没有原文：不再花钱
        self.assertEqual((len(calls), result), (1, None))

    def test_a_recheck_that_still_fails_reads_the_image_with_its_ocr_text(self):
        from pearnly_core import ocr

        from pearnly_erp_portal.recognition import policy

        # 语料 NBC 逐行折扣 / Giffarine 整单折扣：看图两次都对不上，「原图 + OCR 原文」读对。
        answers = [{"lines": [], "n": 1}, {"lines": [], "n": 2}, {"lines": [], "n": 3}]
        review = lambda r: [] if r["n"] == 3 else ["computed_total"]
        with patch.object(ocr, "text_from_image", return_value="TOTAL 67,852.80") as vision:
            (result, issues, model), calls = self.read(answers, review)
        vision.assert_called_once()
        self.assertEqual((result["n"], issues, len(calls)), (3, [], 3))
        with patch.object(ocr, "text_from_image", return_value=None):
            (result, _issues, _model), calls = self.read(answers[:2], review)
        self.assertEqual((result["n"], len(calls)), (1, 2))  # 没有原文：不再读

    def test_the_site_switch_upgrades_only_high_value_documents_that_still_fail(self):
        big = {"lines": [], "n": 1, "printed": {"total": 80000}}
        answers = [big, dict(big, n=2), {"lines": [], "n": 3}]
        review = lambda r: [] if r["n"] == 3 else ["computed_total"]
        (result, _issues, model), calls = self.read(answers, review, upgrade=("gemini-3.8-flash", 50000))
        self.assertEqual(([c[0] for c in calls], result["n"], model),
                         (["gemini-3.1-flash-lite", "gemini-3.1-flash-lite", "gemini-3.8-flash"], 3, "gemini-3.8-flash"))
        small = {"lines": [], "n": 1, "printed": {"total": 300}}
        (_result, _issues, _model), calls = self.read([small, dict(small, n=2)], review,
                                                      upgrade=("gemini-3.8-flash", 50000))
        self.assertEqual(len(calls), 2)  # 金额不到下限：不升级
        (_result, _issues, _model), calls = self.read([big, dict(big, n=2)], review)
        self.assertEqual(len(calls), 2)  # 默认没有开关：不升级

    def test_a_pdf_with_a_text_layer_is_read_as_text_first(self):
        from pearnly_core import ocr

        from pearnly_erp_portal.recognition import inputs, policy

        sent = []

        def fake(prompt, images, *, model, thinking=None, **kw):
            sent.append((model, "PAGE TEXT" in prompt, len(images)))
            return {"lines": [], "n": len(sent)}

        pdf = [(b"%PDF", "application/pdf")]
        with patch.object(ocr, "json_from_images", side_effect=fake), \
                patch.object(policy, "escalation", return_value=None), \
                patch.object(frappe, "logger", return_value=Mock(), create=True), \
                patch.object(inputs, "pdf_text", return_value=(1, "PAGE TEXT " * 40)):
            policy.read("P", pdf, structured=lambda raw: raw, review=lambda r: ["computed_total"] if r["n"] == 1 else [],
                        label="t")
        # 首读只给文字、不传原件；金额对不上，复核读 PDF 原件。
        self.assertEqual(sent, [("gemini-3.1-flash-lite", True, 0), ("gemini-3.1-flash-lite", False, 1)])
        with patch.object(inputs, "pdf_text", return_value=(3, "short")):
            self.assertIsNone(inputs.text_path(pdf))  # 文字太少（扫描件）：照常传原件

    def test_pdf_page_limit(self):
        from pearnly_erp_portal.recognition import inputs

        pages = SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: "x")] * 21)
        with patch("pypdf.PdfReader", return_value=pages, create=True), \
                patch.object(frappe, "throw", side_effect=frappe.ValidationError, create=True), \
                patch.object(frappe, "_", _same, create=True):
            with self.assertRaises(frappe.ValidationError):
                inputs.pdf_text(b"%PDF")

    def read_all(self, pages, answers, review=lambda result: []):
        from pearnly_core import ocr

        from pearnly_erp_portal.recognition import inputs, policy

        calls = []

        def fake(prompt, images, *, model, thinking=None, **kw):
            calls.append(model)
            return answers[len(calls) - 1]

        with patch.object(ocr, "json_from_images", side_effect=fake), \
                patch.object(frappe, "logger", return_value=Mock(), create=True), \
                patch.object(inputs, "pdf_pages", return_value=pages), \
                patch.object(inputs, "text_path", return_value=None):
            documents = policy.read_all("P", [(b"%PDF", "application/pdf")], structured=lambda raw: dict(raw),
                                        review=review, label="t", fields=("doc_no", "supplier_name"))
        return documents, calls

    def test_pages_are_read_one_by_one_and_grouped_by_document_number(self):
        # 旧线上 13 页撞限流：逐页读、按单号归组；续页没合计不触发升级。
        answers = [{"doc_no": "A1", "supplier_name": "S", "lines": [{"n": 1}], "printed": {}},
                   {"doc_no": "", "lines": [{"n": 2}], "printed": {"total": 30}},
                   {"doc_no": "B7", "lines": [{"n": 3}], "printed": {"total": 5}}]
        missing = lambda result: [] if (result.get("printed") or {}).get("total") else ["total_missing"]
        documents, calls = self.read_all([b"p1", b"p2", b"p3"], answers, review=missing)
        self.assertEqual(calls, ["gemini-3.1-flash-lite"] * 3)
        self.assertEqual([(d["doc_no"], [row["n"] for row in d["lines"]], d["printed"].get("total"), codes)
                          for d, codes, _model in documents],
                         [("A1", [1, 2], 30, []), ("B7", [3], 5, [])])

    def test_several_documents_on_one_image_become_separate_documents(self):
        answers = [{"doc_no": "R1", "lines": [], "additional_documents": [{"doc_no": "R2", "lines": []}]}]
        documents, calls = self.read_all([], answers)
        self.assertEqual([d["doc_no"] for d, _codes, _model in documents], ["R1", "R2"])
        self.assertTrue(all("additional_documents" not in d for d, _codes, _model in documents))

    def test_thinking_is_sent_only_when_asked(self):
        from pearnly_core import ocr

        sent = {}

        def post(url, headers, payload, timeout_s, model):
            sent.update(payload["generationConfig"])
            return Mock(status_code=200, json=Mock(return_value={"candidates": [
                {"content": {"parts": [{"text": "{}"}]}}]}))

        self.enterContext(patch.object(ocr, "backend", return_value="aistudio"))
        self.enterContext(patch.object(ocr, "api_key", return_value="k"))
        self.enterContext(patch.object(ocr, "endpoint", return_value="https://x/{model}"))
        self.enterContext(patch.object(ocr, "_post_with_backoff", side_effect=post))
        ocr.json_from_images("P", [(b"x", "image/jpeg")], model="gemini-3.8-flash", thinking=ocr.thinking_for("gemini-3.8-flash"))
        self.assertEqual(sent["thinkingConfig"], {"thinkingLevel": "LOW"})
        sent.clear()
        ocr.json_from_images("P", [(b"x", "image/jpeg")], model="gemini-3.8-flash")
        self.assertNotIn("thinkingConfig", sent)  # DMS 等不传的调用照旧


class WebRecognition(unittest.TestCase):
    def test_busy_provider_gives_a_plain_message_on_the_web_only(self):
        from pearnly_core.execution.errors import Deferred

        from pearnly_erp_portal.recognition import policy

        def busy():
            raise Deferred("provider_429", 10)

        with patch.object(frappe, "throw", side_effect=RuntimeError, create=True) as thrown, \
                patch.object(frappe, "_", _same, create=True):
            with self.assertRaises(RuntimeError):
                policy.for_web(busy)
        self.assertIn("busy", thrown.call_args.args[0])
        with self.assertRaises(Deferred):
            busy()  # LINE 队列那条路不包：限流要原样交给队列重试


class Billing(Base):
    """按套餐扣：开工前查余额，识别成功才报账，按物理页，B 档（用户 2026-09-26）。"""

    def test_units_are_physical_pages(self):
        from pearnly_erp_portal.recognition import billing, inputs

        with patch.object(inputs, "pdf_page_count", return_value=13):
            self.assertEqual(billing.pages([(b"%PDF", "application/pdf"), (b"img", "image/jpeg")]), 14)

    def test_gate_says_insufficient_balance_but_never_blames_the_user_for_our_outage(self):
        from pearnly_core import billing_client
        from pearnly_core.execution.errors import Deferred

        from pearnly_erp_portal.recognition import billing

        self.enterContext(patch.object(billing, "_", _same))
        self.enterContext(patch.object(frappe, "throw", side_effect=lambda msg, exc=None: (_ for _ in ()).throw(
            (exc or frappe.ValidationError)(msg)), create=True))
        with patch.object(billing_client, "status", return_value={"allowed": False, "error_code": "insufficient_balance"}):
            with self.assertRaises(billing.InsufficientBalance):
                billing.gate(1)
        with patch.object(billing_client, "status", return_value={"allowed": False, "error_code": "lookup_error"}):
            with self.assertRaises(Deferred):
                billing.gate(1)
        with patch.object(billing_client, "status", return_value={"allowed": True}) as status:
            billing.gate(3)
        status.assert_called_once_with("pdf", 3, tier="B")

    def test_line_charges_once_per_intake_and_tells_the_sender_to_top_up(self):
        from pearnly_erp_portal.recognition import billing

        doc = _doc(status=intake.QUEUED, language="th")
        doc.reload = lambda: None
        doc.save = lambda **kw: None
        self.enterContext(patch.object(frappe, "get_doc", return_value=doc, create=True))
        self.enterContext(patch("pearnly_core.line_binding.user_of", return_value="buyer"))
        seen = {}
        self.enterContext(patch.object(intake, "_recogniser", return_value=SimpleNamespace(
            recognise=lambda files, **kw: seen.update(kw) or {"lines": []})))
        self.enterContext(patch.object(intake, "totals", return_value={}))
        self.enterContext(patch("pearnly_erp_portal.integrations.line_cards.preview", return_value={}))
        intake.run("LI-1", "U1")
        self.assertEqual(seen["billing_reference"], "erp.line_intake:LI-1")

        def broke(files, **kw):
            raise billing.InsufficientBalance("Top up please")

        doc.status = intake.QUEUED
        self.enterContext(patch.object(intake, "_recogniser", return_value=SimpleNamespace(recognise=broke)))
        self.enterContext(patch.object(intake, "_drop"))
        intake.run("LI-1", "U1")
        self.assertEqual(self.last_text(), "Top up please")


class Products(Base):
    def test_purchase_created_items_take_our_running_code_not_the_printed_one(self):
        from pearnly_erp_portal.products import purchase, service

        created = []
        item = SimpleNamespace(name="PRD-9", code="P-000099", barcode=None, base_unit="pcs", unit="pcs", is_active=1)

        def get_doc(value, name=None):
            if isinstance(value, dict):
                created.append(value)
                return SimpleNamespace(insert=lambda **kw: None, name="PRD-9")
            return item

        self.enterContext(patch("pearnly_erp_portal.access.guard.require"))
        self.enterContext(patch.object(frappe, "get_all", return_value=[], create=True))
        self.enterContext(patch.object(frappe, "get_doc", side_effect=get_doc, create=True))
        self.enterContext(patch.object(service, "_allocate_code", return_value="P-000099"))
        self.enterContext(patch.object(service, "_assert_unique"))
        line = purchase.resolve({"description": "Cushion 01", "code": "110814SX001", "qty": 48,
                                 "unit": "pcs", "unit_price": 589, "line_total": 22617.6})
        self.assertEqual(created[0]["code"], "P-000099")
        self.assertEqual((line["product"], line["code"]), ("PRD-9", "P-000099"))


class SourceFile(Base):
    def test_the_card_uses_its_own_file_record_not_a_shared_url(self):
        # The same photo sent twice gets two File records sharing one file_url; looking the
        # file up by URL returned the earlier card's attached copy and recognition was refused.
        self.assertEqual(intake._file_name(_doc(source_file_name="FILE-2")), "FILE-2")

    def test_the_source_file_is_never_auto_attached_to_the_intake_record(self):
        # Frappe attaches Files referenced by Attach fields to the saved record; an attached
        # file is no longer the uploader's own unattached upload, so recognition refused it.
        from pathlib import Path

        meta = json.loads((Path(intake.__file__).parents[1] / "pearnly_erp_portal" / "doctype"
                           / "erp_line_intake" / "erp_line_intake.json").read_text())
        types = {f["fieldname"]: f["fieldtype"] for f in meta["fields"]}
        self.assertNotIn("Attach", {types["source_file"], types["source_file_name"]})
        self.assertFalse([name for name, kind in types.items() if kind in ("Attach", "Attach Image")])


class Web(Base):
    """卡片「编辑」打开网页端原生页 → 提交入账（用户 2026-09-25）。"""

    def setUp(self):
        super().setUp()
        self.doc = _doc()
        self.enterContext(patch.object(frappe, "db", Mock(), create=True))
        frappe.db.exists.return_value = True
        frappe.db.get_value.side_effect = lambda doctype, name, fields=None, **kw: (
            frappe._dict(name="FILE-1", file_name="a.jpg", file_url="/private/files/a.jpg") if doctype == "File"
            else "Acme Co" if doctype == "ERP Supplier" else None)
        self.enterContext(patch.object(frappe, "get_doc", side_effect=lambda doctype, name=None: self.doc))
        self.enterContext(patch.object(intake, "_file_name", return_value="FILE-1"))
        self.enterContext(patch.object(intake, "now", lambda: "2026-09-25 23:00:00"))
        self.active = self.enterContext(patch("pearnly_erp_portal.masters.company.active_company", return_value="CO-1"))
        self.switch = self.enterContext(patch("pearnly_erp_portal.masters.company.set_active_company"))
        frappe.session.user = "buyer"
        self.roles["buyer"] |= {"purchase.write"}

    def test_only_the_sender_can_open_or_submit(self):
        frappe.session.user = "someone-else"
        self.roles["someone-else"] = {"purchase.write"}
        with self.assertRaises(frappe.PermissionError):
            intake.open_for_edit("LI-1")
        with self.assertRaises(frappe.PermissionError):
            intake.submit("LI-1", {})

    def test_open_prefills_the_native_form_with_choices_and_source_file(self):
        opened = intake.open_for_edit("LI-1")
        form = opened["doc"]
        self.assertEqual((opened["intake"], opened["switched_company"]), ("LI-1", False))
        self.assertEqual((form["supplier"], form["supplier_name"], form["is_paid"], form["payment_method"]),
                         ("SUP-1", "Acme Co", 1, "transfer"))
        self.assertEqual((form["doc_no"], form["lines"][0]["item_type"], form["lines"][0]["warehouse"]),
                         ("INV-1", "goods", "WH-1"))
        self.assertEqual(form["attachments"], [{"file": "FILE-1", "file_name": "a.jpg",
                         "file_url": "/private/files/a.jpg", "kind": "bill", "intake": True}])
        self.assertNotIn("company", form)
        self.switch.assert_not_called()

    def test_open_switches_to_the_company_chosen_in_line(self):
        self.active.return_value = "CO-2"
        self.assertTrue(intake.open_for_edit("LI-1")["switched_company"])
        self.switch.assert_called_once_with("CO-1")

    def test_recorded_card_opens_the_record(self):
        self.doc.status, self.doc.purchase = "confirmed", "PD-1"
        self.assertEqual(intake.open_for_edit("LI-1"), {"direction": "purchase", "recorded": "PD-1"})

    def test_submit_posts_once_with_the_card_company_and_file_and_no_line_reply(self):
        seen = {}

        def post(payload, *, request_id, finalize, allow_duplicate):
            seen.update(payload=payload, request_id=request_id, allow_duplicate=allow_duplicate)
            finalize({"name": "PD-1"})
            return {"name": "PD-1"}

        self.enterContext(patch("pearnly_erp_portal.purchasing.entry.post", side_effect=post))
        result = intake.submit("LI-1", {"company": "CO-9", "doc_no": "INV-9", "lines": [],
                                        "attachments": [{"file": "EXTRA", "kind": "payment_proof"}]})
        self.assertEqual(result, {"name": "PD-1"})
        self.assertEqual((seen["request_id"], seen["allow_duplicate"]), ("line-intake:LI-1", True))
        self.assertEqual((seen["payload"]["company"], seen["payload"]["source"], seen["payload"]["doc_no"]),
                         ("CO-1", "line", "INV-9"))
        self.assertEqual([row["file"] for row in seen["payload"]["attachments"]], ["FILE-1", "EXTRA"])
        args = frappe.db.set_value.call_args.args
        self.assertEqual((args[1], args[2]["purchase"], args[2]["status"]), ("LI-1", "PD-1", "confirmed"))
        self.assertEqual(self.replies, [])

    def test_submitted_card_is_not_recorded_twice(self):
        self.doc.status, self.doc.purchase = "confirmed", "PD-1"
        post = self.enterContext(patch("pearnly_erp_portal.purchasing.entry.post"))
        self.assertEqual(intake.submit("LI-1", {}), {"name": "PD-1", "duplicate": True})
        post.assert_not_called()


class CardShape(Base):
    def card(self, doc, result, totals):
        from pearnly_erp_portal.integrations import line_cards

        masters = {"ERP Company": {"company_name": "Sister Makeup Co., Ltd.", "tax_id": "0105567178203"},
                   "ERP Supplier": {"supplier_name": "NBC", "tax_id": "0105555167627", "branch_type": "head"}}

        def get_value(doctype, name=None, fields=None, as_dict=False):
            row = masters.get(doctype, {})
            return row if as_dict else (row.get(fields) if isinstance(fields, str) else "Main")

        self.enterContext(patch.object(frappe, "db", Mock(**{"get_value.side_effect": get_value}), create=True))
        self.enterContext(patch.object(line_cards, "_", _same))
        self.enterContext(patch("frappe.utils.get_url", return_value="https://testerp.pearnly.com"))
        return line_cards.preview(doc, result, totals)

    @staticmethod
    def rows(card):
        # 顶层的横排行就是「左标签 · 右值」那些字段行；明细在底下那个框里。
        return {row["contents"][0]["text"]: row["contents"][1]["text"]
                for row in card["contents"]["body"]["contents"] if row.get("layout") == "horizontal"}

    def test_card_follows_the_old_layout_and_has_only_an_edit_link(self):
        doc = _doc()
        result = {**json.loads(doc.result), "doc_kind": "purchase_invoice",
                  "lines": [{"description": f"Item {i}", "qty": 1, "unit_price": 10} for i in range(12)]}
        card = self.card(doc, result, {"grand_total": "107.00", "vat_amount": "7.00", "discount_total": "0",
                                       "lines": [{"line_total": "10.00", "discount": "0"}] * 12})
        header = card["contents"]["header"]["contents"]
        self.assertEqual((header[0]["text"], card["contents"]["header"]["backgroundColor"]),
                         ("Review document · Buy", "#16873E"))
        rows = self.rows(card)
        labels = [label for label in rows]
        self.assertEqual(labels[:4], ["Company", "Transaction type", "Documents", "Item Type"])
        # 采购只列卖方；金额与付款在明细下面（用户 2026-09-26）。
        self.assertEqual(labels[4:], ["Document no.", "Date", "Document type", "Seller", "Seller tax ID",
                         "Seller branch", "Seller address", "Notes",
                         "Subtotal", "Discount", "VAT", "WHT amount", "Document total", "Currency", "Payment method"])
        body = card["contents"]["body"]["contents"]
        items_at = next(i for i, row in enumerate(body) if row.get("layout") == "vertical" and "contents" in row
                        and any("Items" in str(part.get("text", "")) for part in row["contents"]))
        subtotal_at = next(i for i, row in enumerate(body) if row.get("layout") == "horizontal"
                           and row["contents"][0]["text"] == "Subtotal")
        self.assertLess(items_at, subtotal_at)
        self.assertEqual((rows["Date"], rows["Seller"], rows["Subtotal"], rows["Document total"]),
                         ("25/09/2569", "NBC", "100.00", "107.00"))
        text = json.dumps(card, ensure_ascii=False)
        for i in range(12):  # 商品名称全部显示（用户 2026-09-26）
            self.assertIn(f"{i + 1}. Item {i}", text)
        footer = card["contents"]["footer"]["contents"]
        self.assertEqual(len(footer), 1)
        self.assertEqual(footer[0]["action"], {"type": "uri", "label": "Edit",
                         "uri": "https://testerp.pearnly.com/api/method/pearnly_core.account.line_login.enter?to=%2Fdesk%2Fpearnly-purchase%2Fline%2FLI-1"})

    def test_long_documents_stay_within_the_line_bubble_limit(self):
        doc = _doc()
        name = "เอสเอ็มเอ็กซ์อาร์ ดิวอี้สกินคุชชั่น 01พิงค์ไอวอรี่ 12 กรัม"
        lines = [{"description": name, "qty": 48, "unit": "ชิ้น", "unit_price": 589, "discount": 5654.4}] * 60
        card = self.card(doc, {"lines": lines}, {"grand_total": "1", "vat_amount": "0",
                                                  "lines": [{"line_total": "22617.60", "discount": "5654.40"}] * 60})
        text = json.dumps(card, ensure_ascii=False)
        self.assertLessEqual(len(text.encode()), 28 * 1024)
        self.assertIn("more items", text)

    def test_card_names_an_abbreviated_tax_invoice(self):
        card = self.card(_doc(), {"doc_kind": "purchase_invoice", "document_type": "simplified_tax_invoice",
                                  "lines": []}, {})
        self.assertEqual(self.rows(card)["Document type"], "Abbreviated Tax Invoice")

    def test_sales_card_shows_our_company_as_seller_and_the_fallback_buyer(self):
        doc = _doc(direction="sales", party="@any")
        card = self.card(doc, {"doc_date": "2026-05-27", "lines": []}, {})
        rows = self.rows(card)
        self.assertEqual((rows["Transaction type"], rows["Buyer"], rows["Payment method"]), ("Sell", "Walk-in customer", "Transfer"))
        self.assertNotIn("Seller", rows)  # 销售不列卖方（就是我方公司）
        self.assertEqual(card["contents"]["header"]["backgroundColor"], "#B11B50")
        self.assertIn("No items were read", json.dumps(card))

    def test_old_card_postbacks_are_not_handled(self):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.assertFalse(handlers.handle({"type": "postback", "source": {"userId": "U1"},
                                              "postback": {"data": "a=card&op=confirm&i=LI-1&n=n1"}}))



class ProfileBinding(Base):
    """录制问题 #8：租户没配 LINE 渠道时，个人资料页的 LINE 块只说一句，不去发码（发码会弹错误框）。"""

    def test_status_says_whether_the_channel_is_configured(self):
        from pearnly_erp_portal.integrations import binding

        self.roles["buyer"] = {"integration.read"}
        with patch.object(frappe, "session", SimpleNamespace(user="buyer")):
            for configured in (False, True):
                with patch("pearnly_core.line.is_configured", return_value=configured):
                    self.assertEqual(binding.status(), {"bound": False, "channel": configured,
                                                        "display_name": None, "bound_at": None})


if __name__ == "__main__":
    unittest.main(verbosity=2)
