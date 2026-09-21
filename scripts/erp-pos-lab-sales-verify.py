"""Verify the sales document service on the dedicated laboratory.

Real Site with synthetic data. Covers: draft does not take a number, the gates run before
the number is taken, issuing posts the goods out once (and only once), voiding brings them
back and keeps the number, notes reference an issued original, job-based access and tenant
isolation. No external call, no OCR, no money.
"""
import json
import os
from pathlib import Path
import sys

import frappe

CONTROL_APP = "/home/frappe/frappe-bench/apps/pearnly_control"
SITE = "erp-pos-a.localhost"
OTHER_SITE = "erp-pos-b.localhost"
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

    for name in frappe.get_all("ERP Sales Document", pluck="name"):
        frappe.delete_doc("ERP Sales Document", name, force=True, ignore_permissions=True)
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
    ok, made = as_user(SELLER, service.create_document, doc_kind=contracts.TAX_INVOICE,
                       doc_date="2026-09-21", buyer_name="Lab Buyer Co", party_type="company",
                       tax_id="0105550000001", branch_type="hq", address="Bangkok",
                       lines=lines)
    results["create"] = ok
    results["create_error"] = None if ok else made
    doc = made.get("name") if ok else None
    results["draft_grand_total"] = str(made.get("grand_total")) if ok else None
    results["draft_has_no_number"] = frappe.db.get_value("ERP Sales Document", doc, "doc_no") \
        if doc else None

    # 闸：面额 0 与未收款收据都开不出去，且不占号。
    zero = service.create_document(doc_kind=contracts.TAX_INVOICE, doc_date="2026-09-21",
                                   buyer_name="Lab Buyer Co", party_type="company",
                                   tax_id="0105550000001", branch_type="hq", address="BKK",
                                   lines=[{"description": "free", "qty": 1, "unit_price": 0}])
    ok, err = as_user(SELLER, service.issue_document, zero["name"])
    results["zero_blocked"] = ok
    results["zero_error"] = None if ok else err
    results["zero_has_no_number"] = frappe.db.get_value("ERP Sales Document", zero["name"],
                                                        "doc_no")
    receipt = service.create_document(doc_kind=contracts.RECEIPT, doc_date="2026-09-21",
                                      buyer_name="Walk-in", party_type="anonymous",
                                      lines=lines)
    ok, err = as_user(SELLER, service.issue_document, receipt["name"])
    results["unpaid_receipt_blocked"] = ok

    # 开出：取号 + 出库一次。
    on_hand = frappe.db.get_value("ERP Stock Balance", {"product": product, "warehouse": warehouse},
                                  "qty_on_hand")
    ok, issued = as_user(SELLER, service.issue_document, doc)
    results["issue"] = ok
    results["issue_error"] = None if ok else issued
    results["doc_no"] = issued.get("doc_no") if ok else None
    results["stock_posted"] = bool(issued.get("stock", {}).get("posted")) if ok else None
    after = frappe.db.get_value("ERP Stock Balance", {"product": product, "warehouse": warehouse},
                                "qty_on_hand")
    results["stock_delta"] = float(after) - float(on_hand) if on_hand is not None else None
    ok, again = as_user(SELLER, service.issue_document, doc)
    results["reissue_blocked"] = ok
    results["movements_after_reissue"] = frappe.db.count(
        "ERP Stock Movement", {"ref_id": doc})

    # 作废：货回来、号保留。
    ok, voided = as_user(SELLER, service.void_document, doc, "lab void")
    results["void"] = ok
    results["void_error"] = None if ok else voided
    results["number_kept"] = frappe.db.get_value("ERP Sales Document", doc, "doc_no")
    results["stock_after_void"] = float(frappe.db.get_value(
        "ERP Stock Balance", {"product": product, "warehouse": warehouse}, "qty_on_hand"))
    results["void_status"] = frappe.db.get_value("ERP Sales Document", doc, "status")

    # 第二张：号继续往后。
    second = service.create_document(doc_kind=contracts.TAX_INVOICE, doc_date="2026-09-21",
                                     buyer_name="Lab Buyer Co", party_type="company",
                                     tax_id="0105550000001", branch_type="hq", address="BKK",
                                     lines=lines)
    ok, issued2 = as_user(SELLER, service.issue_document, second["name"])
    results["second_doc_no"] = issued2.get("doc_no") if ok else None
    results["numbers_differ"] = (results.get("doc_no") != results.get("second_doc_no"))

    # 红冲：只能冲已开出的；草稿不行。
    ok, note = as_user(SELLER, service.create_note, second["name"], contracts.CREDIT_NOTE,
                       reason="lab credit")
    results["credit_note"] = ok
    results["credit_note_error"] = None if ok else note
    results["credit_note_kind"] = frappe.db.get_value("ERP Sales Document",
                                                      note.get("name"), "doc_kind") if ok else None
    results["credit_note_ref"] = frappe.db.get_value("ERP Sales Document", note.get("name"),
                                                     "references_document") if ok else None
    draft = service.create_document(doc_kind=contracts.QUOTATION, doc_date="2026-09-21",
                                    buyer_name="Lab Buyer Co", party_type="company",
                                    tax_id="0105550000001", branch_type="hq", address="BKK",
                                    lines=lines)
    ok, err = as_user(SELLER, service.create_note, draft["name"], contracts.CREDIT_NOTE)
    results["note_on_draft_blocked"] = ok
    results["note_on_draft_error"] = None if ok else err

    # 上传发票：识别被替换成固定结果（不花钱），落 source=ocr 草稿并留底。
    import io

    from PIL import Image

    from pearnly_erp_portal.sales import ocr as sales_ocr

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 120, 200)).save(buffer, "JPEG")
    jpeg = buffer.getvalue()
    original_recognise = sales_ocr.recognise
    sales_ocr.recognise = lambda content, content_type: {
        "doc_no": "INV-LAB-1", "doc_date": "2026-09-21", "buyer_name": "Lab OCR Buyer",
        "buyer_type": "company", "buyer_tax_id": "0105550000002",
        "buyer_branch_type": "hq", "buyer_address": "Chiang Mai", "has_vat": True,
        "vat_rate": 7, "lines": [{"description": "ocr line", "qty": 3, "unit_price": "50"}]}
    try:
        ok, up = as_user(SELLER, service.create_from_upload, content=jpeg,
                         content_type="image/jpeg", filename="lab-invoice.jpg")
    finally:
        sales_ocr.recognise = original_recognise
    results["upload"] = ok
    results["upload_error"] = None if ok else up
    results["upload_source"] = frappe.db.get_value("ERP Sales Document", up.get("name"),
                                                   "source") if ok else None
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

    # 报价 → 开票：转出草稿、开出时才取号；票面模型带泰文金额大写与未收款付款码。
    ok, conv = as_user(SELLER, service.convert_quotation, draft["name"],
                       contracts.TAX_INVOICE)
    results["convert"] = ok
    results["convert_error"] = None if ok else conv
    results["convert_has_no_number"] = frappe.db.get_value(
        "ERP Sales Document", conv.get("name"), "doc_no") if ok else None
    ok, conv_issued = as_user(SELLER, service.issue_document, conv.get("name"))
    results["convert_issue"] = ok
    results["convert_doc_no"] = conv_issued.get("doc_no") if ok else None
    results["convert_refs_quote"] = frappe.db.get_value(
        "ERP Sales Document", conv.get("name"), "references_document") if ok else None
    from pearnly_erp_portal.masters import company as company_master
    as_user(OWNER, company_master.update_profile, promptpay_id="0899999999")
    paid = service.create_document(doc_kind=contracts.RECEIPT, doc_date="2026-09-21",
                                   buyer_name="Walk-in", party_type="anonymous",
                                   payment_status="paid", payment_method="cash",
                                   payment_date="2026-09-21", lines=lines)
    as_user(SELLER, service.issue_document, paid["name"])
    ok, printed = as_user(SELLER, service.print_document, paid["name"])
    results["print"] = ok
    results["print_error"] = None if ok else printed
    results["print_words"] = printed.get("amount_in_words") if ok else None
    results["print_promptpay"] = printed.get("promptpay") if ok else None
    ok, printed2 = as_user(SELLER, service.print_document, results.get("second_doc_no") and
                           second["name"] or second["name"])
    results["print_unpaid_has_promptpay"] = bool(printed2.get("promptpay")) if ok else None

    # 岗位与隔离。
    ok, err = as_user(CASHIER, service.create_document, doc_kind=contracts.TAX_INVOICE,
                      doc_date="2026-09-21", buyer_name="X", lines=lines)
    results["cashier_can_create"] = ok
    ok, rows = as_user(SELLER, service.list_documents)
    results["seller_can_list"] = ok and len(rows) >= 3
    results["site_a_documents"] = frappe.db.count("ERP Sales Document")

    frappe.set_user("Administrator")
    frappe.destroy()
    connect(OTHER_SITE)
    results["other_site_documents"] = frappe.db.count("ERP Sales Document")

    expected = {
        "create": True, "draft_grand_total": "214.00", "draft_has_no_number": None,
        "zero_blocked": False, "zero_has_no_number": None, "unpaid_receipt_blocked": False,
        "issue": True, "stock_posted": True, "stock_delta": -2.0,
        "reissue_blocked": False, "movements_after_reissue": 1,
        "void": True, "number_kept": results.get("doc_no"), "void_status": "void",
        "numbers_differ": True, "credit_note": True,
        "credit_note_kind": "credit_note", "credit_note_ref": second.get("name"),
        "note_on_draft_blocked": False, "cashier_can_create": False,
        "upload": True, "upload_source": "ocr", "upload_total": "160.50",
        "upload_has_file": True, "upload_sha_set": True,
        "attach_recognition": True, "reviewed_total": "214.00",
        "convert": True, "convert_has_no_number": None, "convert_issue": True,
        "convert_refs_quote": draft["name"], "print": True,
        "print_words": "สองร้อยสิบสี่บาทถ้วน", "print_promptpay": "",
        "print_unpaid_has_promptpay": True,
        "seller_can_list": True, "other_site_documents": 0,
    }
    problems = {k: {"expected": v, "actual": results.get(k)} for k, v in expected.items()
                if results.get(k) != v}
    if results.get("stock_after_void") != float(on_hand):
        problems["stock_after_void"] = {"expected": on_hand,
                                        "actual": results.get("stock_after_void")}
    for key in ("zero_error", "note_on_draft_error"):
        if not str(results.get(key) or "").startswith("ValidationError"):
            problems[key] = results.get(key)
    print(json.dumps({"site": SITE, "results": results, "problems": problems},
                     ensure_ascii=False, indent=2, default=str))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
