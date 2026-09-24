"""盘点预览用的标准演示数据（只写隔离的 stocktake.localhost）。

为什么要有它：图表只在"有已盘点行"时才画得出来，而实验室站点初始化后只有一个商品两行，
看不出分类、排序、12 组上限和长表。这里造一套**像正式主数据那样**的样本：

· 商品编码由系统发（`P-000001` 这种），条形码是校验位正确的 EAN-13（885 开头）；
· 商品按 6 个分类、不同单位铺开，货位用标准货位号（A-01 / R-01 / T-01 …）而不是"某架"；
· 三个仓（主仓 / 冷藏仓 / 退货仓）、三份盘点（一份月度大盘、一份周盘、一份已过账的复盘）；
· 差异有种类：大幅少货、大幅多货、微小差异、正好对上、未盘点、放错货位。

列表排序（2026-09-25 口径"列表也点列头排序"）要看得见效果，而客户 / 供应商这两张列表在
演示站点初始化后是空的（一行都没有），所以这里一并铺一批**像正式主数据**的名录。

它同时**收回本任务上一版造的演示数据**（编码前缀 `DEMO-CHART-` 的那批商品与两份进行中盘点：
先把结存清掉再停用），免得同一套预览里混着两批口径不同的样本。别的任务留下的站点数据
（验收仓库那几份）不动。

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
OWNER = "stocktake-owner@example.com"
RETIRED_CODE_PREFIX = "DEMO-CHART-"
RETIRED_TASKS = ("月底盘点", "冷藏区抽盘")
MAIN_TASK = "2026-09 月度盘点"

# 分类只作商品档案里的一个编号（`ERP Product.category_id` 是整数，旧系统同形）：
# 1 饮料 · 2 调味 · 3 粮油 · 4 清洁 · 5 日用 · 6 冷藏。
WAREHOUSE_SHELVES = {
    "主仓": ["A-01", "A-02", "A-03", "B-01", "B-02", "B-03", "C-01", "C-02"],
    "冷藏仓": ["R-01", "R-02", "R-03"],
    "退货仓": ["T-01", "T-02"],
}

# 名录类演示数据：客户 / 供应商这两张列表本来是空的，排序点下去也看不出效果。
# (客户名, 纳税号, 电话, 地址)
DEMO_CUSTOMERS = [
    ("บริษัท สยามเทรดดิ้ง จำกัด", "0105558001001", "02-123-4567", "88/12 ถ.สุขุมวิท กรุงเทพฯ"),
    ("บริษัท นครพาณิชย์ จำกัด", "0105558002002", "02-234-5678", "129 ถ.พหลโยธิน กรุงเทพฯ"),
    ("ร้านเจริญพานิช", "1103700112233", "081-234-5678", "45/7 ต.บางรัก อ.เมือง ชลบุรี"),
    ("บริษัท ไทยฟู้ดส์ เซอร์วิส จำกัด", "0105558003003", "02-345-6789", "500 ถ.บางนา-ตราด สมุทรปราการ"),
    ("บริษัท เชียงใหม่มาร์ท จำกัด", "0505558004004", "053-123-456", "77 ถ.นิมมานเหมินท์ เชียงใหม่"),
    ("ร้านป้าแดงของชำ", "", "089-111-2222", "12/3 ต.ในเมือง อ.เมือง ขอนแก่น"),
    ("บริษัท ภูเก็ตซีฟู้ด จำกัด", "0805558005005", "076-234-567", "9/1 ถ.เทพกระษัตรี ภูเก็ต"),
    ("บริษัท อีสานค้าส่ง จำกัด", "0405558006006", "043-345-678", "222 ถ.มิตรภาพ ขอนแก่น"),
]

# (供应商名, 纳税号, 电话, 地址)
DEMO_SUPPLIERS = [
    ("บริษัท เครื่องดื่มไทย จำกัด", "0105559001001", "02-111-2222", "31 ถ.รามคำแหง กรุงเทพฯ"),
    ("บริษัท เครื่องปรุงรสไทย จำกัด", "0105559002002", "02-222-3333", "88 ถ.ลาดพร้าว กรุงเทพฯ"),
    ("หจก. ข้าวสารเจริญทรัพย์", "1103700445566", "081-333-4444", "19/5 ต.ท่าศาลา อ.เมือง นครศรีธรรมราช"),
    ("บริษัท น้ำมันพืชไทย จำกัด", "0105559003003", "02-333-4444", "450 นิคมฯ บางปู สมุทรปราการ"),
    ("บริษัท ของใช้ในบ้าน จำกัด", "0105559004004", "02-444-5555", "77/9 ถ.เพชรเกษม นครปฐม"),
    ("บริษัท อาหารสดตะวันออก จำกัด", "0205559005005", "038-555-666", "25 ถ.สุขุมวิท ระยอง"),
]

# (名称, 单位, 售价, 参考成本, 分类)
PRODUCTS = [
    ("矿泉水 600ml", "瓶", 7, 4, 1), ("苏打水 325ml", "瓶", 12, 7, 1),
    ("可乐 325ml", "瓶", 15, 9, 1), ("橙汁 1L", "瓶", 45, 28, 1),
    ("椰子水 330ml", "瓶", 25, 15, 1), ("豆奶 300ml", "瓶", 14, 8, 1),
    ("运动饮料 500ml", "瓶", 20, 12, 1), ("即饮咖啡 180ml", "罐", 22, 13, 1),
    ("啤酒 320ml", "瓶", 32, 20, 1), ("气泡水 500ml", "瓶", 18, 10, 1),
    ("生抽 700ml", "瓶", 42, 26, 2), ("老抽 700ml", "瓶", 45, 28, 2),
    ("蚝油 500g", "瓶", 55, 34, 2), ("鱼露 700ml", "瓶", 48, 30, 2),
    ("白醋 500ml", "瓶", 26, 15, 2), ("陈醋 500ml", "瓶", 32, 19, 2),
    ("辣椒酱 250g", "瓶", 38, 22, 2), ("番茄酱 300g", "瓶", 35, 20, 2),
    ("味精 200g", "袋", 18, 10, 2), ("鸡精 200g", "袋", 26, 15, 2),
    ("白砂糖 1kg", "袋", 32, 19, 2), ("食盐 500g", "袋", 12, 6, 2),
    ("花椒粉 50g", "袋", 22, 13, 2), ("咖喱粉 100g", "袋", 36, 21, 2),
    ("茉莉香米 5kg", "袋", 235, 175, 3), ("糯米 1kg", "袋", 62, 42, 3),
    ("面粉 1kg", "袋", 45, 29, 3), ("玉米油 1L", "瓶", 78, 52, 3),
    ("棕榈油 1L", "瓶", 65, 43, 3), ("花生 500g", "袋", 58, 38, 3),
    ("绿豆 500g", "袋", 42, 27, 3), ("红豆 500g", "袋", 44, 28, 3),
    ("方便面 5包", "包", 55, 36, 3), ("意大利面 500g", "包", 62, 40, 3),
    ("洗衣液 2L", "瓶", 128, 86, 4), ("洗衣粉 1kg", "袋", 68, 44, 4),
    ("洗洁精 1L", "瓶", 52, 33, 4), ("洁厕剂 700ml", "瓶", 58, 37, 4),
    ("玻璃清洁剂 600ml", "瓶", 62, 40, 4), ("消毒液 1L", "瓶", 88, 57, 4),
    ("漂白水 1L", "瓶", 48, 30, 4),
    ("沐浴露 450ml", "瓶", 118, 74, 5), ("洗发水 400ml", "瓶", 145, 92, 5),
    ("护发素 400ml", "瓶", 138, 88, 5), ("牙膏 160g", "支", 62, 38, 5),
    ("牙刷 2支装", "包", 55, 33, 5), ("香皂 100g", "个", 25, 14, 5),
    ("洗手液 500ml", "瓶", 85, 54, 5), ("面巾纸 3包", "包", 52, 32, 5),
    ("卷纸 10卷", "提", 135, 92, 5), ("湿巾 80片", "包", 45, 27, 5),
    ("垃圾袋 30只", "卷", 38, 22, 5), ("保鲜膜 30m", "卷", 42, 25, 5),
    ("铝箔纸 10m", "卷", 58, 36, 5), ("一次性手套 50只", "盒", 96, 62, 5),
    ("口罩 50只", "盒", 88, 55, 5),
    ("鲜牛奶 1L", "瓶", 68, 45, 6), ("原味酸奶 4杯", "组", 55, 34, 6),
    ("黄油 200g", "盒", 128, 88, 6), ("芝士片 150g", "盒", 105, 70, 6),
    ("冷冻虾仁 500g", "袋", 215, 155, 6), ("冷冻鸡翅 1kg", "袋", 168, 118, 6),
    ("冰淇淋 1L", "桶", 185, 128, 6), ("香肠 500g", "袋", 132, 90, 6),
]

# 差异样本，按已盘点行循环取用：大幅少货、大幅多货、微小差异、正好对上混在一起。
DELTAS = [-48, 45, -12, 0, 62, -35, 8, -70, 26, 0, 41, -18, 15, -55, 3, 33,
          -22, 0, 58, -9, 19, -41, 7, 28, -63, 0, 12, -30, 47, -5]


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
    from pearnly_erp_portal.masters import customers
    from pearnly_erp_portal.products import service as products
    from pearnly_erp_portal.purchasing import suppliers

    try:
        retire_previous_demo(products, counts, stock)
        frappe.set_user("Administrator")
        directory = seed_directory(customers, suppliers)
        product_ids = seed_products(products)
        frappe.set_user(OWNER)
        warehouse_ids = seed_stock(product_ids, stock, warehouses)
        seed_tasks(product_ids, warehouse_ids, counts)
        frappe.db.commit()
    except Exception:
        # 整批一个事务：中途失败不留半份盘点（不然重跑会按标题跳过，留下缺记录的任务）。
        frappe.db.rollback()
        raise
    print(json.dumps({"products": len(product_ids), "counts": frappe.db.count("ERP Stock Count"),
                      **directory}, ensure_ascii=False))


def seed_directory(service_customers, service_suppliers) -> dict:
    """客户 / 供应商的名录演示数据（按业务键幂等，重复跑只补缺的那几条）。"""
    made = {"customers": 0, "suppliers": 0}
    for customer_name, tax_id, phone, address in DEMO_CUSTOMERS:
        if frappe.db.exists("ERP Customer", {"customer_name": customer_name}):
            continue
        service_customers.create_customer(customer_name=customer_name, tax_id=tax_id,
                                          contact_phone=phone, address=address)
        made["customers"] += 1
    for supplier_name, tax_id, phone, address in DEMO_SUPPLIERS:
        if frappe.db.exists("ERP Supplier", {"supplier_name": supplier_name}):
            continue
        service_suppliers.create_supplier(supplier_name=supplier_name, tax_id=tax_id,
                                          phone=phone, address=address)
        made["suppliers"] += 1
    return made


def retire_previous_demo(products, counts, stock):
    """收回本任务上一版的演示数据（只碰 `DEMO-CHART-` 前缀的商品与那两份进行中的盘点）。"""
    frappe.set_user(OWNER)
    for title in RETIRED_TASKS:
        task = frappe.db.get_value("ERP Stock Count", {"title": title, "status": "Active"}, "name")
        if task:
            counts.delete_count(task)
    for row in frappe.get_all("ERP Product", filters={"code": ["like", f"{RETIRED_CODE_PREFIX}%"]},
                              fields=["name"], limit_page_length=0):
        for balance in frappe.get_all("ERP Stock Balance", filters={"product": row["name"]},
                                      fields=["warehouse", "location", "qty_on_hand"],
                                      limit_page_length=0):
            qty = float(balance["qty_on_hand"] or 0)
            if qty:
                stock.apply_movement(product=row["name"], warehouse=balance["warehouse"],
                                     location=balance["location"] or "", movement_type="stock_out", qty=qty)
        products.set_active(row["name"], False)


def seed_products(service):
    out = {}
    for index, (name, unit, price, cost, category) in enumerate(PRODUCTS):
        existing = frappe.db.get_value("ERP Product", {"product_name": name, "is_active": 1}, "name")
        if existing:
            out[name] = existing
            continue
        # 编码交给系统发（`P-000001`），不手填；条码是校验位正确的 EAN-13。
        # 前缀用 8851：条码列是唯一索引，而上一版演示数据占着 8850 那一段（停用后仍然占号）。
        created = service.create_product(
            product_name=name, barcode=ean13(f"8851{index:08d}"), qr_payload=f"{name} QR",
            unit=unit, unit_price=price, default_cost=cost, category_id=category)
        out[name] = created["name"]
    return out


def ean13(twelve: str) -> str:
    """12 位数字 → 带校验位的 EAN-13。条码看起来要像真的，扫码/导入才不会一眼假。"""
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(twelve))
    return twelve + str((10 - total % 10) % 10)


def seed_stock(product_ids, stock, warehouses):
    resolved = {name: warehouses.resolve(name, create=True) for name in WAREHOUSE_SHELVES}
    for index, (name, _unit, _price, cost, category) in enumerate(PRODUCTS):
        product = product_ids[name]
        if category == 6:
            house, shelves = "冷藏仓", WAREHOUSE_SHELVES["冷藏仓"]
        elif index % 11 == 4:
            house, shelves = "退货仓", WAREHOUSE_SHELVES["退货仓"]  # 退回来的货也占库存
        else:
            house, shelves = "主仓", WAREHOUSE_SHELVES["主仓"]
        # 每 6 个商品留一个没有货位：老口径里的"未填"就是这个来源。
        place = None if index % 6 == 5 else shelves[index % len(shelves)]
        stock.apply_movement(product=product, warehouse=resolved[house], location=place,
                             movement_type="stock_in", qty=20 + (index * 7) % 180, unit_cost=cost)
        # 每 4 个商品在第二个货位再放一份：同一商品两行，图表按商品合并后的样子才看得见。
        if index % 4 == 2:
            other = shelves[(index + 3) % len(shelves)]
            if other != place:
                stock.apply_movement(product=product, warehouse=resolved[house], location=other,
                                     movement_type="stock_in", qty=12 + index % 24, unit_cost=cost)
    return resolved


def seed_tasks(product_ids, warehouse_ids, counts):
    # 每份盘点按**标题**各自幂等：只补缺的那一份。写成"主任务在就整体跳过"的话，
    # 中途删掉一份就再也补不回来（record_id 还是全局唯一的，重建时会撞上旧记录）。
    if not frappe.db.exists("ERP Stock Count", {"title": MAIN_TASK}):
        monthly = counts.create_count(title=MAIN_TASK, warehouse=warehouse_ids["主仓"],
                                      notes="月度全盘：主仓全部货位，含两处放错货位。")
        count_rows(monthly["name"], counts, request_prefix="std-monthly", move_every=9, skip_every=7)
    if not frappe.db.exists("ERP Stock Count", {"title": "冷藏仓周盘"}):
        weekly = counts.create_count(title="冷藏仓周盘", warehouse=warehouse_ids["冷藏仓"],
                                     notes="每周抽盘冷藏仓，重点看有效期与损耗。")
        count_rows(weekly["name"], counts, request_prefix="std-weekly", move_every=0, skip_every=5)
    # 一份已经过账的复盘：工作台的只读态（导出 / 刷新）也要能看。过账过的那份删不掉，
    # 所以这里只补缺（已存在就原样留着）。
    if not frappe.db.exists("ERP Stock Count", {"title": "退货仓复盘"}):
        back = counts.create_count(title="退货仓复盘", warehouse=warehouse_ids["退货仓"],
                                   notes="退货入账后的复核，数量按实际点清。")
        count_rows(back["name"], counts, request_prefix="std-returns", move_every=0, skip_every=0,
                   exact=True)
        review = counts.review_count(back["name"])
        counts.close_count(back["name"], review_token=review["review_token"])


def count_rows(task, counts, *, request_prefix, move_every, skip_every, exact=False):
    """按快照行录记录：`skip_every` 留未盘点，`move_every` 制造放错货位，`exact` 是完全一致。"""
    for index, row in enumerate(counts.list_items(count=task, limit=0)):
        if skip_every and index % skip_every == skip_every - 1:
            continue
        delta = 0 if exact else DELTAS[index % len(DELTAS)]
        qty = max(0, float(row["book_qty"]) + delta)
        found = row["location"]
        if move_every and index % move_every == move_every - 1:
            # 挑一个**和账面不同**的货位，并在几个候选里轮着取：不然演示数据里"实际"全是同一个位。
            others = [s for s in WAREHOUSE_SHELVES["主仓"] if s != row["location"]]
            found = others[(index // move_every) % len(others)]
        counts.add_entry(count=task, item=row["name"], qty=qty, location=found,
                         request_id=f"{request_prefix}-{index + 1}")


if __name__ == "__main__":
    main()
