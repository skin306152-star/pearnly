"""两套固定数据（主会话 10-28：所有流程用例都跑这两套，不由窗口临时挑数据）。在回归实验室容器里跑，幂等：已经齐了就只回清单。

- new（照 polymer 新开户）：站点 `erp-new.regression.localhost`。一家公司，按正式方式接上 Express（CC 桌面版心跳报账套），
  7 个仓都由 Express 主档同步来（仓库码 01–07），零库存。商品各靠一种方式认出：
  编码、条码、二维码内容、内部编码、Express 同步来的存货码，外加一件按台管理的商品（车辆）。
- old（照 mrerp 有存量）：站点 `erp-old.regression.localhost`。两家公司（A 两个仓、带库位清单；B 一个仓），
  采购入库有存量，有采购单、销售单（含作废）、收银开班 / 卖 / 退 / 交班、进行中与已过账的盘点。

全部走正式接口（金额、单号、库存都由系统算）。岗位账号照 `lab_users.py` 的岗位，员工只开本套的公司（old 只开 A）。
最后一行打印本套的清单 JSON（公司、仓、各种认法对应的商品与要扫的码、单号），流程用例照它取，不自己挑数据。

Cowork 同样两套（第二个参数 `cowork`）：
- new：站点 `cowork-new.regression.localhost`，开户后什么都没建（只有开户时建的站点归属）。
- old：站点 `cowork-old.regression.localhost`，上传导入的两份盘点（带实盘的那份已结束）、银行与收入对账任务各一份、预设各一条。

DMS 同样两套（第二个参数 `dms`），外部 DMS 一律不连：DMS 里读到的主档 / 地址 / 客户由实验室直接发布成本地完整快照
（与线上夜间同步发布的是同一种数据），LINE 走站点的本地镜像（`pearnly_line_mirror`，站点没配 LINE 凭据时才生效，
「本该发出的消息」记进推送台账、不发出去）：
- new：站点 `dms-new.regression.localhost`。开户后接好一个 DMS 连接，一个销售操作员（`operator@dms.regression`），
  LINE 已绑定；DMS 主档最少一套（一个车型一个颜色），没有客户。
- old：站点 `dms-old.regression.localhost`。同样的连接与操作员；主档多几项（两个车型、两个订车地点），DMS 里已有一位客户。

    cd /home/frappe/frappe-bench && env/bin/python /workspace/scripts/regression/datasets.py new|old [erp|cowork|dms]
"""
import json
import os
import sys
import uuid

if os.environ.get("PEARNLY_REGRESSION_LAB") != "1":
    raise SystemExit("Requires the task-owned regression laboratory")

import frappe  # noqa: E402

SITES = {"new": "erp-new.regression.localhost", "old": "erp-old.regression.localhost"}
COWORK_SITES = {"new": "cowork-new.regression.localhost", "old": "cowork-old.regression.localhost"}
# Cowork 老站点的盘点（上传导入，走正式的导入函数）：(标题, [(编码, 名称, 单位, 账面, 实盘)])
COWORK_COUNTS = [("ตรวจนับเก่า มีจำนวน", [("CW-001", "สินค้าเก่า ก", "ชิ้น", 10, 9), ("CW-002", "สินค้าเก่า ข", "กล่อง", 4, 4)]),
                 ("ตรวจนับเก่า ยังไม่นับ", [("CW-003", "สินค้าเก่า ค", "ชิ้น", 7, None)])]
