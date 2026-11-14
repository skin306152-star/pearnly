"""性能体检基准：在回归实验室 ERP 站点造一家「大账套」公司（同线上 testerp นิว ลิงค์ CO-00146 的量级），供接口耗时 / 查询条数测量重复用。

量级（默认值，可调）：客户 10,511、物料 1,157（其中 251 个在本公司首个仓有库存）、另一家公司 2 万个物料各一条结存
（撑出线上整站的结存 / 流水表规模）、每个有库存的物料若干条库存流水、
一张进行中的盘点（盘点在库 → 251 行快照）并录入一部分实盘记录。客户 / 物料按 Express 同步的样子挂 `express_company`
（线上那家是从 Express 同步来的）。公司、盘点、实盘记录走系统正式接口建；客户、物料、流水、结存量大，按正式写法的同一个键
直接批量写（同 `scripts/smoke/seed_stock_load.py`）。

只在任务自有的回归实验室跑（容器环境 PEARNLY_REGRESSION_LAB=1，`infrastructure/compose/regression-lab.yml`）。
重复跑：已造过就只打印已有的公司 / 盘点名（标记是公司名），不重复造。最后一行 `PERF_SEED {json}`。

    docker compose -p <栈> -f infrastructure/compose/regression-lab.yml exec -T -w /home/frappe/frappe-bench/sites bench \\
        ../env/bin/python /workspace/scripts/perf/seed_big_company.py erp.regression.localhost
"""
import argparse
import json
import os
import random
from datetime import date, timedelta

if os.environ.get("PEARNLY_REGRESSION_LAB") != "1":
    raise SystemExit("Requires the task-owned regression laboratory")

import frappe  # noqa: E402

COMPANY = "บริษัท นิว ลิงค์ ทดสอบประสิทธิภาพ จำกัด"
NOISE_COMPANY = "บริษัท อื่น ทดสอบประสิทธิภาพ จำกัด"
ADMIN = "admin@erp.regression"
BATCH = 2000


def insert(table, columns, rows):
    cols = ", ".join(f"`{c}`" for c in columns)
    marks = "(" + ", ".join(["%s"] * len(columns)) + ")"
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        frappe.db.sql(f"INSERT INTO `{table}` ({cols}) VALUES " + ", ".join([marks] * len(chunk)),
                      [value for row in chunk for value in row])
    frappe.db.commit()


def existing():
    company = frappe.db.get_value("ERP Company", {"company_name": COMPANY})
    if not company:
        return None
    warehouse = frappe.get_all("ERP Warehouse", filters={"company": company}, pluck="name", order_by="creation asc",
                               limit_page_length=1)[0]
    count = frappe.get_all("ERP Stock Count", filters={"warehouse": warehouse, "status": "Active"}, pluck="name",
                           order_by="creation desc", limit_page_length=1)
    return {"company": company, "warehouse": warehouse, "count": count[0] if count else None}


def noise_warehouse():
    """别家公司（撑表规模用）的仓；没有就走正式接口建。"""
    from pearnly_erp_portal.masters import company as companies

    co = frappe.db.get_value("ERP Company", {"company_name": NOISE_COMPANY})
    if not co:
        frappe.set_user(ADMIN)
        co = companies.create_company(company_name=NOISE_COMPANY, tax_id="0105561999981", warehouses=["คลังอื่น"],
                                      payment_methods=["cash"])["name"]
        frappe.db.commit()
    return frappe.get_all("ERP Warehouse", filters={"company": co}, pluck="name", order_by="creation asc", limit_page_length=1)[0]


