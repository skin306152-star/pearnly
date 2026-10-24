"""实验栈专用：给一家测试公司造大批假库存流水，测收发存 / 库存一览的大数据量打开时限。

只在测试机实验栈的站点容器里跑（`testenv.sh` 起的 pearnly-testenv-* 栈）；生产一律拒绝：
  - 站点名必须是 test 开头的测试租户；
  - 必须带环境变量 PEARNLY_LOADTEST_LAB=1（只由测试机 WSL 里对 pearnly-testenv-* 容器执行的调用方给；
    生产上的同名测试租户 testerp 也是 test 开头，所以这条是真正的闸，别在生产容器里加这个变量）。

造出的数据全部带标记，`--drop` 一键删除：
  - 商品：名称前缀「LOADTEST 」、编码 LT-00001…（本地档，不挂 Express）；
  - 仓库：「LOADTEST 仓 01」…（挂在 --company 名下）；
  - 流水：ref_type = "LOADTEST"、doc_no = LT-…；余额按流水汇总写进 ERP Stock Balance（与正式写法同一个键）。

用法（容器里，bench 目录）：
  env/bin/python seed_stock_load.py <site> --company CO-00001 --products 2000 --warehouses 20 --per-product 6 --movements 300000
  env/bin/python seed_stock_load.py <site> --company CO-00001 --drop

在测试机 WSL 里对实验栈 ERP 网页容器跑（容器名必须是 pearnly-testenv-*，验收 10-25 的做法）：
  C=pearnly-testenv-erp-erp-web-1
  docker cp seed_stock_load.py "$C":/tmp/seed_stock_load.py
  docker exec -i -e PEARNLY_LOADTEST_LAB=1 "$C" bash -c "cd /home/frappe/frappe-bench && env/bin/python /tmp/seed_stock_load.py testerp.pearnly.com --company CO-00001" </dev/null
  docker exec -u root -i "$C" rm -f /tmp/seed_stock_load.py </dev/null
造完用 `scripts/smoke/run.py --site erp --company CO-00001` 跑页面打开时限（收发存、库存一览）。
"""
import argparse
import os
import random
import sys
from datetime import date, timedelta

import frappe

TAG = "LOADTEST"
BATCH = 5000


def check_lab(site):
    if not site.startswith("test"):
        sys.exit(f"refuse: {site} is not a test tenant")
    if os.environ.get("PEARNLY_LOADTEST_LAB") != "1":
        sys.exit("refuse: PEARNLY_LOADTEST_LAB=1 not set (lab stack only)")


def insert(table, columns, rows):
    cols = ", ".join(f"`{c}`" for c in columns)
    marks = "(" + ", ".join(["%s"] * len(columns)) + ")"
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        frappe.db.sql(f"INSERT INTO `{table}` ({cols}) VALUES " + ", ".join([marks] * len(chunk)),
                      [value for row in chunk for value in row])
        frappe.db.commit()


def drop(company):
    from pearnly_erp_portal.inventory import contracts
    whs = frappe.get_all("ERP Warehouse", filters={"company": company, "warehouse_name": ["like", f"{TAG}%"]}, pluck="name")
    n = frappe.db.sql("delete from `tabERP Stock Movement` where ref_type=%s", TAG)
    frappe.db.sql("delete from `tabERP Stock Balance` where name like %s", ("BAL-PRD-LT%",))
    if whs:
        frappe.db.sql("delete from `tabERP Stock Balance` where warehouse in %s", (tuple(whs),))
    frappe.db.sql("delete from `tabERP Product` where name like %s", ("PRD-LT%",))
    frappe.db.sql("delete from `tabERP Warehouse` where company=%s and warehouse_name like %s", (company, f"{TAG}%"))
    frappe.db.commit()
    print("dropped", len(whs), "warehouses and all LOADTEST rows")


