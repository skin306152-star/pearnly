"""Verify stock counts on the dedicated laboratory.

Real Site path with synthetic data: snapshot from live balances, append-only count
entries (idempotent, editable, voidable), scanning a product that is not in the
snapshot, closing, posting the differences into stock, the Excel export, per-job
access and the sidebar gate.
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
STOCK = "erp-pos-stock@example.com"
SHOP = "erp-pos-products@example.com"
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


def product(code, name, unit="pcs"):
    from pearnly_erp_portal.products import service

    existing = frappe.db.get_value("ERP Product", {"code": code}, "name")
    if existing:
        return existing
    return service.create_product(product_name=name, code=code, unit=unit)["name"]


def main():
    site = connect()
    frappe.set_user("Administrator")
    from pearnly_core.provisioning import ensure_feature_roles
    from pearnly_erp_portal.inventory import api as inventory_api
    from pearnly_erp_portal.inventory import count as counts
    from pearnly_erp_portal.inventory import stock, warehouses

    if CONTROL_APP not in sys.path:
        sys.path.insert(0, CONTROL_APP)
    from pearnly_control import features as control_features

    spec = control_features.roles_payload("erp", control_features.codes_of("erp"))
    ensure_feature_roles(json.dumps(spec), admin_user=OWNER)
    ensure_member(STOCK, ["ERP Stock"])
    ensure_member(SHOP, ["ERP Products"])
    ensure_member(OUTSIDER, [])

    # 功能码要在这个站点上"买到了"才算数（样板站点在开通时写死一份，实验室自己补）
    from frappe.installer import update_site_config
    features = [str(code) for code in (frappe.conf.get("pearnly_features") or [])]
    if "stocktake" not in features:
        update_site_config("pearnly_features", features + ["stocktake"])
        frappe.conf["pearnly_features"] = features + ["stocktake"]

    results = {}
    warehouse = warehouses.ensure_default()
    watched = [product("LAB-SC-A", "Lab count A"), product("LAB-SC-B", "Lab count B")]
    untouched = product("LAB-SC-C", "Lab count untouched")

    for name in frappe.get_all("ERP Stock Count", filters={"title": ["like", "Lab count %"]},
                               pluck="name"):
        frappe.delete_doc("ERP Stock Count", name, force=True, ignore_permissions=True)
    frappe.db.delete("ERP Stock Count Item", {"count": ["like", "STC-%"]})
    frappe.db.delete("ERP Stock Count Entry", {"count": ["like", "STC-%"]})
    for do in ("ERP Stock Movement", "ERP Stock Balance"):
        for name in frappe.get_all(do, filters={"product": ["in", watched + [untouched]]},
                                   pluck="name"):
            frappe.delete_doc(do, name, force=True, ignore_permissions=True)
    frappe.db.commit()
    for product_id in watched + [untouched]:
        stock.rebuild_balance(product_id, warehouse)

    for product_id, qty in ((watched[0], 10), (watched[1], 3), (untouched, 7)):
        stock.apply_movement(product=product_id, warehouse=warehouse, movement_type="stock_in",
                             qty=qty, unit_cost=5, client_uuid="lab-sc-in-" + product_id)
    frappe.db.commit()

    # 快照：从当前结存生成
    ok, created = as_user(STOCK, counts.create_count, title="Lab count 1")
    results["create_ok"] = ok
    results["create_rows_positive"] = bool(ok and (created or {}).get("rows", 0) >= 3)
    task = (created or {}).get("name")
    ok, items = as_user(STOCK, counts.list_items, count=task, limit=0)
    results["items_ok"] = ok
    books = {row["product"]: row["book_qty"] for row in (items or [])}
    results["book_matches_balance"] = (books.get(watched[0]) == 10.0
                                       and books.get(watched[1]) == 3.0)
    results["items_are_uncounted"] = all(not row["counted"] for row in (items or []))
    item_a = next((row["name"] for row in (items or []) if row["product"] == watched[0]), None)
    item_b = next((row["name"] for row in (items or []) if row["product"] == watched[1]), None)

    # 追加式记录：两笔相加；幂等锚重放不记第二笔
    ok, first = as_user(STOCK, counts.add_entry, count=task, item=item_a, qty=4,
                        location="A1", request_id="lab-sc-e1", source="LINE")
    results["entry_ok"] = ok
    ok, replay = as_user(STOCK, counts.add_entry, count=task, item=item_a, qty=4,
                         request_id="lab-sc-e1")
    results["entry_deduped"] = bool(ok and replay.get("deduped"))
    ok, second = as_user(STOCK, counts.add_entry, count=task, item=item_a, qty=6,
                         request_id="lab-sc-e2", source="LINE")
    results["entry_second_ok"] = ok
    row_a = frappe.db.get_value("ERP Stock Count Item", item_a,
                                ["counted_qty", "difference"], as_dict=True)
    results["counted_sum"] = float(row_a["counted_qty"] or 0)
    results["difference_after_sum"] = float(row_a["difference"] or 0)

    # 扫到快照里没有的货：就地补一行，账面取当前结存
    ok, extra = as_user(STOCK, counts.add_entry, count=task, code="LAB-SC-C", qty=5,
                        request_id="lab-sc-e3", source="LINE")
    results["scan_unknown_ok"] = ok
    added = frappe.db.get_value("ERP Stock Count Item", (extra or {}).get("item"),
                                ["book_qty", "counted_qty"], as_dict=True) if ok else None
    results["scan_unknown_added_row"] = bool(added and float(added["book_qty"]) == 7.0)
    results["scan_unknown_counted"] = float((added or {}).get("counted_qty") or 0)

    # 改一笔 / 撤销一笔 / 版本过期要拦
    entry_second = (second or {}).get("entry")
    version = frappe.db.get_value("ERP Stock Count Entry", entry_second, "version")
    ok, edited = as_user(STOCK, counts.edit_entry, entry=entry_second, qty=9, version=version)
    results["edit_ok"] = ok
    results["counted_after_edit"] = float(
        frappe.db.get_value("ERP Stock Count Item", item_a, "counted_qty") or 0)
    ok, stale = as_user(STOCK, counts.edit_entry, entry=entry_second, qty=1, version=version)
    results["stale_version_allowed"] = ok
    ok, voided = as_user(STOCK, counts.edit_entry, entry=entry_second, voided=1)
    results["void_ok"] = ok
    results["counted_after_void"] = float(
        frappe.db.get_value("ERP Stock Count Item", item_a, "counted_qty") or 0)

    # 结束盘点：锁记录；没数过的行仍为空
    ok, closed = as_user(STOCK, counts.close_count, task)
    results["close_ok"] = ok
    results["status_after_close"] = frappe.db.get_value("ERP Stock Count", task, "status")
    ok, refused = as_user(STOCK, counts.add_entry, count=task, item=item_b, qty=1)
    results["entry_after_close_allowed"] = ok
    row_b = frappe.db.get_value("ERP Stock Count Item", item_b, ["counted", "counted_qty"],
                                as_dict=True)
    results["uncounted_stays_empty"] = bool(row_b and not row_b["counted"])

    # 过账：结存收敛到实盘；没数过的行不碰；重复过账要拦
    ok, posted = as_user(OWNER, counts.post_count, task)
    results["post_ok"] = ok
    results["status_after_post"] = frappe.db.get_value("ERP Stock Count", task, "status")
    results["balance_after_post"] = float(
        frappe.db.get_value("ERP Stock Balance", "BAL-" + watched[0] + "-" + warehouse,
                            "qty_on_hand") or 0)
    results["uncounted_balance_untouched"] = float(
        frappe.db.get_value("ERP Stock Balance", "BAL-" + watched[1] + "-" + warehouse,
                            "qty_on_hand") or 0)
    ok, again = as_user(OWNER, counts.post_count, task)
    results["post_twice_allowed"] = ok

    # 岗位：商品岗位不能开盘点；无关账号看不到；页面只对库存岗位出现
    ok, err = as_user(SHOP, counts.create_count, title="Lab count by shop")
    results["shop_can_create"] = ok
    ok, err = as_user(OUTSIDER, counts.list_counts)
    results["outsider_can_list"] = ok
    from pearnly_core.desk import navigation
    frappe.set_user(STOCK)
    results["stock_sees_count_page"] = "pearnly-stock-count" in navigation.visible_pages("erp")
    frappe.set_user(SHOP)
    results["shop_sees_count_page"] = "pearnly-stock-count" in navigation.visible_pages("erp")

    # 导出：两张表（汇总 + 明细），真 xlsx
    ok, _ = as_user(STOCK, inventory_api.api_stock_count_export, name=task)
    payload = frappe.local.response.get("filecontent") or b""
    results["export_ok"] = bool(ok and payload[:2] == b"PK")
    results["export_bytes"] = len(payload)

    frappe.set_user("Administrator")
    results["site_a_counts"] = frappe.db.count("ERP Stock Count")
    frappe.destroy()
    connect(OTHER_SITE)
    results["other_site_counts"] = frappe.db.count("ERP Stock Count")

    expected = {
        "create_ok": True,
        "create_rows_positive": True,
        "items_ok": True,
        "book_matches_balance": True,
        "items_are_uncounted": True,
        "entry_ok": True,
        "entry_deduped": True,
        "entry_second_ok": True,
        "counted_sum": 10.0,
        "difference_after_sum": 0.0,
        "scan_unknown_ok": True,
        "scan_unknown_added_row": True,
        "scan_unknown_counted": 5.0,
        "edit_ok": True,
        "counted_after_edit": 13.0,
        "stale_version_allowed": False,
        "void_ok": True,
        "counted_after_void": 4.0,
        "close_ok": True,
        "status_after_close": "Closed",
        "entry_after_close_allowed": False,
        "uncounted_stays_empty": True,
        "post_ok": True,
        "status_after_post": "Posted",
        "balance_after_post": 4.0,
        "uncounted_balance_untouched": 3.0,
        "post_twice_allowed": False,
        "shop_can_create": False,
        "outsider_can_list": False,
        "stock_sees_count_page": True,
        "shop_sees_count_page": False,
        "export_ok": True,
        "other_site_counts": 0,
    }
    problems = {k: {"expected": v, "actual": results.get(k)} for k, v in expected.items()
                if results.get(k) != v}
    if not results.get("export_bytes"):
        problems["export_bytes"] = results.get("export_bytes")
    print(json.dumps({"site": site, "results": results, "problems": problems},
                     ensure_ascii=False, indent=2, default=str))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
