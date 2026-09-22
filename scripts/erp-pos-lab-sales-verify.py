"""Verify the sales record service on the dedicated laboratory.

Real Site with synthetic data. Covers: one-submit posting (record + goods out + collection
mark) and its replay guard, the amount gate, the removed tax-document kinds being rejected,
collection by amount, list/search/filter, private attachments and the PDF page renderer,
the workbook export, record scope and tenant isolation.

**Samples are created on the isolated check Site** (`erp-pos-b.localhost`) and the demo Site
(`erp-pos-a.localhost`) is only read, so a probe run can never touch the demo's records.
Nothing is deleted wholesale: this probe never wipes a Site's sales documents.
"""
import base64
import io
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import frappe
import pymupdf
from PIL import Image

CONTROL_APP = "/home/frappe/frappe-bench/apps/pearnly_control"
SITE = "erp-pos-b.localhost"
DEMO_SITE = "erp-pos-a.localhost"
SITE_ROOT = Path("/home/frappe/frappe-bench/sites")
OWNER = "erp-pos-owner@example.com"
SELLER = "erp-pos-sales2@example.com"
CASHIER = "erp-pos-sales2-cashier@example.com"
OUTSIDER = "erp-pos-sales2-outsider@example.com"


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


def private_file(name, content, content_type="image/png"):
    return frappe.get_doc({"doctype": "File", "file_name": name, "is_private": 1,
                           "content": content}).insert(ignore_permissions=True)


def png():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 120, 200)).save(buffer, "PNG")
    return buffer.getvalue()


def pdf(pages=2):
    with pymupdf.open() as document:
        for number in range(1, pages + 1):
            page = document.new_page(width=400, height=560)
            page.insert_text((35, 60), f"SALES VIEWER TEST - PAGE {number}", fontsize=16)
        return document.tobytes()


