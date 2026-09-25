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
        self.enterContext(patch("pearnly_core.line.download_message_content", return_value=b"file"))
        seen = {}
        receive = self.enterContext(patch.object(intake, "receive",
                                    side_effect=lambda **kw: seen.update(user=frappe.session.user, **kw)))
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            handlers.handle(self.event())
        receive.assert_called_once()
        self.assertEqual((seen["user"], seen["sent_by"], seen["choices"]["company"]), ("buyer", "buyer", "CO-1"))
        self.assertEqual(frappe.session.user, "Guest")


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
            recognise=lambda files: seen.setdefault("lang", frappe.local.lang) and {"lines": []})))
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
                         "uri": "https://testerp.pearnly.com/desk/pearnly-stock-count"})
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
        self.assertEqual(labels[:4], ["Company", "Direction", "Documents", "Item Type"])
        self.assertEqual(labels[4:], ["Document no.", "Date (BE)", "Document type", "Seller", "Seller tax ID",
                         "Seller branch", "Seller address", "Buyer", "Buyer tax ID", "Buyer branch", "Buyer address",
                         "Subtotal", "Discount", "VAT", "WHT amount", "Document total", "Currency",
                         "Payment method", "Notes"])
        self.assertEqual((rows["Date (BE)"], rows["Seller"], rows["Buyer"], rows["Subtotal"], rows["Document total"]),
                         ("25/09/2569", "NBC", "Sister Makeup Co., Ltd.", "100.00", "107.00"))
        text = json.dumps(card, ensure_ascii=False)
        for i in range(12):  # 商品名称全部显示（用户 2026-09-26）
            self.assertIn(f"{i + 1}. Item {i}", text)
        footer = card["contents"]["footer"]["contents"]
        self.assertEqual(len(footer), 1)
        self.assertEqual(footer[0]["action"], {"type": "uri", "label": "Edit",
                         "uri": "https://testerp.pearnly.com/desk/pearnly-purchase/line/LI-1"})

    def test_long_documents_stay_within_the_line_bubble_limit(self):
        doc = _doc()
        name = "เอสเอ็มเอ็กซ์อาร์ ดิวอี้สกินคุชชั่น 01พิงค์ไอวอรี่ 12 กรัม"
        lines = [{"description": name, "qty": 48, "unit": "ชิ้น", "unit_price": 589, "discount": 5654.4}] * 60
        card = self.card(doc, {"lines": lines}, {"grand_total": "1", "vat_amount": "0",
                                                  "lines": [{"line_total": "22617.60", "discount": "5654.40"}] * 60})
        text = json.dumps(card, ensure_ascii=False)
        self.assertLessEqual(len(text.encode()), 28 * 1024)
        self.assertIn("more items", text)

    def test_sales_card_shows_our_company_as_seller_and_the_fallback_buyer(self):
        doc = _doc(direction="sales", party="@any")
        card = self.card(doc, {"doc_date": "2026-05-27", "lines": []}, {})
        rows = self.rows(card)
        self.assertEqual((rows["Direction"], rows["Seller"], rows["Buyer"], rows["Payment method"]),
                         ("Sell", "Sister Makeup Co., Ltd.", "Walk-in customer", "Transfer"))
        self.assertEqual(card["contents"]["header"]["backgroundColor"], "#B11B50")
        self.assertIn("No items were read", json.dumps(card))

    def test_old_card_postbacks_are_not_handled(self):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.assertFalse(handlers.handle({"type": "postback", "source": {"userId": "U1"},
                                              "postback": {"data": "a=card&op=confirm&i=LI-1&n=n1"}}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