# 这两份盘点的单号（上传导入时的请求号）：清单按它认出本套自己的盘点，不跟着实验栈里别的流程累积的盘点走
COWORK_COUNT_IDS = [str(uuid.uuid5(uuid.NAMESPACE_URL, f"regression-cowork-count-{index}")) for index in range(len(COWORK_COUNTS))]
# Cowork 老站点的对账任务：照对账页解析好的两边内容走正式的建任务函数（`create_from_parsed`，不识别、不排后台），
# 数据同 `scripts/cowork-*-recon-lab-verify.py` 的合成样例
COWORK_INCOME = {"title": "รายได้เก่า มิ.ย. 2569", "gl_filename": "GL June.xlsx", "vat_filename": "VAT June.xlsx",
                 "header": {"gl_account_code": "4110-01"},
                 "gl": [["วันที่", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต", "รหัสบัญชี"],
                        ["01/06/2569", "GL-1", "sale", "0.00", "1,070.00", "4110-01"],
                        ["02/06/2569", "GL-2", "credit note", "107.00", "0.00", "4110-01"]],
                 "vat": [["วันที่", "เลขที่ใบกำกับ", "ชื่อผู้ซื้อ", "เลขประจำตัวผู้เสียภาษี", "สาขา", "มูลค่า", "ภาษี", "ยอดรวม"],
                         ["01/06/2569", "GL-1", "ACME", "1234567890123", "00000", "1,000.00", "70.00", "1,070.00"],
                         ["02/06/2569", "GL-2", "ACME", "1234567890123", "00000", "(100.00)", "(7.00)", "(107.00)"]]}
COWORK_BANK = {"title": "ธนาคารเก่า มิ.ย. 2569", "filename": "STM KBANK.pdf", "header": {"gl_opening": "990.00", "gl_closing": "590.00"},
               "statement": {"pages": [{"document": {"bank_name": "Kasikornbank", "opening_balance": "1,000.00", "closing_balance": "600.00",
                                                     "entries": [{"description": "SWD ACME INV-77", "deposit": "0", "withdrawal": "400.00",
                                                                  "balance": "600.00", "transaction_date_raw": "01/06/2569"}]}}],
                             "_needs_review": False},
               "gl": {"pages": [{"document": {"entries": [{"voucher_no": "GL-1", "account_code": "1000", "description": "payment",
                                                            "credit": "400.00", "transaction_date": "2026-06-01"}]}}]}}
# Cowork 老站点的对账预设：(对账类型, 范围, 规则, 答案)
COWORK_PRESETS = [("bank", "bank|KBANK|4821|1112-01", "grouped", "yes"), ("income", "income|0105556012345|4201", "excluded", "no")]
DMS_SITES = {"new": "dms-new.regression.localhost", "old": "dms-old.regression.localhost"}
DMS_OPERATOR = "operator@dms.regression"
# 合法校验位的合成证号（不是真人）；老站点 DMS 里那位已有客户用它
DMS_CUSTOMER_PID = "1000000730317"
ADMIN = "admin@{}"
JOBS = {"products": ("products", "profile"),
        "stock": ("inventory", "stock-in", "stock-out", "stock-transfer", "stock-card", "stock-count", "warehouses", "profile"),
        "purchase": ("purchase", "suppliers", "profile"), "sales": ("sales", "clients", "profile"),
        "cashier": ("cashier", "pos-receipts", "pos-shifts", "profile"), "tasks": ("tasks", "profile")}
# 新开户的商品：每件一种认法（扫 / 输 `scan` 那个值应认出这件）
NEW_PRODUCTS = [
    {"key": "by_code", "product_name": "สินค้าทดสอบ รหัส", "code": "NEW-CODE-01", "scan": "NEW-CODE-01"},
    {"key": "by_barcode", "product_name": "สินค้าทดสอบ บาร์โค้ด", "code": "NEW-BAR-01", "barcode": "8850001000019", "scan": "8850001000019"},
    {"key": "by_qr", "product_name": "สินค้าทดสอบ คิวอาร์", "code": "NEW-QR-01", "qr_payload": "PEARNLY-QR-0001", "scan": "PEARNLY-QR-0001"},
    {"key": "by_internal", "product_name": "สินค้าทดสอบ รหัสภายใน", "code": "NEW-INT-01", "internal_code": "INT-0001", "scan": "INT-0001"},
    {"key": "by_unit", "product_name": "รถทดสอบ รายคัน", "code": "NEW-UNIT-01", "serial_kind": "vehicle", "scan": "NEW-UNIT-01"},
]
EXPRESS_PRODUCT = {"key": "by_express", "code": "EXP-0001", "name": "สินค้าจาก Express", "unit": "ชิ้น", "barcode": "8850009990008",
                   "scan": "EXP-0001"}
OLD_COMPANIES = ("บริษัท ข้อมูลเก่า เอ จำกัด", "บริษัท ข้อมูลเก่า บี จำกัด")


def lab_tax(name) -> str:
    """合成税号（过泰国税号校验位），同 `scripts/erp-express-linked-lab-fixture.py` 的做法。"""
    import hashlib

    body = "0105" + str(int(hashlib.sha256(name.encode()).hexdigest(), 16))[:8]
    return body + str((11 - sum(int(d) * (13 - i) for i, d in enumerate(body)) % 11) % 10)


def ean13(first12: str) -> str:
    """合法的 EAN-13（补校验位）：假摄像头的条码视频只认合法码。"""
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12))
    return first12 + str((10 - total % 10) % 10)


