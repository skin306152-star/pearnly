"""盘点图表预览用的合成数据（只写隔离的 stocktake.localhost）。

为什么要有它：图表只在"有已盘点行"时才画得出来，而实验室站点初始化后只有一个商品两行，
看不出分类、排序、12 组上限和长表。这里造一批商品 / 结存 / 盘点记录，让本地预览能看到
接近真实规模的样子（差异、无差异、未盘点、跨仓位移动混在一起）。

只在 lab 站点写合成数据；不连生产、不发 LINE、不调付费 OCR。按业务键幂等，重复跑是空操作。

用法（在 lab 的 erp web 容器里，cwd 为 sites）：
  docker --context colima-pearnly-stocktake exec -i -w /home/frappe/frappe-bench/sites \\
    pearnly-stocktake-acceptance-erp-web-1 /home/frappe/frappe-bench/env/bin/python - < scripts/local-review/seed-stocktake-chart.py
"""
import json
from pathlib import Path

import frappe

SITE = "stocktake.localhost"
SITE_ROOT = Path("/home/frappe/frappe-bench/sites")
CODE_PREFIX = "DEMO-CHART-"
OWNER = "stocktake-owner@example.com"

# 差异样本：正数=实盘多、负数=实盘少、0=正好对上（口径与工作台一致）。
DELTAS = [-34, 4, 32, 23, -12, 7, 0, -60, 45, 18, -25, 0, 9, -18, 26, -7,
          15, -41, 0, 52, -3, 11, -29, 6, 0, 34, -16, 21, -8, 13]

PRODUCTS = [
    ("沐浴露", "瓶", 15, 8), ("番茄酱", "瓶", 22, 12), ("蛋白质", "罐", 39, 24),
    ("饮料", "瓶", 12, 6), ("洗发水", "瓶", 18, 10), ("牙膏", "支", 9, 5),
    ("洗衣粉", "袋", 28, 16), ("洗洁精", "瓶", 14, 7), ("酱油", "瓶", 16, 9),
    ("醋", "瓶", 13, 7), ("蚝油", "瓶", 25, 14), ("辣椒酱", "瓶", 19, 11),
    ("味精", "袋", 11, 6), ("白砂糖", "袋", 21, 12), ("食盐", "袋", 8, 4),
    ("食用油", "瓶", 42, 26), ("面粉", "袋", 30, 17), ("大米", "袋", 65, 38),
    ("方便面", "包", 7, 4), ("牛奶", "盒", 17, 10), ("酸奶", "盒", 20, 12),
    ("矿泉水", "瓶", 6, 3), ("汽水", "瓶", 10, 5), ("啤酒", "瓶", 24, 14),
    ("咖啡", "盒", 48, 29), ("茶叶", "盒", 55, 33), ("饼干", "包", 15, 8),
    ("巧克力", "盒", 36, 21), ("薯片", "包", 18, 10), ("糖果", "包", 12, 7),
    ("纸巾", "包", 9, 5), ("湿巾", "包", 13, 7), ("垃圾袋", "卷", 16, 9),
    ("保鲜膜", "卷", 14, 8), ("一次性手套", "盒", 27, 15), ("酒精喷雾", "瓶", 23, 13),
]


def main():
    if not (SITE_ROOT / ".pearnly-stocktake-acceptance").is_file():
        raise SystemExit("Refusing a non-lab Site volume")
    frappe.init(site=SITE, sites_path=str(SITE_ROOT))
    frappe.connect()
    if frappe.conf.get("db_host") != "mariadb" or frappe.conf.get("pearnly_product") != "erp":
        raise SystemExit("Refusing a non-lab Site")
    if not frappe.db.exists("User", OWNER):
        raise SystemExit("Lab owner missing")
    frappe.flags.mute_emails = True
    from pearnly_erp_portal.inventory import count as counts
    from pearnly_erp_portal.inventory import stock, warehouses
    from pearnly_erp_portal.products import service as products

    try:
        frappe.set_user("Administrator")
        product_ids = seed_products(products)
        frappe.set_user(OWNER)
        warehouse_ids = seed_stock(product_ids, stock, warehouses)
        seed_tasks(product_ids, warehouse_ids, counts)
        frappe.db.commit()
    except Exception:
        # 整批一个事务：中途失败不留半份盘点（不然重跑会按标题跳过，留下缺记录的任务）。
        frappe.db.rollback()
        raise
    print(json.dumps({"products": len(product_ids),
                      "counts": frappe.db.count("ERP Stock Count")}, ensure_ascii=False))


