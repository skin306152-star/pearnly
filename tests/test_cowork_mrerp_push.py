"""MR.ERP 发票推送：分流 + 导入 5 步 + 逐行回执 + 结果落定。假 MR.ERP 站点，不连真实系统、不碰数据库。

对照旧线上 `ec2e5c957e6b`：`mrerp_http/routing.py`（方向 / 付款 / 货费分流、匹配闸）、`mrerp_http/adapter.py`
（formupload → uploadexcel → formrdpc → importpc → report；"1" 全成功、纯数字读 report 逐行判、
非数字体整批失败）、`mrerp_http/autocreate.py`（缺客户 / 供应商 / 商品先自建）、
`confirmed_push_reservation` 的结果判定（已存在 = 已推过；导入确认发出后异常 = 结果不明）。
"""
import contextlib
import io
import socket
import unittest
from datetime import date
from unittest.mock import patch

try:
    import requests
    from openpyxl import Workbook
    from pearnly_cowork.push.mrerp import routing, runner, transport
    from pearnly_cowork.push.mrerp.models import MRERPBusinessError
except ImportError as exc:  # 需要 Frappe 环境（bench 的 Python）
    raise unittest.SkipTest(f"needs the Frappe bench environment: {exc}")

BASE = "https://www.mrerp4sme.com"
PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
LOGIN_PAGE = '<form><input name="txtusers"><input name="txtpasswords"></form>'
TODAY = date(2026, 9, 26)
OWN = "0105559000001"
SUPPLIER = "0105561012345"
CUSTOMER = "0105562055501"
PREVIEW = ('<form id="frmimport1"><input type="hidden" name="idus1" value="15">'
           '<input type="checkbox" name="cbimport[1]" value="1" checked></form>')


def response(url, text="", status=200, content=None):
    r = requests.Response()
    r.url, r.status_code = url, status
    r._content = content if content is not None else text.encode()
    r.encoding = "utf-8"
    return r


def report(rows):
    book = Workbook()
    sheet = book.active
    sheet.title = "Worksheet"
    sheet.append(["เลขที่", "วันที่", "หมายเหตุ"])
    for invoice_no, note in rows:
        sheet.append([invoice_no, "2026-09-12", note])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


class FakeMrErp:
    """按路径回页面；记录每次导入落在哪个模块；发票导入的确认结果可配置。"""

    def __init__(self, password="test01", importpc="1", notes=None, upload_error="", importpc_raises=False):
        self.password, self.importpc, self.notes = password, importpc, notes
        self.upload_error, self.importpc_raises = upload_error, importpc_raises
        self.accepted, self.calls, self.uploads = False, [], []

    def __call__(self, session, method, url, **kwargs):
        path = url.replace(BASE, "").split("?")[0]
        self.calls.append((method, path))
        if path == "/login/checklogin.php":
            session.cookies.set("PHPSESSID", "abc")
            self.accepted = kwargs["data"]["txtpasswords"] == self.password
            return response(url)
        if path == "/login/mainmenu.php":
            return response(url, "<p>menu</p>") if self.accepted else response(BASE + "/login/login.php", LOGIN_PAGE)
        if path.endswith("/formupload.php"):
            return response(url, '<input type="hidden" name="idus" value="15">')
        module = path.strip("/").split("/")[0]
        if path.endswith("/component/uploadexcel.php"):
            self.uploads.append((module, kwargs["data"]["selmenu"]))
            return response(url, self.upload_error if module in ("impartran", "imparse", "impaptran") else "")
        if path.endswith("/formrdpc.php"):
            return response(url, PREVIEW)
        if path.endswith("/component/importpc.php"):
            if module in ("imparmas", "impapmas", "impstkmas"):
                return response(url, "1")  # 主档自建：全部受理
            if self.importpc_raises:
                raise requests.ConnectionError("connection reset after import confirmation")
            return response(url, self.importpc)
        if path.endswith("/component/report.php"):
            return response(url, content=report(self.notes or []))
        return response(url, "<html></html>")


def flat(hid, direction, **fields):
    base = {"invoice_number": f"INV-{hid}", "date": "2026-09-12", "subtotal": "1000.00", "vat": "70.00",
            "total_amount": "1070.00", "direction": direction,
            "items": [{"name": "กระดาษ A4", "qty": "2", "unit_price": "500.00", "amount": "1000.00"}]}
    base.update(fields)
    return {"id": f"CWH-{hid:0>12}", "invoice_no": base["invoice_number"], "invoice_date": base["date"],
            "total_amount": base["total_amount"], "direction": direction, "fields": base}