def commit(value=None):
    frappe.db.commit()
    return value


def assign_jobs(product_site, companies):
    from pearnly_erp_portal.access import menu
    from pearnly_erp_portal.team import service as team

    domain = product_site.split(".")[0] + ".regression"
    for job, keys in JOBS.items():
        email = f"{job}@erp.regression"
        if frappe.db.exists("User", email):
            commit(team.set_member_roles(email, roles=[menu.role(key) for key in keys], companies=companies))
    return domain


def build_new():
    from frappe.utils import now_datetime
    from pearnly_core.cc_desktop import books
    from pearnly_erp_portal.express import masters, push
    from pearnly_erp_portal.inventory import serials, stock
    from pearnly_erp_portal.masters import company as companies
    from pearnly_erp_portal.products import service as products

    frappe.set_user(ADMIN.format("erp.regression"))
    co = companies.ensure_default()
    name = frappe.db.get_value("ERP Company", co, "company_name")
    if not push.connected(co):
        tax = lab_tax(SITES["new"])
        companies.update_company(co, tax_id=tax, branch="00000")
        books.replace("lab-dataset-new-" + tax, [{"path": "C:\\PearnlyExpress\\NEW\\" + tax, "company": name, "tax_id": tax, "branch": "00000",
                                                  "writable": True, "period_start": "2026-01-01", "period_end": "2026-12-31", "uid": "NEW"}],
                      now_datetime())
        commit()
    if not push.linked(co):
        commit(companies.update_company(co, express_enabled=1))  # 公司页勾「接 Express」（用户 10-29：连上且勾了才推送 / 同步）
    masters.apply(co, "NEW", {"warehouses": [{"code": f"0{i}", "name": f"คลัง {i}"} for i in range(1, 8)],
                              "settings": {"mainloc": "01"},
                              "products": [{"code": EXPRESS_PRODUCT["code"], "name": EXPRESS_PRODUCT["name"], "unit": EXPRESS_PRODUCT["unit"],
                                            "kind": "stock", "prices": ["50"], "barcode": EXPRESS_PRODUCT["barcode"]}],
                              "stock": [], "customers": [], "suppliers": []})
    commit()
    companies.set_active_company(co)
    found = {}
    for spec in NEW_PRODUCTS:
        product = frappe.db.get_value("ERP Product", {"code": spec["code"]})
        fields = {k: v for k, v in spec.items() if k not in ("key", "scan", "serial_kind")}
        if not product:
            product = commit(products.create_product(unit="ชิ้น", unit_price=100, default_cost=60, **fields))["name"]
        else:
            # 定义改了（条码、二维码内容、内部编码）就收敛到定义上，走正式改商品接口
            changes = {k: v for k, v in fields.items() if k in ("barcode", "qr_payload", "internal_code")
                       and frappe.db.get_value("ERP Product", product, k) != v}
            if changes:
                commit(products.update_product(product, **changes))
        if spec.get("serial_kind") and not frappe.db.get_value("ERP Product", product, "track_serial"):
            commit(serials.turn_on(product, kind=spec["serial_kind"]))
        found[spec["key"]] = {"product": product, "scan": spec["scan"]}
    found[EXPRESS_PRODUCT["key"]] = {"product": frappe.db.get_value("ERP Product", {"express_code": EXPRESS_PRODUCT["code"]}),
                                     "scan": EXPRESS_PRODUCT["scan"]}
    warehouses = frappe.get_all("ERP Warehouse", filters={"company": co, "is_active": 1}, fields=["name", "warehouse_name", "express_code"],
                                order_by="express_code asc")
    on_hand = frappe.db.sql("select coalesce(sum(qty_on_hand), 0) from `tabERP Stock Balance` where warehouse in %(w)s",
                            {"w": tuple(w.name for w in warehouses) or ("",)})[0][0]
    assign_jobs(SITES["new"], [co])
    return {"dataset": "new", "site": SITES["new"], "company": co, "company_name": name, "express_linked": push.linked(co),
            "warehouses": warehouses, "stock_on_hand": float(on_hand), "products": found}


