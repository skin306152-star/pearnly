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


REPORT = "ERP Stock Count Variance"


def _upload(data: bytes, name: str) -> str:
    """把生成好的表落成一条 `File`，拿回 file_url —— 与页面上传那条路同一个入口。"""
    from frappe.utils.file_manager import save_file

    return save_file(name, data, None, None, is_private=1, df="File").file_url


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
    # 本仓不再有"默认仓"这个概念（2026-09-24 删掉）：实验站上**显式**取/建一个属于本脚本的仓。
    warehouse = warehouses.resolve("LAB-POS", create=True)
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

    # ── 手机端（LINE 里那一页）：一次性票据，权限与桌面端同一条路 ────────────
    # 票据**不放宽任何权限**：它只是把请求认成票据上那个人，之后照走岗位与盘点口径。
    from pearnly_erp_portal.inventory import line_mobile

    def bind(user, line_user_id):
        existing = frappe.db.get_value("Pearnly LINE Binding", {"user": user}, "name")
        if existing:
            doc = frappe.get_doc("Pearnly LINE Binding", existing)
            doc.line_user_id, doc.status = line_user_id, "active"
            doc.save(ignore_permissions=True)
        else:
            frappe.get_doc({"doctype": "Pearnly LINE Binding", "user": user,
                            "line_user_id": line_user_id, "status": "active"}
                           ).insert(ignore_permissions=True)
        frappe.db.commit()

    bind(STOCK, "Ulab-sc-stock")
    frappe.set_user(STOCK)
    ok, auth = as_user(STOCK, line_mobile.api_line_stocktake_auth)
    results["mobile_auth_ok"] = ok
    ticket = (auth or {}).get("ticket") or ""
    results["mobile_ticket_user"] = (auth or {}).get("user")

    ok, tasks = as_user(STOCK, line_mobile.api_line_stocktake_tasks, ticket=ticket)
    results["mobile_lists_task"] = bool(ok and any(t["name"] == task for t in tasks or []))
    entries_before = frappe.db.count("ERP Stock Count Entry", {"count": task})

    ok, found = as_user(STOCK, line_mobile.api_line_stocktake_lookup, ticket=ticket,
                        name=task, code="LAB-SC-A")
    results["mobile_lookup_ok"] = ok
    results["mobile_lookup_error"] = "" if ok else str(found)
    results["mobile_lookup_item"] = bool(
        ok and ((found or {}).get("item") or {}).get("product") == watched[0])
    # 扫一下只是"看看是哪件、账面多少"：不该在那一下写进一条实盘
    results["mobile_lookup_writes_nothing"] = (
        frappe.db.count("ERP Stock Count Entry", {"count": task}) == entries_before)
    results["mobile_lookup_book"] = float(((found or {}).get("item") or {}).get("book_qty") or 0)
    ok, missing = as_user(STOCK, line_mobile.api_line_stocktake_lookup, ticket=ticket,
                          name=task, code="LAB-SC-NOPE")
    results["mobile_lookup_unknown_empty"] = bool(ok and not missing)

    ok, mobile_entry = as_user(STOCK, line_mobile.api_line_stocktake_add, ticket=ticket,
                               name=task, item=item_b, qty=2, request_id="lab-sc-m1")
    results["mobile_add_ok"] = ok
    results["mobile_add_error"] = "" if ok else str(mobile_entry)
    mobile_name = (mobile_entry or {}).get("entry") if isinstance(mobile_entry, dict) else None
    entry_row = frappe.db.get_value("ERP Stock Count Entry", mobile_name,
                                    ["source", "counted_by", "qty"], as_dict=True)
    results["mobile_entry_source"] = (entry_row or {}).get("source")
    results["mobile_entry_by"] = (entry_row or {}).get("counted_by")
    ok, mobile_replay = as_user(STOCK, line_mobile.api_line_stocktake_add, ticket=ticket,
                                name=task, item=item_b, qty=2, request_id="lab-sc-m1")
    results["mobile_replay_deduped"] = bool(
        ok and isinstance(mobile_replay, dict) and mobile_replay.get("deduped"))
    results["mobile_counted"] = float(
        frappe.db.get_value("ERP Stock Count Item", item_b, "counted_qty") or 0)
    ok, undone = as_user(STOCK, line_mobile.api_line_stocktake_undo, ticket=ticket,
                         entry=mobile_name)
    results["mobile_undo_ok"] = ok
    results["mobile_counted_after_undo"] = float(
        frappe.db.get_value("ERP Stock Count Item", item_b, "counted_qty") or 0)
    results["mobile_untouched_row_after_undo"] = bool(
        not frappe.db.get_value("ERP Stock Count Item", item_b, "counted"))

    # 没有票据 / 假票据：一律拒
    ok, _ = as_user(STOCK, line_mobile.api_line_stocktake_tasks)
    results["mobile_without_ticket_allowed"] = ok
    ok, _ = as_user(STOCK, line_mobile.api_line_stocktake_tasks, ticket="not-a-ticket")
    results["mobile_bad_ticket_allowed"] = ok

    # 解绑之后这张票据立刻失效（票据不缓存权限）
    binding = frappe.get_doc("Pearnly LINE Binding", {"user": STOCK})
    binding.status = "unbound"
    binding.save(ignore_permissions=True)
    frappe.db.commit()
    ok, _ = as_user(STOCK, line_mobile.api_line_stocktake_tasks, ticket=ticket)
    results["mobile_after_unbind_allowed"] = ok
    bind(STOCK, "Ulab-sc-stock")
    ok, _ = as_user(STOCK, line_mobile.api_line_stocktake_tasks, ticket=ticket)
    results["mobile_after_rebind_allowed"] = ok

    # 有票据但**没有库存岗位**的人：照样走不动（票据不是权限）
    bind(SHOP, "Ulab-sc-shop")
    frappe.set_user(SHOP)
    ok, shop_auth = as_user(SHOP, line_mobile.api_line_stocktake_auth)
    shop_ticket = (shop_auth or {}).get("ticket") or ""
    results["mobile_shop_auth_ok"] = ok
    ok, _ = as_user(SHOP, line_mobile.api_line_stocktake_add, ticket=shop_ticket,
                    name=task, item=item_b, qty=1, request_id="lab-sc-m2")
    results["mobile_shop_add_allowed"] = ok
    ok, _ = as_user(SHOP, line_mobile.api_line_stocktake_tasks, ticket=shop_ticket)
    results["mobile_shop_list_allowed"] = ok

    # ── 上传导入：模版 → 填数 → 传回 ────────────────────────────────────────
    # 单开一份任务，免得把上面那套"两笔相加/撤销"的数搅乱。
    from pearnly_erp_portal.inventory import workbook as count_workbook
    ok, second_task = as_user(STOCK, counts.create_count, title="Lab count import",
                              snapshot=False)
    results["import_task_ok"] = ok
    import_task = (second_task or {}).get("name")
    results["import_task_rows"] = (second_task or {}).get("rows")

    fields = [field for field, _key in count_workbook.FIELD_LABELS]
    sheet = count_workbook.build_bytes(fields, [
        {"product_code": "LAB-SC-A", "product_name": "Lab count A", "barcode": "",
         "unit": "pcs", "book_qty": 10, "counted_qty": 8, "warehouse": "", "location": "A1"},
        {"product_code": "LAB-SC-A", "product_name": "Lab count A", "barcode": "",
         "unit": "pcs", "book_qty": 2, "counted_qty": 2, "warehouse": "", "location": "B2"},
        {"product_code": "LAB-SC-B", "product_name": "Lab count B", "barcode": "",
         "unit": "pcs", "book_qty": None, "counted_qty": None, "warehouse": "", "location": ""},
    ])
    file_url = _upload(sheet, "lab-stock-count.xlsx")
    ok, imported = as_user(STOCK, inventory_api.api_stock_count_import,
                           name=import_task, file_url=file_url)
    results["import_ok"] = ok
    results["import_rows"] = (imported or {}).get("rows")
    results["import_counted"] = (imported or {}).get("counted")
    results["import_problems"] = len((imported or {}).get("problems") or [])
    # 同一个商品在两个货架上 = 两行，各有各的账面与差异
    rows_a = counts.rows_of_product(count=import_task, product=watched[0])
    results["import_rows_per_place"] = len(rows_a)
    results["import_places"] = [(row["location"], float(row["book_qty"]))
                                for row in sorted(rows_a, key=lambda r: r["location"])]
    row_a2 = counts.item_of_product(count=import_task, product=watched[0], location="A1") or {}
    results["import_book_from_sheet"] = float(row_a2.get("book_qty") or 0)
    results["import_counted_qty"] = float(row_a2.get("counted_qty") or 0)
    results["import_entry_source"] = frappe.db.get_value(
        "ERP Stock Count Entry", {"count": import_task, "item": row_a2.get("name")}, "source")
    # 记录上的库位是**那一行的**库位（行是仓位，不是记录上的自由文本）
    results["import_entry_place"] = frappe.db.get_value(
        "ERP Stock Count Entry", {"count": import_task, "item": row_a2.get("name")}, "location")

    # 同一份表再传一次是**覆盖**，不是又记一笔；改成 9 也一样只有一条
    ok, again_import = as_user(STOCK, inventory_api.api_stock_count_import,
                               name=import_task, file_url=file_url)
    results["import_again_rows"] = (again_import or {}).get("rows")
    results["import_entries_total"] = frappe.db.count(
        "ERP Stock Count Entry", {"count": import_task, "voided": 0})
    changed = count_workbook.build_bytes(fields, [
        {"product_code": "LAB-SC-A", "product_name": "Lab count A", "barcode": "",
         "unit": "pcs", "book_qty": 10, "counted_qty": 9, "warehouse": "", "location": "A1"},
    ])
    ok, _ = as_user(STOCK, inventory_api.api_stock_count_import,
                    name=import_task, file_url=_upload(changed, "lab-stock-count-2.xlsx"))
    results["import_after_change"] = float(
        frappe.db.get_value("ERP Stock Count Item", row_a2.get("name"), "counted_qty") or 0)

    # 表里认不出的编号：只跳过那一行，别的照进
    mixed = count_workbook.build_bytes(fields, [
        {"product_code": "LAB-NOPE", "product_name": "", "barcode": "", "unit": "",
         "book_qty": None, "counted_qty": 1, "warehouse": "", "location": ""},
        {"product_code": "LAB-SC-B", "product_name": "Lab count B", "barcode": "",
         "unit": "pcs", "book_qty": None, "counted_qty": 2, "warehouse": "", "location": ""},
    ])
    ok, mixed_result = as_user(STOCK, inventory_api.api_stock_count_import,
                               name=import_task, file_url=_upload(mixed, "lab-mixed.xlsx"))
    results["import_mixed_rows"] = (mixed_result or {}).get("rows")
    results["import_mixed_problems"] = [p["reason"] for p in
                                        (mixed_result or {}).get("problems") or []]

    # 模版：真 xlsx，且表头能被自己认回来
    ok, _ = as_user(STOCK, inventory_api.api_stock_count_template, name=import_task)
    template = frappe.local.response.get("filecontent") or b""
    results["template_ok"] = bool(ok and template[:2] == b"PK")
    parsed, parse_problems = count_workbook.parse_workbook(
        template, count_workbook.header_aliases())
    results["template_round_trip"] = len(parsed)
    results["template_parse_problems"] = len(parse_problems)
    results["template_book_kept"] = float(
        next((row["book_qty"] for row in parsed if row["product_code"] == "LAB-SC-A"), 0) or 0)
    # 不带 name = 列表上那个"模版"按钮：按当前结存现拉一份
    ok, _ = as_user(STOCK, inventory_api.api_stock_count_template)
    blank = frappe.local.response.get("filecontent") or b""
    balances = frappe.db.sql("""select count(*) from `tabERP Stock Balance` b
        join `tabERP Product` p on p.name=b.product
        where b.warehouse=%s and p.is_active=1""", (warehouse,))[0][0]
    results["template_blank_ok"] = bool(ok and blank[:2] == b"PK")
    blank_rows = len(count_workbook.parse_workbook(blank, count_workbook.header_aliases())[0])
    results["template_blank_matches_balances"] = bool(blank and blank_rows == balances)

    # 差异看板 = **原生报表**：走框架自己的 run 入口（与页面上点开是同一条路）
    from frappe.desk import query_report

    ok, report = as_user(STOCK, query_report.run, report_name=REPORT,
                         filters={"count": task, "only": "differences"})
    results["report_ok"] = ok
    results["report_columns"] = len((report or {}).get("columns") or [])
    report_rows = (report or {}).get("result") or []
    results["report_rows"] = len(report_rows)
    results["report_first_place"] = report_rows[0].get("location") if report_rows else None
    results["report_first_difference"] = (
        float(report_rows[0].get("difference") or 0) if report_rows else None)
    results["report_has_value_column"] = any(
        column["fieldname"] == "difference_value"
        for column in (report or {}).get("columns") or [])
    ok, shop_report = as_user(SHOP, query_report.run, report_name=REPORT,
                              filters={"count": task})
    results["report_shop_allowed"] = ok
    results["report_shop_has_value_column"] = (
        any(column["fieldname"] == "difference_value"
            for column in (shop_report or {}).get("columns") or []) if ok else None)
    ok, all_rows = as_user(STOCK, query_report.run, report_name=REPORT,
                           filters={"count": task})
    results["report_all_rows_match"] = bool(
        ok and len((all_rows or {}).get("result") or [])
        == int(frappe.db.get_value("ERP Stock Count", task, "item_total") or 0))

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
    results["site_a_counts"] = frappe.db.count("ERP Stock Count", {"title": ["like", "Lab count %"]})
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
        "mobile_auth_ok": True,
        "mobile_ticket_user": STOCK,
        "mobile_lists_task": True,
        "mobile_lookup_ok": True,
        "mobile_lookup_item": True,
        "mobile_lookup_book": 10.0,
        "mobile_lookup_writes_nothing": True,
        "mobile_lookup_unknown_empty": True,
        "mobile_add_ok": True,
        "mobile_entry_source": "LINE",
        "mobile_entry_by": STOCK,
        "mobile_replay_deduped": True,
        "mobile_counted": 2.0,
        "mobile_undo_ok": True,
        "mobile_counted_after_undo": 0.0,
        "mobile_untouched_row_after_undo": True,
        "mobile_without_ticket_allowed": False,
        "mobile_bad_ticket_allowed": False,
        "mobile_after_unbind_allowed": False,
        "mobile_after_rebind_allowed": True,
        "mobile_shop_auth_ok": True,
        "mobile_shop_add_allowed": False,
        "mobile_shop_list_allowed": False,
        "import_task_ok": True,
        "import_task_rows": 0,
        "import_ok": True,
        "import_rows": 3,
        "import_counted": 2,
        "import_rows_per_place": 2,
        "import_places": [("A1", 10.0), ("B2", 2.0)],
        "import_entry_place": "A1",
        "import_problems": 0,
        "import_book_from_sheet": 10.0,
        "import_counted_qty": 8.0,
        "import_entry_source": "Import",
        "import_again_rows": 3,
        "import_entries_total": 2,
        "import_after_change": 9.0,
        "import_mixed_rows": 1,
        "import_mixed_problems": ["code_not_found"],
        "template_ok": True,
        "template_round_trip": 3,
        "template_parse_problems": 0,
        "template_book_kept": 10.0,
        "template_blank_ok": True,
        "template_blank_matches_balances": True,
        "report_ok": True,
        "report_columns": 13,
        "report_rows": 2,
        "report_first_place": "",
        "report_first_difference": -6.0,
        "report_has_value_column": True,
        "report_shop_allowed": False,
        "report_all_rows_match": True,
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
        "site_a_counts": 2,
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
