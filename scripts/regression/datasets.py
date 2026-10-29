"""两套固定数据（主会话 10-28：所有流程用例都跑这两套，不由窗口临时挑数据）。在回归实验室容器里跑，幂等：已经齐了就只回清单。

- new（照 polymer 新开户）：站点 `erp-new.regression.localhost`。一家公司，按正式方式接上 Express（CC 桌面版心跳报账套），
  7 个仓都由 Express 主档同步来（仓库码 01–07），零库存。商品各靠一种方式认出：
  编码、条码、二维码内容、内部编码、Express 同步来的存货码，外加一件按台管理的商品（车辆）。
- old（照 mrerp 有存量）：站点 `erp-old.regression.localhost`。两家公司（A 两个仓、带库位清单；B 一个仓），
  采购入库有存量，有采购单、销售单（含作废）、收银开班 / 卖 / 退 / 交班、进行中与已过账的盘点。

全部走正式接口（金额、单号、库存都由系统算）。岗位账号照 `lab_users.py` 的岗位，员工只开本套的公司（old 只开 A）。
最后一行打印本套的清单 JSON（公司、仓、各种认法对应的商品与要扫的码、单号），流程用例照它取，不自己挑数据。

    cd /home/frappe/frappe-bench && env/bin/python /workspace/scripts/regression/datasets.py new|old
"""
import json
import os
import sys
import uuid

if os.environ.get("PEARNLY_REGRESSION_LAB") != "1":
    raise SystemExit("Requires the task-owned regression laboratory")

import frappe  # noqa: E402

SITES = {"new": "erp-new.regression.localhost", "old": "erp-old.regression.localhost"}
ADMIN = "admin@{}"
JOBS = {"products": ("products", "profile"),
        "stock": ("inventory", "stock-in", "stock-out", "stock-card", "stock-count", "warehouses", "profile"),
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
    if not push.linked(co):
        tax = lab_tax(SITES["new"])
        companies.update_company(co, tax_id=tax, branch="00000")
        books.replace("lab-dataset-new-" + tax, [{"path": "C:\\PearnlyExpress\\NEW\\" + tax, "company": name, "tax_id": tax, "branch": "00000",
                                                  "writable": True, "period_start": "2026-01-01", "period_end": "2026-12-31", "uid": "NEW"}],
                      now_datetime())
        commit()
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
    assign_jobs(SITES["old"], [a])
    products_list = frappe.get_all("ERP Product", filters={"code": ["like", "OLD-%"]}, fields=["name", "code", "barcode"], order_by="code asc")
    return {"dataset": "old", "site": SITES["old"], "companies": [a, b],
            "warehouses": frappe.get_all("ERP Warehouse", filters={"company": ["in", [a, b]]}, fields=["name", "warehouse_name", "company"],
                                         order_by="creation asc"),
            "products": products_list,
            "stock_on_hand": float(frappe.db.sql("select coalesce(sum(qty_on_hand), 0) from `tabERP Stock Balance`")[0][0]),
            "documents": {doctype: frappe.db.count(doctype) for doctype in ("Purchase Document", "ERP Sales Document", "ERP Sale",
                                                                           "ERP Shift", "ERP Stock Count")}}


def main(which):
    frappe.init(site=SITES[which], sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    try:
        manifest = build_new() if which == "new" else build_old()
        print(json.dumps(manifest, ensure_ascii=False, default=str))
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main(sys.argv[1])