def build_old():
    from pearnly_erp_portal.cashier import sales as pos, shifts
    from pearnly_erp_portal.inventory import count, locations
    from pearnly_erp_portal.masters import company, customers
    from pearnly_erp_portal.products import service as products
    from pearnly_erp_portal.purchasing import entry as purchase, suppliers
    from pearnly_erp_portal.sales import entry as sales

    admin = ADMIN.format("erp.regression")
    frappe.set_user(admin)
    marker = frappe.db.get_value("ERP Company", {"company_name": OLD_COMPANIES[0]})
    if not marker:
        a = commit(company.create_company(company_name=OLD_COMPANIES[0], tax_id=lab_tax(OLD_COMPANIES[0]),
                                          warehouses=["คลังหลัก", "คลังสาขา"], payment_methods=["cash", "transfer"]))
        b = commit(company.create_company(company_name=OLD_COMPANIES[1], tax_id=lab_tax(OLD_COMPANIES[1]), payment_methods=["cash"]))
        company.set_active_company(a["name"])
        commit(locations.api_save_locations(warehouse=a["warehouse"], rows=[{"location_name": "A-01"}, {"location_name": "A-02"}]))
        goods = [commit(products.create_product(product_name=f"สินค้าเก่า {i}", code=f"OLD-{i:03d}", barcode=ean13(f"88500020{i:04d}"),
                                                unit="ชิ้น", unit_price=price, default_cost=price / 2))["name"]
                 for i, price in enumerate((120, 45.5, 300, 80, 15), 1)]
        supplier = commit(suppliers.create_supplier(supplier_name="ผู้ขายเก่า", tax_id=lab_tax("ผู้ขายเก่า")))["name"]
        customer = commit(customers.create_customer(customer_name="ลูกค้าเก่า", tax_id=lab_tax("ลูกค้าเก่า")))["name"]

        def lines(place, qty):
            return [{"product": p, "item_type": "goods", "description": f"สินค้าเก่า {i}", "qty": qty * i, "unit": "ชิ้น",
                     "unit_price": price, "vat_rate": 7, "warehouse": place, "location": ""}
                    for i, (p, price) in enumerate(zip(goods, (120, 45.5, 300, 80, 15)), 1)]

        for place in (a, b):
            company.set_active_company(place["name"])
            commit(purchase.post({"company": place["name"], "supplier": supplier, "doc_date": "2026-10-01", "currency": "THB", "fx_rate": 1,
                                  "has_vat": 1, "price_mode": "exclusive", "doc_kind": "purchase_invoice", "update_stock": 1,
                                  "doc_no": "OLD-IN-" + place["name"], "warehouse": place["warehouse"], "lines": lines(place["warehouse"], 20)},
                                 request_id="old-in-" + place["name"]))
        company.set_active_company(a["name"])
        for void in (False, True):
            name = commit(sales.post({"company": a["name"], "customer": customer, "doc_date": "2026-10-05", "has_vat": 1, "vat_rate": 7,
                                      "source": "manual", "doc_kind": "receipt", "warehouse": a["warehouse"], "paid_amount": 100,
                                      "payment_method": "cash", "payment_date": "2026-10-05",
                                      "lines": [{**row, "vat_applicable": 1} for row in lines(a["warehouse"], 1)]},
                                     request_id=f"old-sale-{void}"))["name"]
            if void:
                commit(sales.cancel(name, reason="regression"))
        terminal = a["terminals"][0]
        shift = commit(shifts.open_shift(terminal=terminal, opening_float=500))["name"]
        sale = commit(pos.create_sale(lines=[{"product": goods[0], "qty": 2, "unit_price": 120}], payments=[{"method": "cash", "amount": 300}],
                                      shift=shift, terminal=terminal, client_uuid="old-pos-sale"))["name"]
        first = pos.get_sale(sale)["lines"][0]
        commit(pos.refund(sale, lines=[{"original_line": first["name"], "qty": 1}], payments=[{"method": "cash", "amount": 120}],
                          reason="ชำรุด", client_uuid="old-pos-refund"))
        commit(shifts.close_shift(shift, counted={"cash": shifts.summary(shift, own_only=False)["expected_cash"]}))
        commit(count.create_count(title="นับประจำเดือน (เปิด)", warehouse=a["warehouse"]))
        posted = commit(count.create_count(title="นับประจำเดือน (ปิดแล้ว)", warehouse=a["warehouse"]))["name"]
        commit(count.add_entry(count=posted, product=goods[0], qty=19, warehouse=a["warehouse"], request_id="old-count-1"))
        commit(count.close_count(posted, review_token=count.review_count(posted)["review_token"]))
    a = frappe.db.get_value("ERP Company", {"company_name": OLD_COMPANIES[0]})
    b = frappe.db.get_value("ERP Company", {"company_name": OLD_COMPANIES[1]})
    # 早先建的条码不是合法 EAN-13（假摄像头画不出来）：收敛到定义上，走正式改商品接口
    company.set_active_company(a)
    for i in range(1, 6):
        product = frappe.db.get_value("ERP Product", {"code": f"OLD-{i:03d}"})
        if product and frappe.db.get_value("ERP Product", product, "barcode") != ean13(f"88500020{i:04d}"):
            commit(products.update_product(product, barcode=ean13(f"88500020{i:04d}")))
    assign_jobs(SITES["old"], [a])
    products_list = frappe.get_all("ERP Product", filters={"code": ["like", "OLD-%"]}, fields=["name", "code", "barcode"], order_by="code asc")
    return {"dataset": "old", "site": SITES["old"], "companies": [a, b],
            "warehouses": frappe.get_all("ERP Warehouse", filters={"company": ["in", [a, b]]}, fields=["name", "warehouse_name", "company"],
                                         order_by="creation asc"),
            "products": products_list,
            "stock_on_hand": float(frappe.db.sql("select coalesce(sum(qty_on_hand), 0) from `tabERP Stock Balance`")[0][0]),
            "documents": {doctype: frappe.db.count(doctype) for doctype in ("Purchase Document", "ERP Sales Document", "ERP Sale",
                                                                           "ERP Shift", "ERP Stock Count")}}


