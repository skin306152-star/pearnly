"""Verify the ERP product master on the dedicated laboratory.

Covers create / list / update / soft-delete, duplicate code and barcode rejection,
per-job access (a shop role may manage products but must not see cost), and tenant
isolation. Synthetic data and lab volume only.
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
SHOP = "erp-pos-products@example.com"
STOCK = "erp-pos-stock@example.com"
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
        return False, type(exc).__name__


def ensure_member(email, roles):
    if frappe.db.exists("User", email):
        user = frappe.get_doc("User", email)
        # 只重设 ERP 岗位，保留框架角色；child table 必须用 append 加行。
        user.roles = [r for r in user.roles if not r.role.startswith("ERP ")]
        have = {r.role for r in user.roles}
        if "Desk User" not in have:
            user.append("roles", {"role": "Desk User"})
        for role in roles:
            user.append("roles", {"role": role})
        user.save(ignore_permissions=True)
    else:
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
    from pearnly_erp_portal.products import service

    if CONTROL_APP not in sys.path:
        sys.path.insert(0, CONTROL_APP)
    from pearnly_control import features as control_features

    spec = control_features.roles_payload("erp", control_features.codes_of("erp"))
    ensure_feature_roles(json.dumps(spec), admin_user=OWNER)
    ensure_member(SHOP, ["ERP Products"])
    ensure_member(STOCK, ["ERP Stock"])
    ensure_member(OUTSIDER, [])

    results = {}
    results["spec_roles"] = sorted(r["role"] for r in spec["roles"])

    existing = frappe.db.get_value("ERP Product", {"code": "LAB-P1"}, "name")
    if existing:
        frappe.delete_doc("ERP Product", existing, force=True, ignore_permissions=True)
        frappe.db.commit()

    # 老板建品（数量用 Decimal 语义，价格留空不等于 0）。
    ok, created = as_user(OWNER, service.create_product, product_name="ผงซักฟอก",
                          code="LAB-P1", barcode="8850000000001", unit="ถุง",
                          unit_price="25", min_stock="5", default_cost="18")
    results["owner_create"] = ok
    results["created"] = created
    product = created["name"] if ok else None

    # 撞码 / 撞条码分别报错，且两处都不能绕过。
    ok, err = as_user(OWNER, service.create_product, product_name="ซ้ำรหัส", code="LAB-P1")
    results["duplicate_code_allowed"] = ok
    results["duplicate_code_error"] = None if ok else err
    ok, err = as_user(OWNER, service.create_product, product_name="ซ้ำบาร์โค้ด",
                      barcode="8850000000001")
    results["duplicate_barcode_allowed"] = ok
    results["duplicate_barcode_error"] = None if ok else err

    # 商品岗位能管商品，但成本对它遮蔽；库存岗位能看到成本。
    ok, shop_list = as_user(SHOP, service.list_products, limit=50)
    results["shop_can_list"] = ok
    shop_row = next((r for r in shop_list if r["name"] == product), None) if ok else None
    results["shop_sees_cost"] = None if shop_row is None else shop_row["default_cost"]
    ok, stock_list = as_user(STOCK, service.list_products, limit=50)
    results["stock_can_list"] = ok
    stock_row = next((r for r in stock_list if r["name"] == product), None) if ok else None
    results["stock_sees_cost"] = None if stock_row is None else stock_row["default_cost"]
    # 库存岗位能看商品，但不能改商品主数据。
    ok, err = as_user(STOCK, service.update_product, product, unit_price="30")
    results["stock_can_edit_products"] = ok
    results["stock_edit_error"] = None if ok else err
    ok, owner_list = as_user(OWNER, service.list_products, limit=50)
    owner_row = next((r for r in owner_list if r["name"] == product), None) if ok else None
    results["owner_sees_cost"] = None if owner_row is None else owner_row["default_cost"]

    # 没有商品岗位的账号读不到列表。
    ok, err = as_user(OUTSIDER, service.list_products, limit=50)
    results["outsider_can_list"] = ok
    results["outsider_list_error"] = None if ok else err

    # 商品岗位不能管理团队（能力分开）。
    from pearnly_erp_portal.team import service as team_service
    ok, err = as_user(SHOP, team_service.list_members)
    results["shop_manages_team"] = ok

    # 单位：换算系数必须为正；默认销售单位只留一个。
    ok, units = as_user(SHOP, service.add_unit, product, unit_name="โหล",
                        factor_to_base="12", barcode="8850000000018", price="280",
                        is_default_sell=True)
    results["add_unit"] = ok
    ok, err = as_user(SHOP, service.add_unit, product, unit_name="ผิด", factor_to_base="0")
    results["zero_factor_allowed"] = ok
    ok, second = as_user(SHOP, service.add_unit, product, unit_name="ลัง",
                         factor_to_base="144", is_default_sell=True)
    doc = frappe.get_doc("ERP Product", product)
    results["default_sell_units"] = len([r for r in doc.units if r.is_default_sell])

    # 停用是软删除：列表默认看不到，但仍能按名字取回并重新上架。
    ok, off = as_user(SHOP, service.set_active, product, 0)
    results["deactivate"] = off
    ok, listed = as_user(SHOP, service.list_products, limit=50)
    results["hidden_after_deactivate"] = all(r["name"] != product for r in listed)
    ok, back = as_user(SHOP, service.set_active, product, 1)
    results["reactivate"] = back

    # 改价用空值 = 清空，不是写 0。
    ok, _ = as_user(SHOP, service.update_product, product, unit_price="")
    results["price_after_blank"] = frappe.db.get_value("ERP Product", product, "unit_price")
    results["price_set_after_blank"] = bool(
        frappe.db.get_value("ERP Product", product, "price_set"))
    ok, _ = as_user(SHOP, service.update_product, product, unit_price="0")
    results["price_set_after_real_zero"] = bool(
        frappe.db.get_value("ERP Product", product, "price_set"))

    frappe.set_user("Administrator")
    results["row_count_site_a"] = frappe.db.count("ERP Product")

    frappe.destroy()
    connect(OTHER_SITE)
    results["other_site_product_rows"] = frappe.db.count("ERP Product")
    results["other_site_has_code"] = bool(
        frappe.db.exists("ERP Product", {"code": "LAB-P1"}))

    expected = {
        "owner_create": True,
        "duplicate_code_allowed": False,
        "duplicate_code_error": "ValidationError",
        "duplicate_barcode_allowed": False,
        "duplicate_barcode_error": "ValidationError",
        "shop_can_list": True,
        "shop_sees_cost": None,
        "stock_sees_cost": 18.0,
        "owner_sees_cost": 18.0,
        "outsider_can_list": False,
        "outsider_list_error": "PermissionError",
        "shop_manages_team": False,
        "add_unit": True,
        "zero_factor_allowed": False,
        "default_sell_units": 1,
        "hidden_after_deactivate": True,
        # Currency 列存不了 NULL，"没设价"由 price_set 标记承担（下一步断言）。
        "price_after_blank": 0.0,
        "price_set_after_blank": False,
        "price_set_after_real_zero": True,
        "other_site_product_rows": 0,
        "other_site_has_code": False,
        "stock_can_list": True,
        "stock_can_edit_products": False,
        "stock_edit_error": "PermissionError",
    }
    problems = {k: {"expected": v, "actual": results.get(k)} for k, v in expected.items()
                if results.get(k) != v}
    for key in ("deactivate", "reactivate"):
        if not (results.get(key) or {}).get("is_active") == (key == "reactivate"):
            problems[key] = results.get(key)
    if "ERP Products" not in (results.get("spec_roles") or []):
        problems["spec_missing_products_role"] = results.get("spec_roles")

    print(json.dumps({"site": site, "results": results, "problems": problems},
                     ensure_ascii=False, indent=2, default=str))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