def add_noise(co, warehouse, total):
    """线上 testerp 整个站点的表规模（11-14 只读实测：结存 20,220 行、物料 59,430、客户 84,912、流水 20,705）：别家物料
    各一条结存 / 一条流水放在另一家公司的仓，按物料查结存、查流水的语句才和线上一样要在大表里找。已有就不再加。"""
    from pearnly_erp_portal.inventory import contracts

    tag = co.replace("-", "")
    if frappe.db.exists("ERP Product", f"PRD-N{tag}-00001") or not total:
        return
    now, start = frappe.utils.now(), date(2026, 1, 1)
    meta = lambda name: [name, now, now, ADMIN, ADMIN, 0]  # noqa: E731
    head = ["name", "creation", "modified", "owner", "modified_by", "docstatus"]
    noise = [f"PRD-N{tag}-{i:05d}" for i in range(1, total + 1)]
    other = noise_warehouse()
    insert("tabERP Product", head + ["product_name", "code", "code_key", "unit", "base_unit", "is_active"],
           [meta(p) + [f"สินค้าอื่น {i:05d}", f"N{i:05d}", f"|N{i:05d}", "ชิ้น", "ชิ้น", 1] for i, p in enumerate(noise, 1)])
    insert("tabERP Stock Movement", head + ["posting_date", "posting_time", "movement_type", "product", "warehouse", "qty", "unit",
                                            "factor", "qty_base", "cost_known", "unit_cost", "value_delta", "ref_type", "doc_no",
                                            "location"],
           [meta(f"STM-N{tag}-{i:07d}") + [str(start), "09:00:00", "opening", p, other, 10, "ชิ้น", 1, 10, 1, 5, 50, "PERFSEED",
                                            f"PN-{i:07d}", ""] for i, p in enumerate(noise)])
    insert("tabERP Stock Balance", head + ["product", "warehouse", "location", "qty_on_hand", "cost_known", "unit_cost", "value",
                                           "last_movement_at"],
           [meta(contracts.position_name("BAL", p, other, "")) + [p, other, "", 10, 1, 5, 50, now] for p in noise])


def add_variety(count_name, co):
    """明细页各种状态都要有（对照测试比得到）：在另一个仓盘到的（位置差异）、作废的记录。已有就不再加。"""
    from pearnly_erp_portal.inventory import count

    other = frappe.get_all("ERP Warehouse", filters={"company": co, "warehouse_name": "คลังสาขา"}, pluck="name",
                           limit_page_length=1)
    if not other or frappe.db.exists("ERP Stock Count Entry", {"count": count_name, "warehouse": other[0]}):
        return
    frappe.set_user(ADMIN)
    items = frappe.get_all("ERP Stock Count Item", filters={"count": count_name, "counted": 0}, pluck="name",
                           order_by="product_name desc", limit_page_length=4)
    for i, item in enumerate(items[:3]):
        count.add_entry(count=count_name, item=item, qty=2 + i, warehouse=other[0], location="B-01", request_id=f"perf-moved-{i}")
        frappe.db.commit()
    made = count.add_entry(count=count_name, item=items[3], qty=7, request_id="perf-voided")
    frappe.db.commit()
    entry = frappe.get_all("ERP Stock Count Entry", filters={"count": count_name, "item": items[3]}, fields=["name", "version"],
                           limit_page_length=1)[0]
    count.edit_entry(entry=entry.name, version=entry.version, voided=1)
    frappe.db.commit()
    return made