def cowork_state(site, dataset):
    """Cowork 站点此刻的清单：站点归属、各类对账任务与预设、盘点（流程用例照它判页面与落库）。"""
    from pearnly_cowork.access import scope

    workspace = scope.existing_workspace()
    counts = frappe.get_all("Cowork Stocktake Task", filters={"workspace": workspace}, fields=["name", "title", "status"],
                            order_by="creation asc", limit_page_length=50)
    return {"dataset": dataset, "product": "cowork", "site": site, "workspace": workspace,
            "workspaces": frappe.db.count("Cowork Workspace"),
            "recon_task_names": {kind: frappe.get_all(doctype, filters={"workspace": workspace}, pluck="name", limit_page_length=5)
                                 for kind, doctype in (("bank", "Cowork Bank Reconciliation"), ("income", "Cowork Income Reconciliation"))},
            "recon_tasks": {kind: frappe.db.count(doctype, {"workspace": workspace}) for kind, doctype in
                            (("bank", "Cowork Bank Reconciliation"), ("income", "Cowork Income Reconciliation"), ("vat", "Cowork VAT Check"))},
            "presets": {kind: frappe.db.count("Cowork Recon Preset", {"workspace": workspace, "recon_kind": kind})
                        for kind in ("bank", "income")},
            "stock_counts": counts,
            # 本套固定数据自己的盘点（老站点上传导入的两份；新开户没有）
            "dataset_counts": frappe.get_all("Cowork Stocktake Task", filters={"workspace": workspace, "name": ["in", COWORK_COUNT_IDS]},
                                             fields=["name", "title", "status"], order_by="creation asc")}


def build_cowork_new():
    frappe.set_user(ADMIN.format("cowork.regression"))
    return cowork_state(COWORK_SITES["new"], "new")


