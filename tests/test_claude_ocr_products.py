#!/usr/bin/env python3
"""Claude 识别路线（ADR-022）接入三个产品：站点侧客户端、ERP 转换、Cowork 结果结构、DMS 身份证分支。
不连 Frappe 站点、不联网（假 frappe / 假 Anthropic）；在生产镜像里跑（要 pydantic、Pillow、pymupdf）。"""
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for app in ("pearnly_core", "pearnly_cowork", "pearnly_erp_portal", "pearnly_dms"):
    sys.path.insert(0, str(ROOT / "apps" / app))


class Deferred(Exception):
    def __init__(self, reason="capacity", delay=5):
        super().__init__(reason)
        self.reason, self.delay = reason, delay


CHECKPOINTS = {}


def stub_frappe():
    frappe = types.ModuleType("frappe")
    frappe.conf = {}
    frappe.flags = types.SimpleNamespace()
    frappe._ = lambda text, *a: text
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.logger = lambda *a, **k: types.SimpleNamespace(warning=lambda *a, **k: None, info=lambda *a, **k: None)
    sys.modules["frappe"] = frappe
    errors = types.ModuleType("pearnly_core.execution.errors")
    errors.Deferred = Deferred
    sys.modules["pearnly_core.execution.errors"] = errors
    work = types.ModuleType("pearnly_core.execution.work")

    def checkpoint(key, state=None, body=None):
        if state is None:
            return CHECKPOINTS.get(key)
        CHECKPOINTS[key] = {"state": state, "body": body}
        return CHECKPOINTS[key]
    work.checkpoint = checkpoint
    sys.modules["pearnly_core.execution.work"] = work
    import pearnly_core.execution as execution
    execution.errors, execution.work = errors, work
    return frappe


frappe = stub_frappe()
from pearnly_core.claude_ocr import client  # noqa: E402

GOOD_TAX = "0105546015062"


def jpeg():
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (3000, 1000), "white").save(buffer, "JPEG")
    return buffer.getvalue()


def voucher(*docs, kind="tax_invoice", entry="enter"):
    return {"doc_kind": kind, "entry": entry, "copy_marking": "none", "documents": list(docs), "uncertain": []}


def doc(page=1, **kw):
    base = {"page": page, "invoice_number": "IV001", "date": "2026-05-01", "date_raw": "01/05/69", "due_date": None,
            "seller_name": "ผู้ขาย", "seller_tax": GOOD_TAX, "seller_branch": "00000", "seller_address": None,
            "buyer_name": "ผู้ซื้อ", "buyer_tax": "0105548082417", "buyer_branch": "00003", "buyer_address": None,
            "currency": "THB", "price_mode": "exclusive", "subtotal": "100.00", "discount": None, "vat": "7.00",
            "total": "107.00", "wht_rate": None, "wht_amount": None, "payment_method": None, "payment_received": None,
            "cash_amount": None, "change_amount": None, "notes": None,
            "lines": [{"description": "น้ำแข็ง", "code": "A1", "barcode": None, "item_type": "goods", "qty": "2",
                       "unit": "ถุง", "unit_price": "50.00", "discount": None, "discount_percent": None, "amount": "100.00",
                       "vat_rate": "7"}]}
    base.update(kw)
    return base


def outcome(read, stages=("claude-haiku-5-5",)):
    return {"read": read, "stages": [{"model": m, "seconds": 2.0, "usd": 0.001, "stop_reason": "end_turn",
                                      "usage": {"input_tokens": 10, "output_tokens": 5}} for m in stages],
            "escalated": len(stages) > 1, "why": [], "usd": 0.001 * len(stages), "seconds": 2.0 * len(stages), "where": "oracle"}


