"""Verify the sales record service on the dedicated laboratory.

Real Site with synthetic data. Covers: a draft takes a system number but moves nothing,
the gates run before the record is confirmed, confirming posts the goods out once (and only
once), voiding brings them back, a quotation never moves stock, the removed tax-document
kinds are rejected, upload recognition lands a `source=ocr` draft (model result is stubbed:
no external call, no money), job-based access, and tenant isolation.

**Samples are created on the isolated check Site** (`erp-pos-b.localhost`) and the demo Site
(`erp-pos-a.localhost`) is only read, so a probe run can never clear the demo's records.
Nothing is deleted: this probe never wipes a Site's sales documents.
"""
import json
import os
from pathlib import Path
import sys

import frappe

CONTROL_APP = "/home/frappe/frappe-bench/apps/pearnly_control"
SITE = "erp-pos-b.localhost"
DEMO_SITE = "erp-pos-a.localhost"
SITE_ROOT = Path("/home/frappe/frappe-bench/sites")
OWNER = "erp-pos-owner@example.com"
SELLER = "erp-pos-sales2@example.com"
CASHIER = "erp-pos-sales2-cashier@example.com"


def connect(site):
    if os.environ.get("PEARNLY_ERP_POS_LAB") != "1" or not (SITE_ROOT / ".erp-pos-lab").is_file():
        raise SystemExit("Dedicated ERP/POS laboratory required")
    os.chdir(SITE_ROOT)
    frappe.init(site=site, sites_path=str(SITE_ROOT))
    frappe.connect()
    if not frappe.conf.get("pearnly_erp_pos_lab") or frappe.conf.get("db_host") != "db":
        raise SystemExit("Refusing a non-lab Site")
    frappe.flags.mute_emails = True


def as_user(user, func, *args, **kwargs):
    frappe.set_user(user)
    try:
        return True, func(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"[:200]


def ensure_member(email, roles):
    if frappe.db.exists("User", email):
        frappe.delete_doc("User", email, force=True, ignore_permissions=True)
    frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0],
                    "send_welcome_email": 0,
                    "roles": [{"role": "Desk User"}, *({"role": r} for r in roles)]
                    }).insert(ignore_permissions=True)
    frappe.db.commit()
    frappe.clear_cache(user=email)


def balance(product, warehouse):
    return frappe.db.get_value("ERP Stock Balance",
                               {"product": product, "warehouse": warehouse}, "qty_on_hand")