def build_cowork_old():
    import io

    from openpyxl import load_workbook
    from pearnly_cowork.access import scope
    from pearnly_cowork.reconciliation import preset_store
    from pearnly_cowork.stocktake import upload

    frappe.set_user(ADMIN.format("cowork.regression"))
    workspace = scope.default_workspace()
    commit()
    for request_id, (title, rows) in zip(COWORK_COUNT_IDS, COWORK_COUNTS):
        if frappe.db.exists("Cowork Stocktake Task", request_id):
            continue
        book = load_workbook(io.BytesIO(upload.template()))
        sheet = book.active
        for code, name, unit, book_qty, actual in rows:
            sheet.append([code, name, "", unit, book_qty, actual, "", "", ""])
        output = io.BytesIO()
        book.save(output)
        commit(upload.create(title, request_id, output.getvalue()))
    # 第一份（全都数过）结束掉：结束的盘点才有「导出」（`stocktake.js`），导出流程用它
    first = COWORK_COUNT_IDS[0]
    if frappe.db.get_value("Cowork Stocktake Task", first, "status") == "active":
        from pearnly_cowork.stocktake import service as stocktake

        commit(stocktake.close(first))
    from pearnly_cowork.reconciliation import bank_recon_service, income_recon_service

    income_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "regression-cowork-income-0"))
    if not frappe.db.exists("Cowork Income Reconciliation", {"workspace": workspace}):
        commit(income_recon_service.create_from_parsed(
            workspace, COWORK_INCOME["title"], income_id, {"kind": "excel", "sheets": [COWORK_INCOME["gl"]]},
            header=COWORK_INCOME["header"], vat_source={"kind": "excel", "cells": COWORK_INCOME["vat"]},
            gl_filename=COWORK_INCOME["gl_filename"], vat_filename=COWORK_INCOME["vat_filename"]))
    bank_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "regression-cowork-bank-0"))
    if not frappe.db.exists("Cowork Bank Reconciliation", {"workspace": workspace}):
        commit(bank_recon_service.create_from_parsed(
            workspace, COWORK_BANK["title"], bank_id, COWORK_BANK["statement"], COWORK_BANK["gl"], header=COWORK_BANK["header"],
            filename=COWORK_BANK["filename"]))
    for kind, found, rule, value in COWORK_PRESETS:
        commit(preset_store.remember(workspace, kind, found, rule, value))
    return cowork_state(COWORK_SITES["old"], "old")


def dms_directory(which):
    """DMS 里读到的东西（发布成本地完整快照用）：主档、颜色、地址三级、客户。新开户最少一套，老站点多几项、带一位客户。"""
    masters = {key: [[key + "-1", "LAB", "Lab " + key]] for key in (
        "cars", "place_books", "term_sales", "branches", "regis_behalfs", "advisors",
        "source_banks", "cheque_banks", "cashier_banks", "card_banks")}
    masters["prefixes"] = [["prefix-1", "นาย"], ["prefix-2", "นางสาว"]]
    masters["company_banks"] = [["bank-1", "LAB", "Lab bank", "0000000000", "lab"]]
    masters["employees"] = [{"id": "employee-1", "name": "Lab employee"}]
    paints = {"cars-1": [["paint-1", "LAB", "ขาวมุก"]]}
    geo = {"provinces": {"": [["P1", "สมุทรปราการ"], ["P2", "กรุงเทพมหานคร"]]},
           "districts": {"P1": [["D1", "เมืองสมุทรปราการ"]], "P2": [["D2", "ลาดพร้าว"]]},
           "subdistricts": {"D1": [["S1", "ปากน้ำ"]], "D2": [["S2", "ลาดพร้าว"]]},
           "zipcodes": {"S1": [["Z1", "10270"]], "S2": [["Z2", "10230"]]}}
    customers = {}
    if which == "old":
        masters["cars"].append(["cars-2", "LAB2", "Lab car two"])
        masters["place_books"].append(["place_books-2", "LAB2", "Lab place two"])
        paints["cars-2"] = [["paint-2", "LAB2", "ดำนิล"]]
        customer = {"customer_id": "customer-1", "name": "ลูกค้าเก่า ทดสอบ", "people_id": DMS_CUSTOMER_PID,
                    "tax_id": DMS_CUSTOMER_PID, "cuscode": "LAB-CUSTOMER-1", "prefix_id": "prefix-1", "phone": "0800000001",
                    "birthday_be": "01/01/2530", "house_no": "9/9", "province_id": "P2", "district_id": "D2",
                    "subdistrict_id": "S2", "zipcode_id": "Z2", "province_name": "กรุงเทพมหานคร", "district_name": "ลาดพร้าว",
                    "subdistrict_name": "ลาดพร้าว", "zipcode_name": "10230"}
        for suffix in ("_ct", "_sd"):
            customer.update({key + suffix: customer[key] for key in
                             ("house_no", "province_id", "district_id", "subdistrict_id", "zipcode_id")})
        customers["customer-1"] = customer
    return {"masters": masters, "paints": paints, "geo": geo, "customers": customers}