class Client(unittest.TestCase):
    def setUp(self):
        frappe.conf.clear()
        CHECKPOINTS.clear()

    def test_engine_choice(self):
        self.assertEqual(client.engine(), "google")
        frappe.conf["pearnly_ocr_engine"] = "Claude"
        self.assertTrue(client.uses_claude())
        frappe.conf["pearnly_ocr_engine"] = "gemini"
        self.assertEqual(client.engine(), "google")

    def test_prepare_shrinks_to_2000(self):
        from PIL import Image
        [(media, data)] = client.prepare([(jpeg(), "image/jpeg")])
        self.assertEqual(media, "image/jpeg")
        self.assertEqual(max(Image.open(io.BytesIO(data)).size), 2000)

    def test_remote_then_checkpoint(self):
        env = {"PEARNLY_CLAUDE_OCR_URL": "https://203.0.113.1:8443", "PEARNLY_CLAUDE_OCR_TOKEN": "t",
               "PEARNLY_CLAUDE_OCR_CERT_SHA256": "ab" * 32}
        answer = types.SimpleNamespace(status_code=200, json=lambda: outcome(voucher(doc())))
        with patch.dict("os.environ", env), patch("requests.Session.post", return_value=answer) as post:
            first = client.read("voucher", [(jpeg(), "image/jpeg")])
            second = client.read("voucher", [(jpeg(), "image/jpeg")])
        self.assertEqual(post.call_count, 1)  # 第二次从断点取，不重复付费
        self.assertEqual((first["where"], second["where"]), ("oracle", "checkpoint"))
        self.assertTrue(post.call_args.kwargs["headers"]["Authorization"].startswith("Bearer "))

    def test_unreachable_oracle_reads_locally(self):
        import requests
        env = {"PEARNLY_CLAUDE_OCR_URL": "https://203.0.113.1:8443", "PEARNLY_CLAUDE_OCR_TOKEN": "t",
               "PEARNLY_CLAUDE_OCR_CERT_SHA256": "ab" * 32, "PEARNLY_ANTHROPIC_API_KEY": "k"}
        with patch.dict("os.environ", env), patch("requests.Session.post", side_effect=requests.ConnectionError("down")), \
                patch.object(client.claude_engine, "run", return_value=outcome(voucher(doc()))) as run:
            got = client.read("voucher", [(jpeg(), "image/jpeg")])
        self.assertEqual(got["where"], "local")
        run.assert_called_once()

    def test_anthropic_down_defers(self):
        env = {"PEARNLY_CLAUDE_OCR_URL": "https://203.0.113.1:8443", "PEARNLY_CLAUDE_OCR_TOKEN": "t",
               "PEARNLY_CLAUDE_OCR_CERT_SHA256": "ab" * 32}
        with patch.dict("os.environ", env), patch("requests.Session.post", return_value=types.SimpleNamespace(status_code=502)):
            with self.assertRaises(Deferred):
                client.read("voucher", [(jpeg(), "image/jpeg")])

    def test_not_configured_defers_never_google(self):
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(Deferred):
            client.read("voucher", [(jpeg(), "image/jpeg")])


class Erp(unittest.TestCase):
    def test_purchase_and_sales_fields(self):
        from pearnly_erp_portal.recognition import claude
        read = voucher(doc())
        purchase = claude.to_raw(read, read["documents"][0], "purchase")
        self.assertEqual((purchase["doc_no"], purchase["supplier_tax_id"], purchase["supplier_branch_type"]),
                         ("IV001", GOOD_TAX, "head_office"))
        self.assertEqual(purchase["printed"]["total"], "107.00")
        self.assertEqual(purchase["lines"][0]["unit_price"], "50.00")
        self.assertTrue(purchase["has_vat"])
        sales = claude.to_raw(read, read["documents"][0], "sales")
        self.assertEqual((sales["seller_tax_id"], sales["buyer_type"]), (GOOD_TAX, "company"))
        self.assertNotIn("supplier_name", sales)

    def test_read_all_multiple_documents(self):
        from pearnly_erp_portal.recognition import claude
        read = voucher(doc(), doc(invoice_number="IV002"))
        with patch.object(claude.client, "read", return_value=outcome(read)):
            found = claude.read_all([(b"x", "image/jpeg")], structured=lambda raw: dict(raw), review=lambda r: [],
                                    label="purchase")
        self.assertEqual([r["doc_no"] for r, _codes, _model in found], ["IV001", "IV002"])
        with patch.object(claude.client, "read", return_value=outcome(voucher(entry="not_invoice", kind="statement"))):
            [(result, _c, _m)] = claude.read_all([(b"x", "image/jpeg")], structured=dict, review=lambda r: [], label="sales")
        self.assertTrue(result["not_document"])