def seed(a):
    from pearnly_erp_portal.inventory import contracts, count
    from pearnly_erp_portal.masters import company as companies

    rnd = random.Random(a.seed)
    frappe.set_user(ADMIN)
    made = companies.create_company(company_name=COMPANY, tax_id="0105561999990", warehouses=["คลังสำนักงานใหญ่", "คลังสาขา"],
                                    payment_methods=["cash", "transfer"])
    frappe.db.commit()
    co, warehouse = made["name"], made["warehouse"]
    companies.set_active_company(co)
    frappe.db.commit()
    now = frappe.utils.now()
    meta = lambda name: [name, now, now, ADMIN, ADMIN, 0]  # noqa: E731
    head = ["name", "creation", "modified", "owner", "modified_by", "docstatus"]
    tag = co.replace("-", "")
    insert("tabERP Customer", head + ["customer_name", "party_type", "tax_id", "branch_type", "is_active", "express_code",
                                      "express_company", "address"],
           [meta(f"CUS-P{tag}-{i:05d}") + [f"ลูกค้า ทดสอบ {i:05d}", "company", f"0{rnd.randrange(10**11, 10**12)}", "hq", 1,
                                          f"C{i:05d}", co, f"{i} ถนนทดสอบ กรุงเทพฯ"] for i in range(1, a.customers + 1)])
    products = [f"PRD-P{tag}-{i:05d}" for i in range(1, a.products + 1)]
    insert("tabERP Product", head + ["product_name", "code", "code_key", "barcode", "unit", "base_unit", "is_active",
                                     "unit_price", "price_set", "default_cost", "cost_set", "express_code", "express_company",
                                     "express_stock", "origin"],
           [meta(p) + [f"สินค้า ทดสอบ {i:05d}", f"P{i:05d}", f"|P{i:05d}", f"885{rnd.randrange(10**9, 10**10)}", "ชิ้น", "ชิ้น", 1,
                       100, 1, 50, 1, f"P{i:05d}", co, 1, "express"] for i, p in enumerate(products, 1)])
    stocked = products[:a.stocked]
    start = date(2026, 1, 1)
    rows, balance = [], {}
    for n, product in enumerate(p for p in stocked for _ in range(a.movements)):
        first = n % a.movements == 0
        qty = rnd.randint(50, 200) if first else rnd.choice((1, -1)) * rnd.randint(1, 5)
        cost = 50.0
        day = start + timedelta(days=0 if first else rnd.randint(1, 300))
        rows.append([f"STM-P{tag}-{n:07d}", now, now, ADMIN, ADMIN, 0, str(day), "09:00:00",
                     "opening" if first else "adjust", product, warehouse, qty, "ชิ้น", 1, qty, 1, cost, round(qty * cost, 2),
                     "PERFSEED", f"PS-{n:07d}", ""])
        b = balance.setdefault(product, [0, 0.0])
        b[0] += qty
        b[1] += qty * cost
    insert("tabERP Stock Movement", head + ["posting_date", "posting_time", "movement_type", "product", "warehouse", "qty", "unit",
                                            "factor", "qty_base", "cost_known", "unit_cost", "value_delta", "ref_type", "doc_no",
                                            "location"], rows)
    insert("tabERP Stock Balance", head + ["product", "warehouse", "location", "qty_on_hand", "cost_known", "unit_cost", "value",
                                           "last_movement_at"],
           [meta(contracts.position_name("BAL", p, warehouse, "")) + [p, warehouse, "", q, 1, round(v / q, 2) if q else 0,
                                                                     round(v, 2), now] for p, (q, v) in balance.items()])
    add_noise(co, warehouse, a.noise)
    task = count.create_count(title="นับสต็อก ทดสอบประสิทธิภาพ", warehouse=warehouse)
    frappe.db.commit()
    items = frappe.get_all("ERP Stock Count Item", filters={"count": task["name"]}, fields=["name", "book_qty"],
                           order_by="product_name asc", limit_page_length=a.entries)
    for i, item in enumerate(items):
        count.add_entry(count=task["name"], item=item.name, qty=max(float(item.book_qty) - (i % 3), 0), request_id=f"perf-{i}")
        frappe.db.commit()
    return {"company": co, "warehouse": warehouse, "count": task["name"], "rows": task["rows"], "entries": len(items)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("site")
    ap.add_argument("--customers", type=int, default=10511)
    ap.add_argument("--products", type=int, default=1157)
    ap.add_argument("--stocked", type=int, default=251)
    ap.add_argument("--movements", type=int, default=20, help="每个有库存的物料几条流水")
    ap.add_argument("--entries", type=int, default=60, help="录入几行实盘")
    ap.add_argument("--noise", type=int, default=20000, help="别家物料数（各一条结存与流水），撑出线上表规模")
    ap.add_argument("--seed", type=int, default=20261114)
    a = ap.parse_args()
    frappe.init(site=a.site, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    try:
        assert frappe.conf.get("pearnly_product") == "erp"
        out = existing() or seed(a)
        add_noise(out["company"], out["warehouse"], a.noise)
        if out.get("count"):
            add_variety(out["count"], out["company"])
        out["counts"] = {d: frappe.db.count(d) for d in ("ERP Customer", "ERP Product", "ERP Stock Movement", "ERP Stock Balance")}
        print("PERF_SEED " + json.dumps(out, ensure_ascii=False))
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main()
