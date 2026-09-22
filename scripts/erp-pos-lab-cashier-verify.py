"""Verify the cashier counter on the dedicated laboratory.

Real Site path with synthetic data: default terminal, opening a shift (one per
terminal), selling with cash change and split payments, stock leaving the
warehouse, idempotent repeat submit, refund and void putting stock back, shift
summary and close with the cash difference, the cashier seeing only their own
records while the owner sees all, and tenant isolation.
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
CASHIER = "erp-pos-cashier-shift@example.com"
CASHIER2 = "erp-pos-cashier-two@example.com"
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


def product(code, name, unit="pcs"):
    existing = frappe.db.get_value("ERP Product", {"code": code}, "name")
    if existing:
        return existing
    from pearnly_erp_portal.products import service

    return service.create_product(product_name=name, code=code, unit=unit)["name"]


def main():
    site = connect()
    frappe.set_user("Administrator")
    from pearnly_core.provisioning import ensure_feature_roles
    from pearnly_erp_portal.cashier import sales, shifts, terminals
    from pearnly_erp_portal.inventory import stock, warehouses

    if CONTROL_APP not in sys.path:
        sys.path.insert(0, CONTROL_APP)
    from pearnly_control import features as control_features

    spec = control_features.roles_payload("erp", control_features.codes_of("erp"))
    ensure_feature_roles(json.dumps(spec), admin_user=OWNER)
    ensure_member(CASHIER, ["ERP Cashier"])
    ensure_member(CASHIER2, ["ERP Cashier"])
    ensure_member(OUTSIDER, [])

    results = {}
    results["cashier_doctypes"] = sorted(
        d for d in ("ERP POS Terminal", "ERP Shift", "ERP Sale", "ERP Sale Line",
                    "ERP Sale Payment") if frappe.db.exists("DocType", d))

    warehouse = warehouses.ensure_default()
    item = product("LAB-POS-A", "สินค้าหน้าร้าน A")
    item2 = product("LAB-POS-B", "สินค้าหน้าร้าน B")

    # 干净起点：本脚本的销售与流水，再把结存按流水重算。
    for do in ("ERP Sale", "ERP Shift"):
        for name in frappe.get_all(do, filters={"name": ["like", "%"]} if do == "ERP Shift"
                                   else {"cashier": ["in", [CASHIER, CASHIER2, OWNER]]},
                                   pluck="name"):
            frappe.delete_doc(do, name, force=True, ignore_permissions=True)
    for name in frappe.get_all("ERP Stock Movement", filters={"product": ["in", [item, item2]]},
                               pluck="name"):
        frappe.delete_doc("ERP Stock Movement", name, force=True, ignore_permissions=True)
    frappe.db.commit()
    for product_id in (item, item2):
        stock.rebuild_balance(product_id, warehouse)

    # 上货：各 20 件，成本 10。
    for product_id, qty in ((item, 20), (item2, 20)):
        as_user(OWNER, stock.apply_movement, product=product_id, warehouse=warehouse,
                movement_type="stock_in", qty=qty, unit_cost=10, posting_date="2026-09-15",
                client_uuid=f"lab-pos-stock-{product_id}")

    # 终端：没有收银台时自动建默认台。
    terminal = terminals.ensure_default()
    results["default_terminal"] = bool(terminal)

    # 没有开班就卖 → 拒绝（否则交班对不上账）。
    no_shift = as_user(CASHIER, sales.create_sale, lines=[{"product": item, "qty": 1,
                                                           "unit_price": 50}],
                       payments=[{"method": "cash", "amount": 50}])
    results["sell_without_shift_allowed"] = no_shift[0]
    results["sell_without_shift_error"] = None if no_shift[0] else no_shift[1]

    # 开班：备用金 500。
    ok, shift = as_user(CASHIER, shifts.open_shift, terminal=terminal, opening_float=500)
    results["open_shift"] = ok
    shift_name = shift["name"] if ok else shift
    # 同一收银台第二个收银员不能同时开班。
    ok, err = as_user(CASHIER2, shifts.open_shift, terminal=terminal, opening_float=100)
    results["second_shift_same_terminal_allowed"] = ok

    # 现金销售 87，收 100 → 找零 13；库存 20 → 19。
    ok, sale = as_user(CASHIER, sales.create_sale,
                       lines=[{"product": item, "description": "A", "qty": 1, "unit_price": 87}],
                       payments=[{"method": "cash", "amount": 100}], client_uuid="lab-pos-sale-1")
    results["cash_sale"] = sale
    results["change_amount"] = sale.get("change_amount") if ok else sale
    results["stock_after_sale"] = float(frappe.db.get_value(
        "ERP Stock Balance", f"BAL-{item}-{warehouse}", "qty_on_hand") or 0)

    # 重复提交同一个幂等锚：返回同一张单，不再扣库存。
    ok, again = as_user(CASHIER, sales.create_sale,
                        lines=[{"product": item, "description": "A", "qty": 1, "unit_price": 87}],
                        payments=[{"method": "cash", "amount": 100}], client_uuid="lab-pos-sale-1")
    results["repeat_deduped"] = again.get("deduped") if isinstance(again, dict) else again
    results["stock_after_repeat"] = float(frappe.db.get_value(
        "ERP Stock Balance", f"BAL-{item}-{warehouse}", "qty_on_hand") or 0)

    # 混合付款：刷卡 50 + 现金 60，应收 100 → 找零 10（现金够）。
    ok, split = as_user(CASHIER, sales.create_sale,
                        lines=[{"product": item2, "description": "B", "qty": 2, "unit_price": 50}],
                        payments=[{"method": "card", "amount": 50},
                                  {"method": "cash", "amount": 60}],
                        client_uuid="lab-pos-sale-2")
    results["split_sale"] = split
    # 非现金多付：刷卡 120 买 100，不给找零。
    ok, err = as_user(CASHIER, sales.create_sale,
                      lines=[{"product": item2, "description": "B", "qty": 2, "unit_price": 50}],
                      payments=[{"method": "card", "amount": 120}],
                      client_uuid="lab-pos-sale-3")
    results["noncash_overpay_allowed"] = ok
    # 少付：现金 90 买 100。
    ok, err = as_user(CASHIER, sales.create_sale,
                      lines=[{"product": item2, "description": "B", "qty": 2, "unit_price": 50}],
                      payments=[{"method": "cash", "amount": 90}],
                      client_uuid="lab-pos-sale-4")
    results["underpay_allowed"] = ok

    # 当班汇总：两笔销售、现金净额、应有现金。
    ok, summary = as_user(CASHIER, shifts.summary, shift_name)
    results["shift_summary"] = summary

    # 收银员只看到自己的单；老板看到全店；另一个收银员一张都看不到。
    ok, other = as_user(CASHIER2, sales.list_sales, limit=50)
    results["other_cashier_sales_count"] = len(other) if ok else other
    ok, err = as_user(OUTSIDER, sales.list_sales, limit=50)
    results["outsider_can_list_sales"] = ok

    # 退货：把 1 件加回库存。
    first = sale["name"] if isinstance(sale, dict) else None
    ok, refunded = as_user(CASHIER, sales.refund, first,
                           payments=[{"method": "cash", "amount": 87}], reason="lab")
    results["refund"] = refunded
    results["stock_after_refund"] = float(frappe.db.get_value(
        "ERP Stock Balance", f"BAL-{item}-{warehouse}", "qty_on_hand") or 0)
    # 再退一次同样的数量必须被拒（不能退超）。
    ok, err = as_user(CASHIER, sales.refund, first,
                      payments=[{"method": "cash", "amount": 87}], reason="lab-again")
    results["second_refund_allowed"] = ok

    # 退货之后再看清单：两笔销售 + 一笔退货，且退货只对本人可见。
    ok, mine = as_user(CASHIER, sales.list_sales, limit=50)
    results["cashier_sales_count"] = len(mine) if ok else mine
    results["cashier_sees_own_refund"] = ok and any(
        r["sale_type"] == "refund" and r["return_against"] == first for r in mine)
    ok, owner_rows = as_user(OWNER, sales.list_sales, limit=50)
    results["owner_sales_count"] = len([r for r in owner_rows if r["name"] in {m["name"] for m in mine}]) if ok else owner_rows

    # 交班：实点与应有之差。
    ok, closed = as_user(CASHIER, shifts.close_shift, shift_name, counted_cash=1500)
    results["close_shift"] = {k: closed[k] for k in
                              ("status", "expected_cash", "counted_cash", "cash_diff")} if ok else closed
    # 别人的班不能关。
    ok, err = as_user(CASHIER2, shifts.close_shift, shift_name, counted_cash=0)
    results["other_cashier_can_close"] = ok

    # 作废：销售作废后库存还回去。
    ok, second_sale = as_user(CASHIER, sales.list_sales, limit=5)
    target = next((r["name"] for r in second_sale if r["name"] != first and r["sale_type"] == "sale"),
                  None) if ok else None
    if target:
        before_void = float(frappe.db.get_value(
            "ERP Stock Balance", f"BAL-{item2}-{warehouse}", "qty_on_hand") or 0)
        ok, voided = as_user(CASHIER, sales.void_sale, target, reason="lab void")
        after_void = float(frappe.db.get_value(
            "ERP Stock Balance", f"BAL-{item2}-{warehouse}", "qty_on_hand") or 0)
        results["void"] = voided
        results["void_restored_stock"] = after_void > before_void

    frappe.set_user("Administrator")
    results["site_a_sales"] = frappe.db.count("ERP Sale")
    results["site_a_shifts"] = frappe.db.count("ERP Shift")

    frappe.destroy()
    connect(OTHER_SITE)
    results["other_site_sales"] = frappe.db.count("ERP Sale")
    results["other_site_shifts"] = frappe.db.count("ERP Shift")

    expected = {
        "default_terminal": True,
        "sell_without_shift_allowed": False,
        "open_shift": True,
        "second_shift_same_terminal_allowed": False,
        "change_amount": 13.0,
        "stock_after_sale": 19.0,
        "repeat_deduped": True,
        "stock_after_repeat": 19.0,
        "noncash_overpay_allowed": False,
        "underpay_allowed": False,
        "cashier_sales_count": 3,
        "cashier_sees_own_refund": True,
        "other_cashier_sales_count": 0,
        "owner_sales_count": 3,
        "outsider_can_list_sales": False,
        "stock_after_refund": 20.0,
        "second_refund_allowed": False,
        "other_cashier_can_close": False,
        "void_restored_stock": True,
        "other_site_sales": 0,
        "other_site_shifts": 0,
    }
    problems = {k: {"expected": v, "actual": results.get(k)} for k, v in expected.items()
                if results.get(k) != v}
    if results.get("cashier_doctypes") != sorted(
            ["ERP POS Terminal", "ERP Shift", "ERP Sale", "ERP Sale Line", "ERP Sale Payment"]):
        problems["cashier_doctypes"] = results.get("cashier_doctypes")
    summary = results.get("shift_summary") or {}
    # 当班：现金 100 + 60 = 160 收，找零 13 + 10 = 23；应有 = 500 + 160 − 23 = 637。
    if summary.get("sales_count") != 2:
        problems["summary_sales_count"] = summary.get("sales_count")
    if abs(float(summary.get("expected_cash", 0)) - 637.0) > 0.01:
        problems["summary_expected_cash"] = summary.get("expected_cash")
    closed = results.get("close_shift") or {}
    # 交班时已经退过一笔：现金 = 160 − 87 = 73，找零 23，应有 = 500 + 73 − 23 = 550。
    if closed and abs(float(closed.get("expected_cash", 0)) - 550.0) > 0.01:
        problems["close_expected_cash"] = closed
    if closed and abs(float(closed.get("cash_diff", 0))
                      - (1500 - float(closed.get("expected_cash", 0)))) > 0.01:
        problems["close_cash_diff"] = closed

    print(json.dumps({"site": site, "results": results, "problems": problems},
                     ensure_ascii=False, indent=2, default=str))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