def seed_products(service):
    out = {}
    for index, (name, unit, price, cost) in enumerate(PRODUCTS):
        code = f"{CODE_PREFIX}{index + 1:03d}"
        existing = frappe.db.get_value("ERP Product", {"code": code}, "name")
        if existing:
            out[name] = existing
            continue
        created = service.create_product(
            product_name=name, code=code, barcode=f"8850{index + 1:09d}",
            qr_payload=f"{code} QR", unit=unit, unit_price=price, default_cost=cost)
        out[name] = created["name"]
    return out


def seed_stock(product_ids, stock, warehouses):
    # 主仓铺 4 个货架（另有 6 个商品的仓位留空 —— 老图表会把空仓位写成"未填"当分类名）；
    # 冷藏仓放饮料/乳制品；备用仓只放两件，用来做一份"已结束"的小盘点。
    plan = {
        "测试主仓": ("A架", "B架", "C架", "D架"),
        "冷藏仓": ("E架", "F架"),
        "备用仓": ("G架",),
    }
    resolved = {}
    for name in plan:
        resolved[name] = warehouses.resolve(name, create=True)
    for index, (name, _unit, _price, cost) in enumerate(PRODUCTS):
        shelves = plan["测试主仓"]
        place = None if index % 6 == 5 else shelves[index % len(shelves)]
        qty = 20 + (index * 7) % 180
        stock.apply_movement(product=product_ids[name], warehouse=resolved["测试主仓"],
                             location=place, movement_type="stock_in", qty=qty, unit_cost=cost)
        # 每 5 个商品在第二个货架上再放一份：同一商品两行，看图表按商品合并后的样子。
        if index % 5 == 2:
            other = shelves[(index + 2) % len(shelves)]
            if other != place:
                stock.apply_movement(product=product_ids[name], warehouse=resolved["测试主仓"],
                                     location=other, movement_type="stock_in",
                                     qty=10 + index % 20, unit_cost=cost)
        if index % 4 == 1:
            stock.apply_movement(product=product_ids[name], warehouse=resolved["冷藏仓"],
                                 location=plan["冷藏仓"][index % 2], movement_type="stock_in",
                                 qty=15 + index % 40, unit_cost=cost)
    for index, name in enumerate(("大米", "食用油")):
        stock.apply_movement(product=product_ids[name], warehouse=resolved["备用仓"],
                             location="G架", movement_type="stock_in",
                             qty=30 + index * 12, unit_cost=10)
    return resolved


def seed_tasks(product_ids, warehouse_ids, counts):
    if not frappe.db.exists("ERP Stock Count", {"title": "月底盘点"}):
        task = counts.create_count(title="月底盘点", warehouse=warehouse_ids["测试主仓"])["name"]
        rows = counts.list_items(count=task, limit=0)
        for index, row in enumerate(rows):
            if index % 9 == 5:
                continue  # 留几行未盘点：图上不该出现、表里要能筛出来
            delta = DELTAS[index % len(DELTAS)]
            qty = max(0, float(row["book_qty"]) + delta)
            # 每 7 行模拟"放错货架"：账面在 B架、实盘在 D架。
            found = row["location"]
            if index % 7 == 3 and row["location"] != "D架":
                found = "D架"
            counts.add_entry(count=task, item=row["name"], qty=qty, location=found,
                             request_id=f"demo-chart-main-{index + 1}")
    if not frappe.db.exists("ERP Stock Count", {"title": "冷藏区抽盘"}):
        task = counts.create_count(title="冷藏区抽盘", warehouse=warehouse_ids["冷藏仓"])["name"]
        for index, row in enumerate(counts.list_items(count=task, limit=0)):
            if index % 4 == 3:
                continue
            counts.add_entry(count=task, item=row["name"],
                             qty=max(0, float(row["book_qty"]) + DELTAS[(index + 4) % len(DELTAS)]),
                             request_id=f"demo-chart-cold-{index + 1}")
    # 一份已经结束的盘点：工作台的只读态（导出 / 刷新）也要能看。
    if not frappe.db.exists("ERP Stock Count", {"title": "备用仓快盘"}):
        task = counts.create_count(title="备用仓快盘", warehouse=warehouse_ids["备用仓"])["name"]
        for index, row in enumerate(counts.list_items(count=task, limit=0)):
            counts.add_entry(count=task, item=row["name"], qty=float(row["book_qty"]),
                             request_id=f"demo-chart-standby-{index + 1}")
        review = counts.review_count(task)
        counts.close_count(task, review_token=review["review_token"])


if __name__ == "__main__":
    main()