class Cowork(unittest.TestCase):
    def pipeline(self, read, pages):
        from pearnly_cowork.ocr.execution import claude
        return claude.to_pipeline(outcome(read, ("claude-haiku-5-5", "claude-opus-5-5")), pages, 2026)

    def test_one_result_per_physical_page_and_saved_plan(self):
        from pearnly_cowork.ocr.results import prepare_history_batch
        read = voucher(doc(page=1), doc(page=1, invoice_number="IV002"), doc(page=3, invoice_number="IV003"))
        result = self.pipeline(read, 3)
        self.assertEqual([p.page_number for p in result.pages], [1, 2, 3])
        self.assertEqual(result.pages[0].invoice.additional_invoices[0].invoice_number, "IV002")
        self.assertTrue(result.pages[1].invoice.is_not_invoice)  # 续页 / 空页：留作证据
        self.assertEqual(result.engine, "claude_v1")
        plan = prepare_history_batch(result, expected_page_count=3, workspace="W", source="manual", client=None,
                                     direction=None, posting_kind=None, payment=None, reference_kind="physical_page",
                                     document_policy={})
        self.assertEqual(plan["invoice_count"], 3)

    def test_not_invoice_and_vat_included(self):
        result = self.pipeline(voucher(kind="vat_report_or_list", entry="not_invoice"), 1)
        self.assertTrue(result.pages[0].invoice.is_not_invoice)
        receipt = self.pipeline(voucher(doc(subtotal="107.00", vat="7.00", price_mode="inclusive", buyer_tax=None),
                                        kind="receipt_tax_invoice"), 1)
        self.assertEqual(receipt.pages[0].invoice.subtotal, "100.00")  # Cowork 的 subtotal 是税前
        self.assertEqual(receipt.pages[0].invoice.currency, "")  # 泰铢不填

    def test_pages_beyond_the_cap_are_flagged_unread(self):
        from pearnly_cowork.ocr.execution import claude
        result = self.pipeline(voucher(doc()), 10)
        self.assertIn("PAGE_UNREAD", result.pages[9].layer_chain)
        self.assertEqual(len(result.pages), 10)
        self.assertEqual(claude.evidence(outcome(voucher(doc()), ("claude-haiku-5-5", "claude-opus-5-5")))[1]["stage"],
                         "claude_review")

    def test_policy_kinds_reach_the_document_policy(self):
        slip = self.pipeline(voucher(doc(invoice_number="REF1", seller_tax=None), kind="transfer_slip", entry="not_invoice"), 1)
        self.assertEqual(slip.pages[0].invoice.document_type, "payment_evidence")
        self.assertFalse(slip.pages[0].invoice.is_not_invoice)
        wht = self.pipeline(voucher(doc(wht_amount="30.00"), kind="withholding_certificate", entry="not_invoice"), 1)
        self.assertEqual(wht.pages[0].invoice.document_flag, "withholding_certificate")


class Dms(unittest.TestCase):
    def test_claude_id_card_feeds_the_same_normalizer(self):
        ocr = types.ModuleType("pearnly_core.ocr")
        ocr.MODE_MODEL_MAPS = {"economy": {"flash_lite": "gemini"}}
        ocr.detect_image_mime = lambda data: "image/jpeg"
        ocr.json_from_images = lambda *a, **k: (_ for _ in ()).throw(AssertionError("Claude 路线不许调谷歌"))
        sys.modules["pearnly_core.ocr"] = ocr
        frappe.conf["pearnly_ocr_engine"] = "claude"
        from pearnly_dms.ocr import ocr_id_card
        card = {"is_id_card": True, "people_id": "1100400123452", "prefix": "น.ส.", "first_name_th": "สมหญิง",
                "last_name_th": "ใจดี", "date_of_birth_be": "01/02/2530", "issue_date_be": "01/01/2560",
                "expiry_date_be": "LIFETIME", "address_raw": "1 ม.2", "house_no": "1", "moo": "2", "soi": None, "road": None,
                "subdistrict": "บางนา", "district": "บางนา", "province": "กรุงเทพมหานคร", "zipcode": "10260", "uncertain": []}
        with patch.object(client, "read", return_value={"read": card}):
            result = ocr_id_card.extract(b"jpeg")
        self.assertEqual(result["id_card"]["prefix_name"], "นางสาว")
        self.assertEqual(result["id_card"]["expiry_date_be"], "LIFETIME")
        self.assertEqual(result["id_card"]["birthday_be"], "01/02/2530")
        frappe.conf.clear()


if __name__ == "__main__":
    unittest.main()