def main():
    connect(SITE)
    frappe.set_user("Administrator")
    from pearnly_core.provisioning import ensure_feature_roles
    from pearnly_erp_portal.sales import contracts, entry, files, output, preview, records, recognition
    from pearnly_erp_portal.products import service as products
    from pearnly_erp_portal.inventory import stock, warehouses

    if CONTROL_APP not in sys.path:
        sys.path.insert(0, CONTROL_APP)
    from pearnly_control import features as control_features

    spec = control_features.roles_payload("erp", control_features.codes_of("erp"))
    ensure_feature_roles(json.dumps(spec), admin_user=OWNER)
    ensure_member(SELLER, ["ERP Sales"])
    ensure_member(CASHIER, ["ERP Cashier"])
    ensure_member(OUTSIDER, [])

    code = "LAB-SALE-P1"
    if not frappe.db.get_value("ERP Product", {"code": code}, "name"):
        products.create_product(product_name="Lab sale item", code=code, unit="pcs",
                                unit_price="100", default_cost="40")
    product = frappe.db.get_value("ERP Product", {"code": code}, "name")
    warehouse = warehouses.ensure_default()
    stock.apply_movement(warehouse=warehouse, movement_type="stock_in", product=product,
                         qty=100, unit="pcs", unit_cost="40")

    results = {}
    run = "lab-sale-" + frappe.generate_hash(length=8)
    lines = [{"product": product, "description": "Lab sale item", "qty": 2, "unit": "pcs",
              "unit_price": "100", "vat_applicable": 1}]
    payload = {"doc_kind": "receipt", "doc_date": "2026-09-21", "buyer_name": f"{run} buyer",
               "party_type": "person", "address": "Bangkok", "has_vat": 1, "vat_rate": 7,
               "is_paid": 1, "payment_method": "cash", "payment_date": "2026-09-21",
               "lines": lines}

    # 一次提交：落定 + 出库 + 收款标记。
    on_hand = balance(product, warehouse)
    ok, made = as_user(SELLER, entry.post, dict(payload), request_id=run)
    results["post"] = ok
    results["post_error"] = None if ok else made
    doc = made.get("name") if ok else None
    results["status"] = frappe.db.get_value("ERP Sales Document", doc, "status") if doc else None
    results["system_no_set"] = bool(frappe.db.get_value("ERP Sales Document", doc, "system_no")) if doc else None
    results["summary"] = frappe.db.get_value("ERP Sales Document", doc, "summary") if doc else None
    results["grand_total"] = float(frappe.db.get_value("ERP Sales Document", doc, "grand_total")) if doc else None
    results["vat_amount"] = float(frappe.db.get_value("ERP Sales Document", doc, "vat_amount")) if doc else None
    results["payment_status"] = frappe.db.get_value("ERP Sales Document", doc, "payment_status") if doc else None
    results["paid_amount"] = float(frappe.db.get_value("ERP Sales Document", doc, "paid_amount")) if doc else None
    after = balance(product, warehouse)
    results["stock_delta"] = float(after) - float(on_hand) if on_hand is not None else None
    results["movements"] = frappe.db.count("ERP Stock Movement", {"ref_id": doc}) if doc else None

    # 重复提交同一个请求号：返回同一张单，不再出一次库。
    ok, replay = as_user(SELLER, entry.post, dict(payload), request_id=run)
    results["replay_duplicate"] = bool(replay.get("duplicate")) if ok else None
    results["replay_same_doc"] = replay.get("name") == doc if ok else None
    results["movements_after_replay"] = frappe.db.count("ERP Stock Movement", {"ref_id": doc}) if doc else None

    # 闸：฿0 的收据落不了定；税票与冲正单类型一律被拒。
    zero = {**payload, "buyer_name": f"{run} zero", "lines": [{"description": "free", "qty": 1,
            "unit_price": 0, "vat_applicable": 1}]}
    ok, err = as_user(SELLER, entry.post, zero, request_id=run + "-zero")
    results["zero_blocked"] = ok
    for kind in ("tax_invoice", "tax_invoice_simple", "tax_invoice_receipt", "credit_note",
                 "debit_note"):
        ok, err = as_user(SELLER, entry.post, {**payload, "doc_kind": kind},
                          request_id=f"{run}-{kind}")
        results[f"reject_{kind}"] = ok

    # 报价单：不动货、不收钱。
    before_quote = balance(product, warehouse)
    ok, quote = as_user(SELLER, entry.post, {**payload, "doc_kind": "quotation",
                       "buyer_name": f"{run} quote"}, request_id=run + "-quote")
    results["quotation_post"] = ok
    results["quotation_moves_nothing"] = frappe.db.count("ERP Stock Movement",
                                                         {"ref_id": quote.get("name")}) if ok else None
    results["quotation_stock_unchanged"] = float(balance(product, warehouse)) == float(before_quote)
    results["quotation_unpaid"] = frappe.db.get_value("ERP Sales Document", quote.get("name"),
                                                      "payment_status") if ok else None

    # 收款：部分 → 部分收款；收满 → 已收款；撤销 → 未收款。
    ok, part = as_user(SELLER, entry.payment, doc, amount=100, method="cash", date="2026-09-21")
    results["partial_status"] = part.get("payment_status") if ok else None
    ok, paid = as_user(SELLER, entry.payment, doc, paid=1, method="transfer", date="2026-09-22")
    results["paid_status"] = paid.get("payment_status") if ok else None
    results["paid_method"] = frappe.db.get_value("ERP Sales Document", doc, "payment_method")
    ok, back = as_user(SELLER, entry.payment, doc, paid=0)
    results["reverted_status"] = back.get("payment_status") if ok else None
    results["reverted_amount"] = float(frappe.db.get_value("ERP Sales Document", doc, "paid_amount"))
    ok, again = as_user(SELLER, entry.payment, doc, paid=1, method="cash")
    results["paid_again"] = ok and again.get("payment_status") == "paid"

    # 列表：条件、搜索、命中总数。
    ok, page_all = as_user(SELLER, records.page, {"date_from": "2026-09-01",
                                                  "date_to": "2026-09-30"}, start=0, limit=20)
    results["list_ok"] = ok
    results["list_has_own_rows"] = bool(ok and page_all["total"] >= 2)
    ok, page_q = as_user(SELLER, records.page, {"q": f"{run} buyer"}, start=0, limit=20)
    results["search_hits"] = page_q["total"] if ok else None
    ok, page_kind = as_user(SELLER, records.page, {"doc_kind": ["quotation"]}, start=0, limit=20)
    results["kind_filter_only_quotes"] = bool(ok and page_kind["items"]
        and all(row["doc_kind"] == "quotation" for row in page_kind["items"]))
    ok, page_unpaid = as_user(SELLER, records.page, {"group": "unpaid"}, start=0, limit=20)
    results["unpaid_group_excludes_paid"] = bool(ok and all(
        row["payment_status"] != "paid" for row in page_unpaid["items"]))

    # 附件：私有上传、绑定、图库、PDF 分页渲染。
    file = private_file(f"{run}.png", png())
    as_user(SELLER, frappe.db.set_value, "File", file.name, "owner", SELLER)
    frappe.set_user(SELLER)
    file.reload()
    ok, bound = as_user(SELLER, files.add, doc, [{"file": file.name, "file_url": file.file_url,
                                                  "file_name": file.file_name, "kind": "payment_proof"}])
    results["attach"] = ok
    results["attach_error"] = None if ok else bound
    ok, page = as_user(SELLER, preview.page_image, file.name, 1)
    results["preview_png_rejected"] = ok  # 图片不是 PDF：查看器走后端只渲染 PDF
    pdf_file = private_file(f"{run}.pdf", pdf(2))
    as_user(SELLER, frappe.db.set_value, "File", pdf_file.name, "owner", SELLER)
    ok, pages = as_user(SELLER, preview.page_image, pdf_file.name)
    results["preview_pdf"] = bool(ok and pages["pages"] == 2
                                  and base64.b64decode(pages["image"].split(",")[1]).startswith(b"\x89PNG"))
    ok, second = as_user(SELLER, preview.page_image, pdf_file.name, 2)
    results["preview_pdf_second_page"] = bool(ok and second.get("image") and second["image"] != pages["image"])

    # 导出：同一个查询出来的 Excel。
    ok, book = as_user(SELLER, output.workbook, {"q": run})
    results["export_bytes"] = bool(ok and book.startswith(b"PK") and len(book) > 2000)

    # 岗位与本人范围。
    ok, err = as_user(CASHIER, entry.post, {**payload, "buyer_name": f"{run} cashier"},
                      request_id=run + "-cashier")
    results["cashier_can_post"] = ok
    ok, rows = as_user(OUTSIDER, records.page, {}, start=0, limit=5)
    results["outsider_blocked"] = ok is False
    # 员工只看自己录的：老板能读到销售的同一天记录，另一个销售看不到。
    ok, other = as_user(OWNER, records.page, {"q": f"{run} buyer"}, start=0, limit=5)
    results["owner_sees_record"] = bool(ok and other["total"] == 1)

    # 作废：货回来、状态与原因留痕。
    ok, voided = as_user(SELLER, entry.cancel, doc, "lab void")
    results["cancel"] = ok
    results["cancel_error"] = None if ok else voided
    results["cancel_status"] = frappe.db.get_value("ERP Sales Document", doc, "status")
    results["cancel_reason"] = frappe.db.get_value("ERP Sales Document", doc, "void_reason")
    results["stock_after_cancel"] = float(balance(product, warehouse))
    ok, err = as_user(SELLER, entry.cancel, doc, "again")
    results["recancel_blocked"] = ok
    # 作废产生的反向流水要带**本单的单据号**：收发存的"关联单据"列显示的是它，
    # 不带的话报表上那笔冲销会显示成内部单据名（用户 2026-09-23 报的就是这条）。
    reversal = frappe.db.get_value("ERP Stock Movement", {"ref_type": "void", "ref_id": doc},
                                  ["doc_no", "movement_type"], as_dict=True) or {}
    results["void_movement_doc_no"] = reversal.get("doc_no")
    results["void_movement_matches_number"] = bool(
        reversal.get("doc_no") and reversal.get("doc_no") == frappe.db.get_value(
            "ERP Sales Document", doc, "system_no"))

    # 删草稿：草稿能动、已落定/已作废的只能作废；要写岗位也要本人范围。
    # 框架那一位仍然关着（共享口径不给功能角色 delete），放行的是产品自己的动作。
    draft = frappe.get_doc({"doctype": "ERP Sales Document", "status": "draft",
                            "doc_kind": "quotation", "doc_date": "2026-09-21",
                            "buyer_name": f"{run} draft", "has_vat": 0,
                            "lines": [{"description": "Lab draft line", "qty": 1, "unit": "pcs",
                                       "unit_price": "10", "vat_applicable": 0}]})
    frappe.set_user(SELLER)
    draft.flags.ignore_permissions = True
    draft.insert()
    frappe.db.commit()
    results["draft_owner"] = frappe.db.get_value("ERP Sales Document", draft.name, "owner")
    results["framework_delete_denied"] = not frappe.has_permission("ERP Sales Document", "delete")
    results["draft_delete_by_outsider_blocked"] = not as_user(OUTSIDER, entry.delete_drafts,
                                                              [draft.name])[0]
    results["draft_delete_by_cashier_blocked"] = not as_user(CASHIER, entry.delete_drafts,
                                                             [draft.name])[0]
    results["submitted_delete_blocked"] = not as_user(SELLER, entry.delete_drafts, [doc])[0]
    ok, deleted = as_user(SELLER, entry.delete_drafts, [draft.name])
    results["delete_drafts"] = ok
    results["delete_drafts_error"] = None if ok else deleted
    results["draft_gone"] = not frappe.db.exists("ERP Sales Document", draft.name)
    results["void_still_present"] = bool(frappe.db.exists("ERP Sales Document", doc))

    # 识别：模型调用被替换（不花钱），归一化后落到建单字段。
    from pearnly_core import ocr as core_ocr

    original = (core_ocr.is_configured, core_ocr.json_from_images, core_ocr.try_with_fallback)
    core_ocr.is_configured = lambda: True
    core_ocr.json_from_images = lambda prompt, images, model=None: {
        "doc_date": "2026-09-21", "buyer_name": "Lab OCR Buyer", "buyer_type": "company",
        "buyer_address": "Chiang Mai", "has_vat": True, "vat_rate": 7,
        "lines": [{"description": "ocr line", "qty": 3, "unit_price": "50"}]}
    core_ocr.try_with_fallback = lambda call, ok=None, label=None: call(None)
    try:
        ocr_file = private_file(f"{run}-ocr.png", png())
        as_user(SELLER, frappe.db.set_value, "File", ocr_file.name, "owner", SELLER)
        frappe.set_user(SELLER)
        ocr_file.reload()
        ok, recognised = as_user(SELLER, recognition.recognise,
                                 [{"file": ocr_file.name, "file_url": ocr_file.file_url,
                                   "file_name": ocr_file.file_name, "kind": "bill"}])
    finally:
        core_ocr.is_configured, core_ocr.json_from_images, core_ocr.try_with_fallback = original
    results["recognise"] = ok
    results["recognise_error"] = None if ok else recognised
    results["recognise_kind"] = recognised.get("doc_kind") if ok else None
    results["recognise_buyer"] = recognised.get("buyer_name") if ok else None
    results["recognise_lines"] = len(recognised.get("lines") or []) if ok else None
    results["recognise_source"] = recognised.get("source") if ok else None
    results["recognise_hash_set"] = bool(recognised.get("image_sha256")) if ok else None

    results["site_records"] = frappe.db.count("ERP Sales Document")
    frappe.set_user("Administrator")
    frappe.destroy()
    connect(DEMO_SITE)
    results["demo_site_records"] = frappe.db.count("ERP Sales Document")
    frappe.destroy()

    expected = {
        "post": True, "status": "submitted", "system_no_set": True,
        "summary": "Lab sale item", "grand_total": 214.0, "vat_amount": 14.0,
        "payment_status": "paid", "paid_amount": 214.0, "stock_delta": -2.0, "movements": 1,
        "replay_duplicate": True, "replay_same_doc": True, "movements_after_replay": 1,
        "zero_blocked": False,
        "reject_tax_invoice": False, "reject_tax_invoice_simple": False,
        "reject_tax_invoice_receipt": False, "reject_credit_note": False,
        "reject_debit_note": False,
        "quotation_post": True, "quotation_moves_nothing": 0, "quotation_stock_unchanged": True,
        "quotation_unpaid": "unpaid",
        "partial_status": "partial", "paid_status": "paid", "paid_method": "transfer",
        "reverted_status": "unpaid", "reverted_amount": 0.0, "paid_again": True,
        "list_ok": True, "list_has_own_rows": True, "search_hits": 1,
        "kind_filter_only_quotes": True, "unpaid_group_excludes_paid": True,
        "attach": True, "preview_png_rejected": False, "preview_pdf": True,
        "preview_pdf_second_page": True, "export_bytes": True,
        "cashier_can_post": False, "outsider_blocked": True, "owner_sees_record": True,
        "cancel": True, "cancel_status": "void", "cancel_reason": "lab void",
        "recancel_blocked": False,
        # 号码本身随站点计数器变，所以只断言"冲销行带的就是本单的号"。
        "void_movement_matches_number": True,
        "draft_owner": SELLER, "framework_delete_denied": True,
        "draft_delete_by_outsider_blocked": True, "draft_delete_by_cashier_blocked": True,
        "submitted_delete_blocked": True, "delete_drafts": True, "draft_gone": True,
        "void_still_present": True,
        "recognise": True, "recognise_kind": "receipt", "recognise_buyer": "Lab OCR Buyer",
        "recognise_lines": 1, "recognise_source": "ocr", "recognise_hash_set": True,
    }
    problems = {k: {"expected": v, "actual": results.get(k)} for k, v in expected.items()
                if results.get(k) != v}
    if on_hand is not None and results.get("stock_after_cancel") != float(on_hand):
        problems["stock_after_cancel"] = {"expected": float(on_hand),
                                          "actual": results.get("stock_after_cancel")}
    print(json.dumps({"site": SITE, "demo_site": DEMO_SITE, "results": results,
                      "problems": problems}, ensure_ascii=False, indent=2, default=str))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
