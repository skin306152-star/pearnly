"""Verify purchasing on the dedicated laboratory: records, suppliers and settings.

Real Site path with synthetic data: supplier master, purchase document lifecycle
(draft -> submitted -> void), duplicate blocking, partial payments, WHT net payable,
settings defaults, per-job access and record scope, and tenant isolation.
"""
import json
import os
from pathlib import Path
import sys

import frappe

CONTROL_APP = "/home/frappe/frappe-bench/apps/pearnly_control"
SITE = os.environ.get("PEARNLY_ERP_POS_LAB_SITE", "erp-pos-a.localhost")
OTHER_SITE = "erp-pos-b.localhost" if SITE != "erp-pos-b.localhost" else "erp-pos-a.localhost"
ALLOWED_SITES = {"erp-pos-a.localhost", "erp-pos-b.localhost"}
SITE_ROOT = Path("/home/frappe/frappe-bench/sites")

OWNER = "erp-pos-owner@example.com"
BUYER = "erp-pos-buyer@example.com"
OUTSIDER = "erp-pos-outsider@example.com"


def connect(site=SITE):
    if os.environ.get("PEARNLY_ERP_POS_LAB") != "1":
        raise SystemExit("Dedicated ERP/POS laboratory required")
    if site not in ALLOWED_SITES or not (SITE_ROOT / ".erp-pos-lab").is_file():
        raise SystemExit("Dedicated lab Site marker required")
    os.chdir(SITE_ROOT)
    frappe.init(site=site, sites_path=str(SITE_ROOT))
    frappe.connect()
    if not frappe.conf.get("pearnly_erp_pos_lab") or frappe.conf.get("db_host") != "db":
        raise SystemExit("Refusing a non-lab Site")
    frappe.flags.mute_emails = True
    return site


def as_user(user, func, *args, **kwargs):
    frappe.set_user(user)
    try:
        return True, func(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the exception type is the evidence
        return False, f"{type(exc).__name__}:{exc}" if _verbose else type(exc).__name__


_verbose = os.environ.get("PEARNLY_ERP_POS_LAB_VERBOSE") == "1"


def drop_user(email):
    if frappe.db.exists("User", email):
        frappe.delete_doc("User", email, force=True, ignore_permissions=True)
        frappe.db.commit()


def ensure_member(email, roles):
    drop_user(email)
    frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0],
                    "send_welcome_email": 0,
                    "roles": [{"role": "Desk User"}, *({"role": r} for r in roles)]
                    }).insert(ignore_permissions=True)
    frappe.db.commit()
    frappe.clear_cache(user=email)