def build_dms(which):
    """DMS 一套：本地镜像开着、一个连接（模板 + 操作员自己的）、操作员已绑 LINE、DMS 读到的东西发布成完整快照。
    每次跑都重新发布一遍快照（线上夜间同步也是整份重发），其余已有就不动。"""
    import time

    from frappe.installer import update_site_config
    from pearnly_core import line, line_binding
    from pearnly_dms.snapshots import store

    site = DMS_SITES[which]
    if not frappe.conf.get("pearnly_line_mirror"):
        update_site_config("pearnly_line_mirror", "1")
        frappe.local.conf.pearnly_line_mirror = "1"
        # 网页进程的站点配置缓存 60 秒（Frappe 16 `site_cache(ttl=60)`，同 `scripts/flows/run.py` 的 `Lab.site_config`）：
        # 只在新实验栈第一次补齐时写，等它过期，接着跑的 DMS 流程才读得到本地镜像
        time.sleep(61)
    if line.is_configured() or not line.mirroring():
        raise SystemExit("LINE must be the local mirror in the regression laboratory")
    frappe.set_user("Administrator")
    lab = "regression-lab-" + which
    if not frappe.db.exists("Pearnly ERP Endpoint", {"adapter": "mrerp_dms", "owner_user": ["is", "not set"]}):
        # 连接页填的那一份（店的 DMS 地址 + 管理员账号）：外部地址不可解析，实验室里只读本地快照
        frappe.get_doc({"doctype": "Pearnly ERP Endpoint", "label": lab, "adapter": "mrerp_dms", "enabled": 1,
                        "system_url": "https://dms.regression.invalid/dms/", "admin_username": lab + "-admin",
                        "admin_password": "regression-lab-only", "booking_defaults": "{}"}).insert(ignore_permissions=True)
    if not frappe.db.exists("DMS Operator", DMS_OPERATOR):
        frappe.get_doc({"doctype": "DMS Operator", "user": DMS_OPERATOR, "display_name": "Lab operator", "status": "active",
                        "dms_role": "sales", "dms_username": lab + "-sales", "dms_password": "regression-lab-only",
                        "can_query_dms": 1}).insert(ignore_permissions=True)
    commit()
    endpoint = frappe.db.get_value("Pearnly ERP Endpoint", {"owner_user": DMS_OPERATOR, "enabled": 1})
    line_user = "Uflowdms" + which  # 合成的 LINE 用户（只在实验室）
    if line_binding.user_of(line_user) != DMS_OPERATOR:
        line_binding.bind(line_user, DMS_OPERATOR, "Lab operator")
    data = dms_directory(which)
    stamp = time.time() - 5
    key = store.scope(endpoint)
    store.publish(endpoint, "geo", data["geo"], stamp, expected_scope=key)
    store.publish(endpoint, "vehicles", {"masters": {"cars": data["masters"]["cars"]}, "paints": data["paints"],
                                         "catalogues": {"lab": {}}}, stamp, expected_scope=key)
    store.publish(endpoint, "company", {"masters": {k: v for k, v in data["masters"].items() if k not in {"cars", "prefixes"}},
                                        "catalogues": {"lab": {}}}, stamp, expected_scope=key)
    store.publish(endpoint, "customers", {"customers": data["customers"], "prefixes": data["masters"]["prefixes"]},
                  stamp, expected_scope=key)
    commit()
    return {"dataset": which, "product": "dms", "site": site, "endpoint": endpoint, "operator": DMS_OPERATOR,
            "line_user_id": line_user, "mirror": line.mirroring(),
            "masters": {k: data["masters"][k] for k in ("cars", "place_books", "term_sales", "regis_behalfs", "prefixes")},
            "paints": data["paints"], "geo": data["geo"],
            "customers": [{"customer_id": cid, "name": c["name"], "people_id": c["people_id"]} for cid, c in data["customers"].items()]}


def main(which, product="erp"):
    if product == "dms":
        frappe.init(site=DMS_SITES[which], sites_path="/home/frappe/frappe-bench/sites")
        frappe.connect()
        try:
            print(json.dumps(build_dms(which), ensure_ascii=False, default=str))
        finally:
            frappe.destroy()
        return
    sites = COWORK_SITES if product == "cowork" else SITES
    frappe.init(site=sites[which], sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    try:
        if product == "cowork":
            manifest = build_cowork_new() if which == "new" else build_cowork_old()
        else:
            manifest = build_new() if which == "new" else build_old()
        print(json.dumps(manifest, ensure_ascii=False, default=str))
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main(*sys.argv[1:3])