def seed(company, products, warehouses, per_product, movements, seed_value):
    from pearnly_erp_portal.inventory import contracts
    rnd = random.Random(seed_value)
    now = frappe.utils.now()
    user = "Administrator"
    base = dict(creation=now, modified=now, owner=user, modified_by=user, docstatus=0)
    wh_names = [f"WH-LT{i:03d}" for i in range(1, warehouses + 1)]
    insert("tabERP Warehouse", ["name", "creation", "modified", "owner", "modified_by", "docstatus", "warehouse_name", "company", "is_active"],
           [[w, now, now, user, user, 0, f"{TAG} 仓 {i:02d}", company, 1] for i, w in enumerate(wh_names, 1)])
    prd_names = [f"PRD-LT{i:05d}" for i in range(1, products + 1)]
    insert("tabERP Product", ["name", "creation", "modified", "owner", "modified_by", "docstatus", "product_name", "code", "code_key",
                              "unit", "base_unit", "is_active"],
           [[p, now, now, user, user, 0, f"{TAG} 商品 {i:05d}", f"LT-{i:05d}", f"|LT-{i:05d}", "ชิ้น", "ชิ้น", 1]
            for i, p in enumerate(prd_names, 1)])
    positions = [(p, w) for p in prd_names for w in rnd.sample(wh_names, min(per_product, warehouses))]
    start = date(2026, 1, 1)
    days = (date.today() - start).days or 1
    kinds = [("purchase_in", 1), ("stock_in", 1), ("sale_out", -1), ("stock_out", -1), ("adjust", 1)]
    rows, balance = [], {}
    for n in range(movements):
        p, w = positions[n % len(positions)] if n < len(positions) else rnd.choice(positions)
        kind, sign = ("opening", 1) if n < len(positions) else rnd.choice(kinds)
        qty = (rnd.randint(50, 200) if kind == "opening" else rnd.randint(1, 20)) * sign
        cost = round(rnd.uniform(5, 500), 2)
        day = start if kind == "opening" else start + timedelta(days=rnd.randint(1, days))
        rows.append([f"STM-LT-{n:07d}", now, now, user, user, 0, str(day), "09:00:00", kind, p, w, qty, "ชิ้น", 1, qty,
                     1, cost, round(qty * cost, 2), TAG, f"LT-{n:07d}", ""])
        b = balance.setdefault((p, w), [0, 0.0])
        b[0] += qty
        b[1] += qty * cost
    insert("tabERP Stock Movement", ["name", "creation", "modified", "owner", "modified_by", "docstatus", "posting_date", "posting_time",
                                     "movement_type", "product", "warehouse", "qty", "unit", "factor", "qty_base", "cost_known",
                                     "unit_cost", "value_delta", "ref_type", "doc_no", "location"], rows)
    insert("tabERP Stock Balance", ["name", "creation", "modified", "owner", "modified_by", "docstatus", "product", "warehouse",
                                    "location", "qty_on_hand", "cost_known", "unit_cost", "value", "last_movement_at"],
           [[contracts.position_name("BAL", p, w, ""), now, now, user, user, 0, p, w, "", q, 1,
             round(v / q, 2) if q else 0, round(v, 2), now] for (p, w), (q, v) in balance.items()])
    print(f"seeded {company}: {warehouses} warehouses, {products} products, {len(positions)} positions, {movements} movements")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("site")
    ap.add_argument("--company", required=True)
    ap.add_argument("--products", type=int, default=2000)
    ap.add_argument("--warehouses", type=int, default=20)
    ap.add_argument("--per-product", type=int, default=6)
    ap.add_argument("--movements", type=int, default=300000)
    ap.add_argument("--seed", type=int, default=20261025)
    ap.add_argument("--drop", action="store_true")
    a = ap.parse_args()
    check_lab(a.site)
    os.chdir("/home/frappe/frappe-bench/sites")
    frappe.init(site=a.site)
    frappe.connect()
    if a.drop:
        drop(a.company)
    else:
        if frappe.db.exists("ERP Product", "PRD-LT00001"):
            sys.exit("LOADTEST rows already exist; run --drop first")
        seed(a.company, a.products, a.warehouses, a.per_product, a.movements, a.seed)
    frappe.destroy()


if __name__ == "__main__":
    main()