def main():
    site = connect()
    frappe.set_user("Administrator")
    from pearnly_core.provisioning import ensure_feature_roles
    from pearnly_erp_portal.purchasing import service, settings, suppliers

    if CONTROL_APP not in sys.path:
        sys.path.insert(0, CONTROL_APP)
    from pearnly_control import features as control_features

    spec = control_features.roles_payload("erp", control_features.codes_of("erp"))
    ensure_feature_roles(json.dumps(spec), admin_user=OWNER)
    ensure_member(BUYER, ["ERP Purchase"])
    ensure_member(OUTSIDER, [])

    results = {}
    results["purchase_doctypes"] = sorted(
        do for do in ("Purchase Document", "Purchase Line", "ERP Supplier",
                      "ERP Purchase Settings", "ERP Purchase Payment")
        if frappe.db.exists("DocType", do))

    # 供应商：建、列、停用。
    existing = frappe.db.get_value("ERP Supplier", {"supplier_name": "Lab Supplier A"}, "name")
    if existing:
        frappe.delete_doc("ERP Supplier", existing, force=True, ignore_permissions=True)
        frappe.db.commit()
    ok, supplier = as_user(OWNER, suppliers.create_supplier, supplier_name="Lab Supplier A",
                           tax_id="0105550000000", branch_type="branch", branch_no="00001",
                           phone="02-000-0000")
    results["create_supplier"] = ok
    supplier_name = supplier["name"] if ok else None
    results["supplier_branch"] = frappe.db.get_value("ERP Supplier", supplier_name,
                                                     ["branch_type", "branch_no"],
                                                     as_dict=True) if supplier_name else None

    # 采购设置：默认值来自旧线上 SettingsIn。
    defaults = settings.read_settings()
    results["settings_defaults"] = {k: defaults[k] for k in
                                    ("default_vat_rate", "default_wht_service_rate",
                                     "base_currency", "dedupe_block", "auto_stock_in")}
    ok, changed = as_user(OWNER, settings.update_settings, default_due_days=30,
                          base_currency="THB")
    results["update_settings"] = ok
    ok, err = as_user(OWNER, settings.update_settings, default_vat_rate=150)
    results["bad_vat_rate_allowed"] = ok

    # 建单：默认 VAT 来自设置，WHT 按服务类税率算，净应付随之变化。
    # 可重复执行：清掉上一次运行留下的本脚本数据。
    for key in ("purchasing-a", "purchasing-b", "purchasing-c"):
        old = frappe.db.get_value("Purchase Document", {"dedupe_key": key}, "name")
        if old:
            frappe.delete_doc("Purchase Document", old, force=True, ignore_permissions=True)
            frappe.db.commit()
    for name in frappe.get_all("Purchase Document", filters={"supplier_name": ["like", "Lab Supplier%"]},
                               pluck="name"):
        frappe.delete_doc("Purchase Document", name, force=True, ignore_permissions=True)
    frappe.db.commit()

    as_user(OWNER, settings.update_settings, default_wht_service_rate=3)
    ok, created = as_user(OWNER, service.create_purchase, supplier_name="Lab Supplier A",
                          supplier=supplier_name, doc_no="IV-LAB-1", doc_date="2026-09-21",
                          doc_kind="service", lines=[{"description": "Service fee",
                                                      "qty": 1, "unit_price": 1000}],
                          wht_rate=3, dedupe_key="purchasing-a", source="web")
    results["owner_create"] = ok
    doc = created["name"] if ok else created
    results["document"] = doc
    amounts = frappe.db.get_value("Purchase Document", doc,
                                  ["subtotal", "vat_amount", "wht_amount", "grand_total",
                                   "net_payable", "payment_status", "status"],
                                  as_dict=True) if ok else None
    results["amounts"] = amounts

    # 重复单据：同一供应商 + 单据号必须被拦下。
    ok, err = as_user(OWNER, service.create_purchase, supplier_name="Lab Supplier A",
                      doc_no="IV-LAB-1", doc_date="2026-09-21",
                      lines=[{"description": "Again", "qty": 1, "unit_price": 10}])
    results["duplicate_allowed"] = ok
    results["duplicate_error"] = None if ok else err

    # 提交后才允许付款；付款分两笔，状态从 partial 变 paid。
    ok, err = as_user(OWNER, service.pay, doc, amount=100)
    results["pay_before_submit_allowed"] = ok
    ok, submitted = as_user(OWNER, service.set_status, doc, "submitted")
    results["submit"] = submitted
    ok, err = as_user(OWNER, service.update_purchase, doc, notes="changed after submit")
    results["edit_after_submit_allowed"] = ok
    ok, first = as_user(OWNER, service.pay, doc, amount=500, method="bank")
    results["partial_payment"] = first
    ok, second = as_user(OWNER, service.pay, doc, amount="470", method="cash")
    results["second_payment"] = second
    ok, third = as_user(OWNER, service.pay, doc, amount="70", method="cash")
    results["final_payment"] = third
    # 已结清后再付一块钱必须被拦下（净应付 1040）。
    ok, err = as_user(OWNER, service.pay, doc, amount=10)
    results["overpay_allowed"] = ok

    # 员工（采购岗位）能建自己的单，但看不到老板那张。
    ok, buyer_doc = as_user(BUYER, service.create_purchase, supplier_name="Lab Supplier B",
                            doc_no="IV-LAB-2", doc_date="2026-09-21",
                            lines=[{"description": "Buyer item", "qty": 2, "unit_price": 50}],
                            dedupe_key="purchasing-b")
    results["buyer_create"] = ok
    ok, buyer_rows = as_user(BUYER, service.list_purchases, limit=50)
    results["buyer_visible"] = len(buyer_rows) if ok else buyer_rows
    results["buyer_sees_owner_doc"] = ok and any(
        r["name"] == doc for r in buyer_rows)
    ok, owner_rows = as_user(OWNER, service.list_purchases, limit=50)
    results["owner_visible"] = len(owner_rows) if ok else owner_rows

    # 无采购岗位的账号：读、写、供应商、设置全部拒绝。
    results["outsider_read"] = as_user(OUTSIDER, service.list_purchases, limit=5)[0]
    results["outsider_write"] = as_user(OUTSIDER, service.create_purchase,
                                       supplier_name="X", doc_date="2026-09-21",
                                       lines=[{"description": "x", "qty": 1, "unit_price": 1}])[0]
    results["outsider_supplier"] = as_user(OUTSIDER, suppliers.list_suppliers, limit=5)[0]
    results["outsider_settings"] = as_user(OUTSIDER, settings.get_settings)[0]

    # 汇总只算非作废单据。
    ok, summary = as_user(OWNER, service.summary)
    results["summary"] = summary if ok else summary

    # 作废：用一张未付款的单验证；已付款的单必须被拒（先把付款冲掉）。
    ok, unpaid_doc = as_user(OWNER, service.create_purchase, supplier_name="Lab Supplier B",
                             doc_no="IV-LAB-3", doc_date="2026-09-21",
                             lines=[{"description": "Unpaid", "qty": 1, "unit_price": 100}],
                             dedupe_key="purchasing-c")
    results["create_unpaid"] = ok
    as_user(OWNER, service.set_status, unpaid_doc["name"], "submitted")
    ok, err = as_user(OWNER, service.set_status, doc, "void", reason="lab")
    results["void_paid_document_allowed"] = ok
    ok, voided = as_user(OWNER, service.set_status, unpaid_doc["name"], "void", reason="lab")
    results["void_submitted"] = voided
    ok, err = as_user(OWNER, service.update_purchase, unpaid_doc["name"], notes="after void")
    results["edit_after_void_allowed"] = ok

    # 付款过的单据不能直接作废（先把付款冲掉）。
    ok, refused = as_user(OWNER, service.set_status, buyer_doc, "submitted")
    ok, paid = as_user(OWNER, service.pay, buyer_doc, amount=100)
    ok, err = as_user(OWNER, service.set_status, buyer_doc, "void", reason="lab")
    results["void_with_payment_allowed"] = ok
    results["void_with_payment_error"] = None if ok else err

    frappe.set_user("Administrator")
    results["site_a_purchase_count"] = frappe.db.count("Purchase Document")
    results["site_a_supplier_count"] = frappe.db.count("ERP Supplier")

    frappe.destroy()
    connect(OTHER_SITE)
    results["other_site_purchase_rows"] = frappe.db.count("Purchase Document")
    results["other_site_supplier_rows"] = frappe.db.count("ERP Supplier")

    expected = {
        "create_supplier": True,
        "update_settings": True,
        "bad_vat_rate_allowed": False,
        "owner_create": True,
        "duplicate_allowed": False,
        "pay_before_submit_allowed": False,
        "edit_after_submit_allowed": False,
        "overpay_allowed": False,
        "buyer_create": True,
        "buyer_visible": 1,
        "buyer_sees_owner_doc": False,
        "outsider_read": False,
        "outsider_write": False,
        "outsider_supplier": False,
        "outsider_settings": False,
        "edit_after_void_allowed": False,
        "void_with_payment_allowed": False,
        "void_paid_document_allowed": False,
        "other_site_purchase_rows": 0,
        "other_site_supplier_rows": 0,
    }
    problems = {k: {"expected": v, "actual": results.get(k)} for k, v in expected.items()
                if results.get(k) != v}

    if results.get("amounts"):
        amounts = results["amounts"]
        if str(amounts["subtotal"]) != "1000.0":
            problems["subtotal"] = amounts["subtotal"]
        if str(amounts["vat_amount"]) != "70.0":
            problems["vat_amount"] = amounts["vat_amount"]
        if str(amounts["wht_amount"]) != "30.0":
            problems["wht_amount"] = amounts["wht_amount"]
        if str(amounts["net_payable"]) != "1040.0":
            problems["net_payable"] = amounts["net_payable"]
    else:
        problems["amounts"] = None
    if (results.get("partial_payment") or {}).get("payment_status") != "partial":
        problems["partial_status"] = results.get("partial_payment")
    if (results.get("final_payment") or {}).get("payment_status") != "paid":
        problems["final_status"] = results.get("final_payment")
    if (results.get("void_submitted") or {}).get("status") != "void":
        problems["void_status"] = results.get("void_submitted")
    if results.get("settings_defaults") != {
            "default_vat_rate": 7.0, "default_wht_service_rate": 3.0, "base_currency": "THB",
            "dedupe_block": 1, "auto_stock_in": 0}:
        problems["settings_defaults"] = results.get("settings_defaults")
    if sorted(results.get("purchase_doctypes") or []) != sorted(
            ["Purchase Document", "Purchase Line", "ERP Supplier",
             "ERP Purchase Payment", "ERP Purchase Settings"]):
        problems["purchase_doctypes"] = results.get("purchase_doctypes")

    print(json.dumps({"site": site, "results": results, "problems": problems},
                     ensure_ascii=False, indent=2, default=str))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
