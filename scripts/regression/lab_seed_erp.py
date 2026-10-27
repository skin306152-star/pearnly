"""回归实验室 ERP 业务夹具：两家公司（A / B）与各类单据都走系统正式接口建（金额、税、单号由系统算 / 发），供打印入口、
页面冒烟与接口矩阵（B 节第二步：跨公司）用。岗位账号除管理员外只开 A 公司，所以 B 的单就是「别家公司的单」。

岗位账号走团队管理的正式接口改成真实岗位（`set_member_roles`：「ERP Menu <菜单>」角色 + 只开 A 公司）。

- A：采购岗位自己开的采购发票（入库，备货）、采购订单（已提交 / 作废），销售岗位自己开的报价单、销售单（已提交 / 作废），
  管理员开的一张采购订单（同公司「同事的单」）；收银员自己开班、卖一张、退一张、交班；
  盘点一张进行中、一张已过账。
- B：采购订单、销售单；管理员开班卖一张、交班。

只在任务自有的回归实验室跑（PEARNLY_REGRESSION_LAB=1），可重复跑（每次加一批，名字带批次号）。最后一行 JSON 是各单名字：

    ../env/bin/python /workspace/scripts/regression/lab_seed_erp.py erp.regression.localhost
"""
import json
import os
import sys
import uuid

if os.environ.get("PEARNLY_REGRESSION_LAB") != "1":
    raise SystemExit("Requires the task-owned regression laboratory")

import frappe  # noqa: E402

ADMIN = "admin@erp.regression"
# 各岗位勾的菜单（团队管理里真实发的是「ERP Menu <菜单>」角色，见 `team.service.set_member_roles`）
JOBS = {"products": ("products", "profile"),
        "stock": ("inventory", "stock-in", "stock-out", "stock-card", "stock-count", "warehouses", "profile"),
        "purchase": ("purchase", "suppliers", "profile"), "sales": ("sales", "clients", "profile"),
        "cashier": ("cashier", "pos-receipts", "pos-shifts", "profile"), "tasks": ("tasks", "profile")}