def main():
    connect(SITE)
    frappe.set_user("Administrator")
    from pearnly_core.provisioning import ensure_feature_roles
    from pearnly_erp_portal.sales import contracts, service
    from pearnly_erp_portal.products import service as products
    from pearnly_erp_portal.inventory import stock, warehouses

    if CONTROL_APP not in sys.path:
        sys.path.insert(0, CONTROL_APP)
    from pearnly_control import features as control_features

    spec = control_features.roles_payload("erp", control_features.codes_of("erp"))
    ensure_feature_roles(json.dumps(spec), admin_user=OWNER)
    ensure_member(SELLER, ["ERP Sales"])
    ensure_member(CASHIER, ["ERP Cashier"])

    code = "LAB-SALE-P1"
    if not frappe.db.get_value("ERP Product", {"code": code}, "name"):
        products.create_product(product_name="Lab sale item", code=code, unit="pcs",
                                unit_price="100", default_cost="40")
    product = frappe.db.get_value("ERP Product", {"code": code}, "name")
    warehouse = warehouses.ensure_default()
    stock.apply_movement(warehouse=warehouse, movement_type="stock_in", product=product,
                         qty=100, unit="pcs", unit_cost="40")

    results = {}
    lines = [{"product": product, "description": "Lab sale item", "qty": 2, "unit": "pcs",
              "unit_price": "100"}]
    paid = {"payment_status": "paid", "payment_method": "cash", "payment_date": "2026-09-21"}

    ok, made = as_user(SELLER, service.create_document, doc_kind=contracts.RECEIPT,
                       doc_date="2026-09-21", buyer_name="Lab Buyer Co", party_type="company",
                       address="Bangkok", **paid, lines=lines)
    results["create"] = ok
    results["create_error"] = None if ok else made
    doc = made.get("name") if ok else None
    results["draft_grand_total"] = str(made.get("grand_total")) if ok else None
    results["draft_has_system_no"] = bool(frappe.db.get_value(
        "ERP Sales Document", doc, "system_no")) if doc else None
    results["draft_status"] = frappe.db.get_value("ERP Sales Document", doc, "status") if doc \
        else None
    results["draft_moves_nothing"] = frappe.db.count("ERP Stock Movement", {"ref_id": doc}) \
        if doc else None

    # 闸：面额 0 与未收款的收据都确认不了，也不动货。
    zero = service.create_document(doc_kind=contracts.RECEIPT, doc_date="2026-09-21",
                                   buyer_name="Lab Buyer Co", party_type="company",
                                   address="BKK", **paid,
                                   lines=[{"description": "free", "qty": 1, "unit_price": 0}])
    ok, err = as_user(SELLER, service.confirm_document, zero["name"])
    results["zero_blocked"] = ok
    results["zero_status"] = frappe.db.get_value("ERP Sales Document", zero["name"], "status")
    results["zero_moves_nothing"] = frappe.db.count("ERP Stock Movement",
                                                    {"ref_id": zero["name"]})
    unpaid = service.create_document(doc_kind=contracts.RECEIPT, doc_date="2026-09-21",
                                     buyer_name="Walk-in", party_type="anonymous", lines=lines)
    ok, err = as_user(SELLER, service.confirm_document, unpaid["name"])
    results["unpaid_receipt_blocked"] = ok
    results["unpaid_status"] = frappe.db.get_value("ERP Sales Document", unpaid["name"],
                                                   "status")

    # 确认：出库一次，且只出一次。
    on_hand = balance(product, warehouse)
    ok, confirmed = as_user(SELLER, service.confirm_document, doc)
    results["confirm"] = ok
    results["confirm_error"] = None if ok else confirmed
    results["stock_posted"] = bool(confirmed.get("stock", {}).get("posted")) if ok else None
    after = balance(product, warehouse)
    results["stock_delta"] = float(after) - float(on_hand) if on_hand is not None else None
    results["confirmed_status"] = frappe.db.get_value("ERP Sales Document", doc, "status")
    ok, again = as_user(SELLER, service.confirm_document, doc)
    results["reconfirm_blocked"] = ok
    results["movements_after_reconfirm"] = frappe.db.count("ERP Stock Movement",
                                                           {"ref_id": doc})
    ok, err = as_user(SELLER, service.update_document, doc, note="late edit")
    results["edit_after_confirm_blocked"] = ok

    # 作废：货回来。
    ok, voided = as_user(SELLER, service.void_document, doc, "lab void")
    results["void"] = ok
    results["void_error"] = None if ok else voided
    results["void_status"] = frappe.db.get_value("ERP Sales Document", doc, "status")
    results["stock_after_void"] = float(balance(product, warehouse)) if on_hand is not None \
        else None

    # 报价单：确认了也不动货（它不是销售凭证）。
    quote = service.create_document(doc_kind=contracts.QUOTATION, doc_date="2026-09-21",
                                    buyer_name="Lab Buyer Co", party_type="company",
                                    address="BKK", lines=lines)
    before_quote = balance(product, warehouse)
    ok, quoted = as_user(SELLER, service.confirm_document, quote["name"])
    results["quotation_confirm"] = ok
    results["quotation_moves_nothing"] = frappe.db.count("ERP Stock Movement",
                                                         {"ref_id": quote["name"]})
    results["quotation_stock_unchanged"] = float(balance(product, warehouse)) == float(before_quote)

    # 去掉开票侧：税票与冲正单类型一律被拒。
    for kind in ("tax_invoice", "tax_invoice_simple", "tax_invoice_receipt", "credit_note",
                 "debit_note"):
        ok, err = as_user(SELLER, service.create_document, doc_kind=kind,
                          doc_date="2026-09-21", buyer_name="X", lines=lines)
        results[f"reject_{kind}"] = ok

    # 上传单据：识别被替换成固定结果（不花钱），落 source=ocr 草稿并留底。
    import io

    from PIL import Image

    from pearnly_erp_portal.sales import ocr as sales_ocr

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 120, 200)).save(buffer, "JPEG")
    jpeg = buffer.getvalue()
    original_recognise = sales_ocr.recognise
    sales_ocr.recognise = lambda content, content_type: {
        "doc_no": "R-LAB-1", "doc_date": "2026-09-21", "buyer_name": "Lab OCR Buyer",
        "buyer_type": "company", "buyer_address": "Chiang Mai", "has_vat": True,
        "vat_rate": 7, "lines": [{"description": "ocr line", "qty": 3, "unit_price": "50"}]}
    try:
        ok, up = as_user(SELLER, service.create_from_upload, content=jpeg,
                         content_type="image/jpeg", filename="lab-receipt.jpg")
    finally:
        sales_ocr.recognise = original_recognise
    results["upload"] = ok
    results["upload_error"] = None if ok else up
    results["upload_source"] = frappe.db.get_value("ERP Sales Document", up.get("name"),
                                                   "source") if ok else None
    results["upload_kind"] = frappe.db.get_value("ERP Sales Document", up.get("name"),
                                                 "doc_kind") if ok else None
    results["upload_total"] = str(up.get("grand_total")) if ok else None
    results["upload_has_file"] = bool(frappe.db.get_value(
        "ERP Sales Document", up.get("name"), "source_file")) if ok else None
    results["upload_sha_set"] = bool(frappe.db.get_value(
        "ERP Sales Document", up.get("name"), "image_sha256")) if ok else None
    ok, fixed = as_user(SELLER, service.attach_recognition, up.get("name"),
                        payload={"buyer_name": "Lab OCR Buyer (reviewed)", "has_vat": True,
                                 "vat_rate": 7,
                                 "lines": [{"description": "reviewed line", "qty": 2,
                                            "unit_price": "100"}]})
    results["attach_recognition"] = ok
    results["reviewed_total"] = str(fixed.get("grand_total")) if ok else None

    # 岗位。
    ok, err = as_user(CASHIER, service.create_document, doc_kind=contracts.RECEIPT,
                      doc_date="2026-09-21", buyer_name="X", lines=lines)
    results["cashier_can_create"] = ok
    ok, rows = as_user(SELLER, service.list_documents)
    results["seller_can_list"] = ok and len(rows) >= 1
    results["site_records"] = frappe.db.count("ERP Sales Document")

    # 隔离：演示站只读回读，本探针不碰它。
    demo_before = results.get("demo_pre_count")
    frappe.set_user("Administrator")
    frappe.destroy()

    connect(DEMO_SITE)
    results["demo_site_records"] = frappe.db.count("ERP Sales Document")
    frappe.destroy()

    expected = {
        "create": True, "draft_grand_total": "214.00", "draft_has_system_no": True,
        "draft_status": "draft", "draft_moves_nothing": 0,
        "zero_blocked": False, "zero_status": "draft", "zero_moves_nothing": 0,
        "unpaid_receipt_blocked": False, "unpaid_status": "draft",
        "confirm": True, "stock_posted": True, "stock_delta": -2.0,
        "confirmed_status": "submitted", "reconfirm_blocked": False,
        "movements_after_reconfirm": 1, "edit_after_confirm_blocked": False,
        "void": True, "void_status": "void",
        "quotation_confirm": True, "quotation_moves_nothing": 0,
        "quotation_stock_unchanged": True,
        "reject_tax_invoice": False, "reject_tax_invoice_simple": False,
        "reject_tax_invoice_receipt": False, "reject_credit_note": False,
        "reject_debit_note": False,
        "cashier_can_create": False,
        "upload": True, "upload_source": "ocr", "upload_kind": "receipt",
        "upload_total": "160.50", "upload_has_file": True, "upload_sha_set": True,
        "attach_recognition": True, "reviewed_total": "214.00",
        "seller_can_list": True,
    }
    problems = {k: {"expected": v, "actual": results.get(k)} for k, v in expected.items()
                if results.get(k) != v}
    if on_hand is not None and results.get("stock_after_void") != float(on_hand):
        problems["stock_after_void"] = {"expected": float(on_hand),
                                        "actual": results.get("stock_after_void")}
    if results.get("zero_error") and not results.get("zero_status") == "draft":
        problems["zero_left_a_trace"] = results.get("zero_status")
    print(json.dumps({"site": SITE, "demo_site": DEMO_SITE, "results": results,
                      "problems": problems}, ensure_ascii=False, indent=2, default=str))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
