"""Verify inventory on the dedicated laboratory.

Real Site path with synthetic data: warehouse default, stock in/out entries
(draft -> submit -> void), direct receive/issue/adjust/count, rolling weighted cost
including the "cost unknown stays empty" branch, opening stock and the stock card,
purchase confirmation posting into stock, per-job access and tenant isolation.
"""
import json
import os
import re
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
    from pearnly_erp_portal.inventory import api as inventory_api
    from pearnly_erp_portal.inventory import documents, openings, report, stock, warehouses
    from pearnly_erp_portal.purchasing import service as purchasing

    if CONTROL_APP not in sys.path:
        sys.path.insert(0, CONTROL_APP)
    from pearnly_control import features as control_features

    spec = control_features.roles_payload("erp", control_features.codes_of("erp"))
    ensure_feature_roles(json.dumps(spec), admin_user=OWNER)
    ensure_member(STOCK, ["ERP Stock"])
    ensure_member(SHOP, ["ERP Products"])
    ensure_member(OUTSIDER, [])

    results = {}
    results["stock_doctypes"] = sorted(
        do for do in ("ERP Warehouse", "ERP Stock Entry", "ERP Stock Entry Line",
                      "ERP Stock Movement", "ERP Stock Balance", "ERP Stock Opening")
        if frappe.db.exists("DocType", do))

    # 仓库：没有仓库时自动建默认仓（否则入出库无处可落）。
    warehouse = warehouses.ensure_default()
    results["default_warehouse"] = bool(warehouse)

    # 干净起点：本脚本的商品、流水、结存、期初。
    watched = [product("LAB-INV-A", "ผงซักฟอก A"), product("LAB-INV-B", "ผงซักฟอก B")]
    fixtures = watched + [product("LAB-INV-BASE", "Lab stock unit"),
                          product("LAB-INV-C", "Lab low stock"),
                          product("LAB-INV-D", "Lab out of stock")]
    for do in ("ERP Stock Entry", "ERP Stock Movement", "ERP Stock Balance", "ERP Stock Opening"):
        for name in frappe.get_all(do, filters={"product": ["in", fixtures]} if do != "ERP Stock Entry"
                                   else {"doc_no": ["like", "LAB-INV-%"]}, pluck="name"):
            frappe.delete_doc(do, name, force=True, ignore_permissions=True)
    frappe.db.commit()
    # 删流水后结存会留在旧值，因此按剩余流水重算，保证脚本可重复执行。
    for product_id in fixtures:
        stock.rebuild_balance(product_id, warehouse)

    # 期初：10 件 × 10 元 → 结存 10 / 均价 10。
    ok, opening = as_user(OWNER, openings.upsert_opening, product=watched[0],
                          warehouse=warehouse, as_of_date="2026-09-01", qty=10, unit_cost=10)
    results["upsert_opening"] = ok

    # 入库 5 件 × 20 元 → 15 件、价值 200、均价 13.33。
    ok, received = as_user(STOCK, stock.apply_movement, product=watched[0], warehouse=warehouse,
                           movement_type="stock_in", qty=5, unit_cost=20,
                           posting_date="2026-09-05", ref_type="manual", client_uuid="lab-inv-in-1")
    results["receive"] = received
    # 同一幂等锚重复提交：不再记一次。
    ok, again = as_user(STOCK, stock.apply_movement, product=watched[0], warehouse=warehouse,
                        movement_type="stock_in", qty=5, unit_cost=20,
                        posting_date="2026-09-05", ref_type="manual", client_uuid="lab-inv-in-1")
    results["receive_deduped"] = again.get("deduped") if isinstance(again, dict) else again

    # 出库 3 件 → 12 件，金额按均价走、均价不变。
    ok, issued = as_user(STOCK, stock.apply_movement, product=watched[0], warehouse=warehouse,
                         movement_type="stock_out", qty=3, posting_date="2026-09-06",
                         ref_type="manual", client_uuid="lab-inv-out-1")
    results["issue"] = issued

    # 第二个商品：先出库（成本未确立）→ 数量为负、金额与单价留空，不写成 0。
    ok, blind = as_user(STOCK, stock.apply_movement, product=watched[1], warehouse=warehouse,
                        movement_type="stock_out", qty=2, posting_date="2026-09-06",
                        ref_type="manual", client_uuid="lab-inv-out-2")
    results["unknown_cost_issue"] = blind
    balance_row = frappe.db.get_value("ERP Stock Balance", f"BAL-{watched[1]}-{warehouse}",
                                      ["qty_on_hand", "cost_known", "unit_cost"], as_dict=True)
    results["unknown_cost_balance"] = balance_row

    # 单位换算：商品有"โหล = 12 件"时按打入库应落 24 件。
    from pearnly_erp_portal.products import service as products_service
    as_user(OWNER, products_service.add_unit, watched[0], unit_name="โหล",
            factor_to_base=12, is_default_sell=False)
    ok, dozen = as_user(STOCK, stock.apply_movement, product=watched[0], warehouse=warehouse,
                        movement_type="stock_in", qty=2, unit="โหล", unit_cost=1,
                        posting_date="2026-09-07", client_uuid="lab-inv-in-2")
    results["dozen_in"] = dozen
    ok, bad_unit = as_user(STOCK, stock.apply_movement, product=watched[0], warehouse=warehouse,
                           movement_type="stock_in", qty=1, unit="ลัง",
                           posting_date="2026-09-07", client_uuid="lab-inv-in-3")
    results["unknown_unit_allowed"] = ok

    # 盘点：系统 39 → 实盘 30 应落一笔 -9 的差异流水。
    ok, counted = as_user(STOCK, stock.count_lines, warehouse=warehouse,
                          lines=[{"product": watched[0], "counted_qty": 30}],
                          posting_date="2026-09-08")
    results["count"] = counted

    # 出入库单据：草稿 → 提交（落流水）→ 作废（反向冲销，流水不删）。
    for key in ("LAB-INV-ENTRY-1",):
        old = frappe.db.get_value("ERP Stock Entry", {"dedupe_key": key}, "name")
        if old:
            frappe.delete_doc("ERP Stock Entry", old, force=True, ignore_permissions=True)
    frappe.db.commit()
    before_movements = frappe.db.count("ERP Stock Movement")
    ok, entry = as_user(STOCK, documents.create_entry, direction="in",
                        posting_date="2026-09-09", warehouse=warehouse,
                        lines=[{"product": watched[1], "qty": 4, "unit_cost": 7}],
                        doc_no="LAB-INV-ENTRY-1", dedupe_key="LAB-INV-ENTRY-1")
    results["create_entry"] = ok
    results["entry_qty_before_submit"] = float(frappe.db.get_value(
        "ERP Stock Balance", f"BAL-{watched[1]}-{warehouse}", "qty_on_hand") or 0)
    ok, submitted = as_user(STOCK, documents.submit_entry, entry["name"])
    results["submit_entry"] = submitted
    results["entry_qty_after_submit"] = frappe.db.get_value(
        "ERP Stock Balance", f"BAL-{watched[1]}-{warehouse}", "qty_on_hand")
    ok, err = as_user(STOCK, documents.update_entry, entry["name"], doc_no="LAB-INV-X")
    results["edit_after_submit_allowed"] = ok
    ok, voided = as_user(STOCK, documents.void_entry, entry["name"])
    results["void_entry"] = voided
    results["entry_qty_after_void"] = frappe.db.get_value(
        "ERP Stock Balance", f"BAL-{watched[1]}-{warehouse}", "qty_on_hand")
    results["movements_not_deleted_on_void"] = frappe.db.count("ERP Stock Movement") >= before_movements

    # 收发存：期初 10、期间入 39、出 3、盘 1 次、期末对得上。
    ok, summary = as_user(STOCK, report.summary, date_from="2026-09-01", date_to="2026-09-30")
    results["summary_rows"] = len(summary) if ok else summary
    row = next((r for r in summary if r["product"] == watched[0]), None) if ok else None
    results["summary_product"] = row
    ok, card = as_user(STOCK, report.card, product=watched[0], warehouse=warehouse,
                       date_from="2026-09-01", date_to="2026-09-30")
    results["card_opening_qty"] = card["opening"]["qty"] if ok else card
    results["card_closing_qty"] = card["closing"]["qty"] if ok else None
    results["card_rows"] = len(card["rows"]) if ok else None

    # 结存能由流水重建（对账口径，不是另算一套）。
    before_rebuild = frappe.db.get_value("ERP Stock Balance", f"BAL-{watched[0]}-{warehouse}",
                                         "qty_on_hand")
    ok, rebuilt = as_user(STOCK, stock.rebuild_balance, watched[0], warehouse)
    results["rebuild_matches"] = (
        float(rebuilt["qty_on_hand"]) == float(before_rebuild)) if ok else rebuilt

    # 采购确认 → 自动入库（受采购设置开关控制）。
    from pearnly_erp_portal.purchasing import settings as purchase_settings
    as_user(OWNER, purchase_settings.update_settings, auto_stock_in=0)
    for key in ("LAB-INV-PUR-1", "LAB-INV-PUR-2"):
        old = frappe.db.get_value("Purchase Document", {"dedupe_key": key}, "name")
        if old:
            frappe.delete_doc("Purchase Document", old, force=True, ignore_permissions=True)
    frappe.db.commit()

    def make_purchase(key, qty):
        return as_user(OWNER, purchasing.create_purchase, supplier_name="Lab Supplier A",
                       doc_date="2026-09-10", doc_no=key,
                       lines=[{"description": "Goods", "product": watched[1],
                               "qty": qty, "unit_price": 7}],
                       dedupe_key=key, doc_kind="goods")

    ok, purchase = make_purchase("LAB-INV-PUR-1", 6)
    qty_before_purchase = float(frappe.db.get_value(
        "ERP Stock Balance", f"BAL-{watched[1]}-{warehouse}", "qty_on_hand") or 0)
    as_user(OWNER, purchasing.set_status, purchase["name"], "submitted")
    results["stock_in_off_keeps_qty"] = (
        float(frappe.db.get_value("ERP Stock Balance", f"BAL-{watched[1]}-{warehouse}",
                                  "qty_on_hand") or 0) == qty_before_purchase)

    as_user(OWNER, purchase_settings.update_settings, auto_stock_in=1)
    ok, purchase2 = make_purchase("LAB-INV-PUR-2", 5)
    as_user(OWNER, purchasing.set_status, purchase2["name"], "submitted")
    results["stock_in_on_adds_qty"] = (
        float(frappe.db.get_value("ERP Stock Balance", f"BAL-{watched[1]}-{warehouse}",
                                  "qty_on_hand") or 0) == qty_before_purchase + 5)
    results["purchase_posted_at"] = bool(frappe.db.get_value(
        "Purchase Document", purchase2["name"], "stock_posted_at"))

    # 库存单位才是记数单位：售卖单位与它不同时，数量按换算进库存单位，页面也显示库存单位。
    base_item = product("LAB-INV-BASE", "Lab stock unit")
    as_user(OWNER, products_service.update_product, base_item, unit="dozen", base_unit="pcs")
    as_user(OWNER, products_service.add_unit, base_item, unit_name="dozen", factor_to_base=12)
    ok, converted = as_user(STOCK, stock.apply_movement, product=base_item, warehouse=warehouse,
                            movement_type="stock_in", qty=3, unit="dozen", unit_cost=2,
                            client_uuid="lab-inv-in-base")
    results["stock_unit_movement"] = converted
    ok, overview = as_user(STOCK, stock.stock_overview, warehouse=warehouse)
    base_row = next((r for r in overview if r["product"] == base_item), None) if ok else None
    results["stock_unit_row"] = base_row
    results["stock_unit_is_stock_unit"] = bool(base_row and base_row["unit"] == "pcs")
    results["stock_unit_qty"] = None if base_row is None else base_row["qty_on_hand"]
    results["stock_unit_has_code"] = bool(base_row and base_row["code"] == "LAB-INV-BASE")
    results["stock_unit_warehouse_name"] = None if base_row is None else base_row["warehouse_name"]

    # 低库存/缺货两档各造一行：一个低于低库存阈值，一个刚好清零（结存行还在）。
    low_item = product("LAB-INV-C", "Lab low stock")
    as_user(OWNER, products_service.update_product, low_item, min_stock=5)
    as_user(STOCK, stock.apply_movement, product=low_item, warehouse=warehouse,
            movement_type="stock_in", qty=3, unit_cost=1, client_uuid="lab-inv-low-1")
    out_item = product("LAB-INV-D", "Lab out of stock")
    as_user(STOCK, stock.apply_movement, product=out_item, warehouse=warehouse,
            movement_type="stock_in", qty=2, unit_cost=1, client_uuid="lab-inv-out-a")
    as_user(STOCK, stock.apply_movement, product=out_item, warehouse=warehouse,
            movement_type="stock_out", qty=2, client_uuid="lab-inv-out-b")

    # 只看低库存/缺货：正常行被筛掉，这两行留着。
    ok, low_rows = as_user(STOCK, stock.stock_overview, warehouse=warehouse, only_low=True)
    results["only_low_ok"] = ok
    results["only_low_has_low"] = bool(ok and any(r["product"] == low_item for r in low_rows))
    results["only_low_has_out"] = bool(ok and any(r["product"] == out_item for r in low_rows))
    results["only_low_drops_ok"] = bool(ok and not any(r["status"] == "ok" for r in low_rows))

    # 页脚计数与四个合计：合计始终按全量算（"缺货"不能被列表开关吃掉）。
    ok, counted_rows = as_user(STOCK, stock.stock_count, warehouse=warehouse, include_zero=True)
    results["stock_count_ok"] = ok
    results["stock_count_positive"] = bool(ok and counted_rows and counted_rows > 0)
    ok, totals = as_user(STOCK, stock.stock_totals)
    results["totals_ok"] = ok
    results["totals_out_positive"] = bool(ok and totals["out_count"] and totals["out_count"] > 0)
    results["totals_value_known"] = bool(ok and totals["value"] is not None and totals["value"] > 0)
    results["totals_low_positive"] = bool(ok and totals["low_count"] and totals["low_count"] > 0)
    results["totals_sku_positive"] = bool(ok and totals["sku_count"] > 0)

    # 导出 CSV：与列表同一套筛选，导全部命中行（表头走语言包）。
    ok, _ = as_user(STOCK, inventory_api.api_stock_export)
    csv_bytes = frappe.local.response.get("filecontent") or b""
    csv_lines = csv_bytes.decode("utf-8-sig").strip().splitlines()
    results["export_ok"] = bool(ok and csv_lines)
    results["export_header"] = csv_lines[0].split(",")[0] if csv_lines else None
    results["export_has_row"] = any("Lab stock unit" in line for line in csv_lines)
    # 数量与金额按**实际值**写：不留固定的尾随 0（8.000 → 8）—— 用户 2026-09-22 定。
    base_row = next((line for line in csv_lines if "Lab stock unit" in line), "")
    numbers = base_row.split(",")[5:8] if base_row else []
    # 只允许"整数"或"小数点后最后一位不是 0"
    results["export_numbers_trimmed"] = len(numbers) == 3 and all(
        re.fullmatch(r"-?\d+(\.\d*[1-9])?", number or "") for number in numbers)

    # 岗位：库存岗位能看能改；商品岗位看得到库存但改不了；无岗位看不到。
    stock_ok, stock_view = as_user(STOCK, stock.stock_overview, include_zero=True)
    results["stock_can_view"] = stock_ok
    shop_ok, shop_view = as_user(SHOP, stock.stock_overview, include_zero=True)
    results["shop_can_view"] = shop_ok
    results["shop_view_error"] = None if shop_ok else shop_view
    stock_row = next((r for r in stock_view if r["product"] == watched[0]), None) if stock_ok else None
    results["stock_sees_cost"] = None if stock_row is None else stock_row["unit_cost"]
    ok, err = as_user(SHOP, stock.apply_movement, product=watched[0], warehouse=warehouse,
                      movement_type="adjust", qty=1)
    results["shop_can_write_stock"] = ok
    ok, err = as_user(OUTSIDER, stock.stock_overview)
    results["outsider_can_view"] = ok

    # 侧栏也是同一道闸：库存页只对库存岗位出现（页面级判权用结存表的读权限）。
    from pearnly_core.desk import navigation
    frappe.set_user(STOCK)
    results["stock_sees_inventory_page"] = "pearnly-inventory" in navigation.visible_pages("erp")
    frappe.set_user(SHOP)
    results["shop_sees_inventory_page"] = "pearnly-inventory" in navigation.visible_pages("erp")

    # 收尾把自动入库开关放回出厂默认，别把状态留给别的探针。
    as_user(OWNER, purchase_settings.update_settings, auto_stock_in=0)
    frappe.set_user("Administrator")
    results["site_a_movements"] = frappe.db.count("ERP Stock Movement")
    results["site_a_balances"] = frappe.db.count("ERP Stock Balance")

    frappe.destroy()
    connect(OTHER_SITE)
    results["other_site_movements"] = frappe.db.count("ERP Stock Movement")
    results["other_site_balances"] = frappe.db.count("ERP Stock Balance")

    expected = {
        "upsert_opening": True,
        "stock_unit_is_stock_unit": True,
        "stock_unit_qty": 36.0,
        "stock_unit_has_code": True,
        "stock_unit_warehouse_name": "Main",
        "only_low_ok": True,
        "only_low_has_low": True,
        "only_low_has_out": True,
        "only_low_drops_ok": True,
        "stock_count_ok": True,
        "stock_count_positive": True,
        "totals_ok": True,
        "totals_out_positive": True,
        "totals_low_positive": True,
        "totals_value_known": True,
        "totals_sku_positive": True,
        "export_ok": True,
        "export_has_row": True,
        "export_numbers_trimmed": True,
        "receive_deduped": True,
        "unknown_unit_allowed": False,
        "edit_after_submit_allowed": False,
        "create_entry": True,
        "stock_can_view": True,
        # 商品岗位管商品主数据；在库一览属于库存岗位（旧线上同样分开）。
        "shop_can_view": False,
        "shop_can_write_stock": False,
        "outsider_can_view": False,
        "stock_sees_inventory_page": True,
        "shop_sees_inventory_page": False,
        "stock_in_off_keeps_qty": True,
        "stock_in_on_adds_qty": True,
        "purchase_posted_at": True,
        "movements_not_deleted_on_void": True,
        "other_site_movements": 0,
        "other_site_balances": 0,
    }
    problems = {k: {"expected": v, "actual": results.get(k)} for k, v in expected.items()
                if results.get(k) != v}

    # 期末数量：期初 10 + 入 5 + 入 24 = 39，出 3 → 36，盘点设成 30 → 30。
    if results.get("card_opening_qty") != 0.0:
        problems["card_opening_qty"] = results.get("card_opening_qty")
    if results.get("card_closing_qty") != 30.0:
        problems["card_closing_qty"] = results.get("card_closing_qty")
    if results.get("rebuild_matches") is not True:
        problems["rebuild_matches"] = results.get("rebuild_matches")
    unknown = results.get("unknown_cost_balance") or {}
    if unknown.get("cost_known"):
        problems["unknown_cost_should_stay_unknown"] = unknown
    if float(unknown.get("qty_on_hand") or 0) != -2.0:
        problems["unknown_cost_qty"] = unknown
    dozen = results.get("dozen_in")
    # 打货入库时点：期初 10 + 入 5 − 出 3 = 12，再 +24 = 36（盘点在更后面）。
    if not isinstance(dozen, dict) or float(dozen.get("qty_on_hand", 0)) != 36.0:
        problems["dozen_conversion"] = dozen
    # 出入库单据作用在 watched[1] 上：提交 +4、作废回到提交前，用相对量判断。
    if results.get("entry_qty_after_submit") is not None and \
            float(results["entry_qty_after_submit"]) != results.get("entry_qty_before_submit", 0) + 4:
        problems["entry_submit_qty"] = results["entry_qty_after_submit"]
    if results.get("entry_qty_after_void") is not None and \
            float(results["entry_qty_after_void"]) != results.get("entry_qty_before_submit"):
        problems["entry_void_qty"] = results["entry_qty_after_void"]
    if results.get("stock_sees_cost") in (None, 0.0):
        problems["stock_should_see_cost"] = results.get("stock_sees_cost")
    if results.get("stock_doctypes") != sorted(
            ["ERP Warehouse", "ERP Stock Entry", "ERP Stock Entry Line",
             "ERP Stock Movement", "ERP Stock Balance", "ERP Stock Opening"]):
        problems["stock_doctypes"] = results.get("stock_doctypes")

    print(json.dumps({"site": site, "results": results, "problems": problems},
                     ensure_ascii=False, indent=2, default=str))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