def sales(hid="a1", **fields):
    return flat(hid, "sales", seller_name="บริษัท เรา จำกัด", seller_tax=OWN, buyer_name="บริษัท ลูกค้า จำกัด",
                buyer_tax=CUSTOMER, **fields)


def purchase(hid="b1", **fields):
    return flat(hid, "purchase", seller_name="บริษัท ผู้ขาย จำกัด", seller_tax=SUPPLIER, buyer_name="บริษัท เรา จำกัด",
                buyer_tax=OWN, **fields)


def push(site, histories, mappings=None):
    def send(session, method, url, **kwargs):
        r = site(session, method, url, **kwargs)
        for hook in session.hooks["response"]:
            hook(r)
        return r

    with patch.object(requests.Session, "request", autospec=True, side_effect=send), \
            patch.object(socket, "getaddrinfo", return_value=PUBLIC), \
            patch.object(transport, "account_lock", lambda *a, **k: contextlib.nullcontext(True)):
        importer = transport.MrErpImport(system_url=BASE, username="test01", password="test01",
                                         comidyear="15", seldb="1")
        with importer:
            return importer, importer.upload_routed_batch(histories, {"_own_tax_id": OWN, **(mappings or {})},
                                                          today=TODAY)


def execute(site, histories):
    def send(session, method, url, **kwargs):
        r = site(session, method, url, **kwargs)
        for hook in session.hooks["response"]:
            hook(r)
        return r

    with patch.object(requests.Session, "request", autospec=True, side_effect=send), \
            patch.object(socket, "getaddrinfo", return_value=PUBLIC), \
            patch.object(transport, "account_lock", lambda *a, **k: contextlib.nullcontext(True)):
        return runner.execute({"histories": histories, "mappings": {"_own_tax_id": OWN}},
                              {"account_set": "15:1"}, {"username": "test01", "password": site.password_used},
                              BASE, TODAY)


class Routing(unittest.TestCase):
    """推什么按票面：用户选的方向与付款、复核改过的货 / 费。"""

    def test_sales_payment_follows_the_user_choice_then_the_invoice(self):
        self.assertEqual(routing.choose_doc_type(sales(posting_payment_manual="cash"), {},
                                                 own_tax_id=OWN, today=TODAY), "sales_cash")
        self.assertEqual(routing.choose_doc_type(sales(posting_payment_manual="credit", payment_method="cash"), {},
                                                 own_tax_id=OWN, today=TODAY), "sales_credit")

    def test_purchase_goods_or_expense_follows_the_review_choice(self):
        self.assertEqual(routing.choose_doc_type(purchase(posting_item_type_manual="goods"), {},
                                                 own_tax_id=OWN, today=TODAY), "purchase")
        self.assertEqual(routing.choose_doc_type(purchase(posting_item_type_manual="expense"), {},
                                                 own_tax_id=OWN, today=TODAY), "purchase_expense")

    def test_another_companys_invoice_is_confirmed_mismatch(self):
        other = flat("c1", None, seller_tax=SUPPLIER, buyer_tax=CUSTOMER)
        self.assertTrue(routing.confirmed_account_set_mismatch(other, other, own_tax_id=OWN,
                                                               expected_direction="sales"))
        self.assertFalse(routing.confirmed_account_set_mismatch(sales(), sales(), own_tax_id=OWN,
                                                                expected_direction="sales"))


class Customer(unittest.TestCase):
    """销项客户（用户 2026-09-26「按票面买方」）：关联客户映射 > 票面买方税号 > 现金客户。"""

    def test_customer_comes_from_the_invoice_buyer_without_a_linked_client(self):
        from pearnly_cowork.push.mrerp.xlsx.lookups import MRERP_CASH_CUSTOMER, resolve_customer_code

        self.assertEqual(resolve_customer_code(sales(), {}), CUSTOMER)
        walk_in = flat("w1", "sales", seller_tax=OWN)
        self.assertEqual(resolve_customer_code(walk_in, {}), MRERP_CASH_CUSTOMER)
        linked = dict(sales(), client_id="CWC-9")
        mapping = {"clients": [{"erp_type": "mrerp", "client_id": "CWC-9", "erp_code": "C0009"}]}
        self.assertEqual(resolve_customer_code(linked, mapping), "C0009")

    def test_invoice_buyer_is_created_in_mrerp_with_its_tax_id_as_code(self):
        from pearnly_cowork.push.mrerp import autocreate

        created = []

        class Adapter:
            def create_customers(self, records, mappings):
                created.extend(records)
                return {r["code"]: True for r in records}

        autocreate.provision_customers(Adapter(), [sales(buyer_addr="99 ถนนสุขุมวิท")], {})
        self.assertEqual([(r["code"], r["name"], r["tax_id"], r["address"]) for r in created],
                         [(CUSTOMER, "บริษัท ลูกค้า จำกัด", CUSTOMER, "99 ถนนสุขุมวิท")])