def main():
    site = sys.argv[1]
    frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    try:
        assert frappe.conf.get("pearnly_product") == "erp"
        from pearnly_erp_portal.cashier import sales as pos, shifts
        from pearnly_erp_portal.inventory import count
        from pearnly_erp_portal.masters import company, customers
        from pearnly_erp_portal.products import service as products
        from pearnly_erp_portal.purchasing import entry as purchase, suppliers
        from pearnly_erp_portal.sales import entry as sales
        from pearnly_erp_portal.team import service as team

        batch = uuid.uuid4().hex[:4]
        out = {"batch": batch}

        def commit(result):
            frappe.db.commit()
            return result

        frappe.set_user(ADMIN)
        a = commit(company.create_company(company_name=f"บริษัท ทดสอบ เอ {batch} จำกัด", tax_id="0105561000003",
                                          payment_methods=["cash", "transfer"]))
        b = commit(company.create_company(company_name=f"บริษัท ทดสอบ บี {batch} จำกัด", tax_id="0105561000011",
                                          payment_methods=["cash"]))
        out.update(company_a=a["name"], company_b=b["name"])
        goods = [commit(products.create_product(product_name=f"สินค้าทดสอบ {batch}-{i}", code=f"RG{batch}{i}", unit="ชิ้น",
                                                unit_price=price, default_cost=price / 2))["name"]
                 for i, price in enumerate((120, 45.5, 300), 1)]
        supplier = commit(suppliers.create_supplier(supplier_name=f"ผู้ขายทดสอบ {batch}", tax_id="0105556000122"))["name"]
        customer = commit(customers.create_customer(customer_name=f"ลูกค้าทดสอบ {batch}", tax_id="0105556000459"))["name"]
        from pearnly_erp_portal.access import menu

        for job, keys in JOBS.items():
            commit(team.set_member_roles(f"{job}@erp.regression", roles=[menu.role(key) for key in keys], companies=[a["name"]]))

        def lines(place, qty=1):
            return [{"product": p, "item_type": "goods", "description": f"สินค้าทดสอบ {batch}-{i}", "qty": qty * i,
                     "unit": "ชิ้น", "unit_price": price, "vat_rate": 7, "warehouse": place, "location": ""}
                    for i, (p, price) in enumerate(zip(goods, (120, 45.5, 300)), 1)]

        def as_user(user, place):
            frappe.set_user(user)
            company.set_active_company(place["name"])

        def buy(place, kind, user=ADMIN, **extra):
            as_user(user, place)
            payload = {"company": place["name"], "supplier": supplier, "doc_date": "2026-10-27", "currency": "THB",
                       "fx_rate": 1, "has_vat": 1, "price_mode": "exclusive", "doc_kind": kind,
                       "doc_no": f"RG-{batch}-{uuid.uuid4().hex[:4]}", "lines": lines(place["warehouse"], 10 if kind == "purchase_invoice" else 1),
                       **extra}
            return commit(purchase.post(payload, request_id=str(uuid.uuid4())))["name"]

        def sell(place, kind, user=ADMIN, **extra):
            as_user(user, place)
            payload = {"company": place["name"], "customer": customer, "doc_date": "2026-10-27", "has_vat": 1, "vat_rate": 7,
                       "source": "manual", "doc_kind": kind,
                       "lines": [{**row, "vat_applicable": 1} for row in lines(place["warehouse"])], **extra}
            return commit(sales.post(payload, request_id=str(uuid.uuid4())))["name"]

        # 员工只看得到自己开的单（`access.rules`：管理员看全部，其余本人）：A 的单由采购 / 销售岗位自己开，B 的单与「同事的单」由管理员开
        buyer, seller = "purchase@erp.regression", "sales@erp.regression"
        out["stock_in_a"] = buy(a, "purchase_invoice", buyer, update_stock=1)
        out["purchase_order_a"] = buy(a, "purchase_order", buyer)
        out["purchase_order_a_admin"] = buy(a, "purchase_order")
        out["sales_a"] = sell(a, "receipt", seller, paid_amount=100, payment_method="cash", payment_date="2026-10-27")
        out["stock_in_b"] = buy(b, "purchase_invoice", update_stock=1)
        out["purchase_order_b"] = buy(b, "purchase_order")
        out["sales_b"] = sell(b, "receipt", paid_amount=100, payment_method="cash", payment_date="2026-10-27")
        out["purchase_order_a_void"] = buy(a, "purchase_order", buyer)
        commit(purchase.cancel(out["purchase_order_a_void"], reason="regression"))
        out["quotation_a"] = sell(a, "quotation", seller)
        out["sales_a_void"] = sell(a, "receipt", seller, paid_amount=100, payment_method="cash", payment_date="2026-10-27")
        commit(sales.cancel(out["sales_a_void"], reason="regression"))

        def till(place, cashier):
            frappe.set_user(cashier)
            company.set_active_company(place["name"])
            terminal = place["terminals"][0]
            current = shifts.current_shift(terminal=terminal)
            if current:
                commit(shifts.close_shift(current["shift"]["name"], counted_cash=current["expected_cash"]))
            shift = commit(shifts.open_shift(terminal=terminal, opening_float=500))["name"]
            sale = commit(pos.create_sale(lines=[{"product": goods[0], "qty": 2, "unit_price": 120},
                                                 {"product": goods[1], "qty": 1, "unit_price": 45.5}],
                                          payments=[{"method": "cash", "amount": 300}], shift=shift, terminal=terminal,
                                          client_uuid=str(uuid.uuid4())))["name"]
            return terminal, shift, sale

        _, shift, sale = till(a, "cashier@erp.regression")
        first = pos.get_sale(sale)["lines"][0]
        refund = commit(pos.refund(sale, lines=[{"original_line": first["name"], "qty": 1}],
                                   payments=[{"method": "cash", "amount": 120}], reason="ชำรุด",
                                   client_uuid=str(uuid.uuid4())))["name"]
        summary = shifts.summary(shift)
        commit(shifts.close_shift(shift, counted={"cash": summary["expected_cash"]}))
        out.update(pos_sale_a=sale, pos_refund_a=refund, shift_a=shift)
        _, shift_b, sale_b = till(b, ADMIN)
        commit(shifts.close_shift(shift_b, counted={"cash": shifts.summary(shift_b, own_only=False)["expected_cash"]}))
        out.update(pos_sale_b=sale_b, shift_b=shift_b)

        frappe.set_user(ADMIN)
        company.set_active_company(a["name"])
        out["count_open_a"] = commit(count.create_count(title=f"นับทดสอบ {batch} เปิด", warehouse=a["warehouse"]))["name"]
        posted = commit(count.create_count(title=f"นับทดสอบ {batch} ปิด", warehouse=a["warehouse"]))["name"]
        # 结束盘点至少要点过一件：第一件少点一个（出差异），第二件点对
        for product, qty in ((goods[0], 9), (goods[1], 20)):
            commit(count.add_entry(count=posted, product=product, qty=qty, warehouse=a["warehouse"], request_id=str(uuid.uuid4())))
        commit(count.close_count(posted, review_token=count.review_count(posted)["review_token"]))
        out["count_posted_a"] = posted
        print(json.dumps(out, ensure_ascii=False), flush=True)
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main()