class Import(unittest.TestCase):
    def test_sales_credit_creates_missing_customer_and_product_then_imports(self):
        site = FakeMrErp()
        importer, result = push(site, [sales(posting_payment_manual="credit", client_id="7")])
        self.assertEqual([s.mrerp_bill_no[:2] for s in result.success], ["SI"])
        self.assertFalse(result.failed)
        self.assertTrue(importer.import_sent)
        # 客户（imparmas）、商品（impstkmas）先自建，再进销项赊账（impartran / selmenu 118）
        self.assertEqual(site.uploads, [("imparmas", ""), ("impstkmas", ""), ("impartran", "118")])

    def test_sales_cash_goes_to_the_cash_module(self):
        site = FakeMrErp()
        _, result = push(site, [sales(posting_payment_manual="cash")],
                         {"products": [{"item_name": "กระดาษ A4", "erp_code": "P-A4"}]})
        self.assertEqual(len(result.success), 1)
        self.assertEqual(site.uploads[-1], ("imparse", "125"))

    def test_purchase_goods_and_expense_use_their_own_menus(self):
        for kind, menu in (("goods", "67"), ("expense", "453")):
            with self.subTest(kind=kind):
                site = FakeMrErp()
                _, result = push(site, [purchase(posting_item_type_manual=kind)])
                self.assertEqual(len(result.success), 1)
                self.assertEqual(site.uploads[0], ("impapmas", ""))  # 缺供应商先建
                self.assertEqual(site.uploads[-1], ("impaptran", menu))

    def test_numeric_reply_reads_the_report_row_by_row(self):
        invoice_no = routing._gen.derive_mrerp_invoice_no(purchase())
        site = FakeMrErp(importpc="2", notes=[(invoice_no, "ไม่พบรหัสสินค้า")])
        _, result = push(site, [purchase()])
        self.assertFalse(result.success)
        self.assertEqual(result.failed[0].reasons, ["ไม่พบรหัสสินค้า"])

    def test_non_numeric_reply_fails_the_whole_batch_honestly(self):
        _, result = push(FakeMrErp(importpc="<script>alert('Error : ')</script>"), [purchase()])
        self.assertFalse(result.success)
        self.assertEqual(result.failed[0].reasons[0], "ERR_MRERP_IMPORT_ERROR")

    def test_upload_rejection_happens_before_the_import_is_sent(self):
        with self.assertRaises(MRERPBusinessError):
            push(FakeMrErp(upload_error="รูปแบบไฟล์ไม่ถูกต้อง"), [purchase()])

    def test_another_companys_invoice_never_reaches_the_import(self):
        # 票面声明的方向优先于税号锚点（旧版同）：这道闸只在方向没声明时起作用。
        site = FakeMrErp()
        other = flat("c1", None, seller_tax=SUPPLIER, buyer_tax=CUSTOMER)
        _, result = push(site, [other])
        self.assertEqual(result.failed[0].reasons, [routing.ACCOUNT_SET_MISMATCH_CODE])
        self.assertFalse([u for u in site.uploads if u[0] in ("impartran", "imparse", "impaptran")])


class Outcome(unittest.TestCase):
    """落到账本的结果：成功 / 已推过 / 转人工 / 结果不明。"""

    def run_case(self, site, histories, password="test01"):
        site.password_used = password
        return execute(site, histories)

    def test_success_carries_the_mrerp_document_number(self):
        out = self.run_case(FakeMrErp(), [purchase()])
        self.assertEqual(out["status"], "success")
        self.assertTrue(out["docnum"])

    def test_already_existing_number_is_already_pushed(self):
        invoice_no = routing._gen.derive_mrerp_invoice_no(purchase())
        out = self.run_case(FakeMrErp(importpc="2", notes=[(invoice_no, "เลขที่ดังกล่าวมีอยู่ในระบบแล้ว")]), [purchase()])
        self.assertEqual((out["status"], out["docnum"]), ("success", invoice_no))

    def test_definite_failures_before_sending_are_not_unknown(self):
        for site, password, code in ((FakeMrErp(), "wrong", "ERR_AUTH"),
                                     (FakeMrErp(upload_error="ผิดพลาด"), "test01", "ERR_BUSINESS")):
            with self.subTest(code=code):
                out = self.run_case(site, [purchase()], password)
                self.assertEqual(out["status"], "failed")
                self.assertTrue(out["reason"].startswith(code))

    def test_an_error_after_the_import_was_sent_is_unknown(self):
        out = self.run_case(FakeMrErp(importpc_raises=True), [purchase()])
        self.assertEqual(out["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
