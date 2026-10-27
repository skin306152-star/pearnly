"""多仓闭环 P1 服务端实测（回归实验室）：多仓公司里单头「仓库 + 库位」怎么带明细、服务端拒什么、预填建议什么、建商品带期初是否同一事务。
全部走正式接口、用真实岗位账号（采购 / 销售 / 库存岗只开 A 公司与新建的多仓公司 M），每条记「预期 / 实际 / 通过」。可重复跑。

    ../env/bin/python /workspace/scripts/regression/lab_check_p1.py erp.regression.localhost <夹具 JSON 文件>
"""
import json
import os
import sys
import uuid

if os.environ.get("PEARNLY_REGRESSION_LAB") != "1":
    raise SystemExit("Requires the task-owned regression laboratory")

import frappe  # noqa: E402

ADMIN, BUYER, SELLER, KEEPER, NOBODY = (f"{job}@erp.regression" for job in ("admin", "purchase", "sales", "stock", "none"))
RESULTS = []


def case(name, expect, fn):
    """跑一条：expect = "ok" 或 "error"；结果与报错原文都记下。"""
    try:
        detail = fn()
        actual = "ok"
    except Exception as error:  # noqa: BLE001 - 每条都要记结果
        frappe.db.rollback()
        detail, actual = f"{type(error).__name__}: {str(error)[:160]}", "error"
    passed = actual == expect if not isinstance(detail, dict) or "pass" not in detail else (actual == expect and detail["pass"])
    RESULTS.append({"case": name, "expect": expect, "actual": actual, "pass": passed, "detail": detail})
    print(json.dumps(RESULTS[-1], ensure_ascii=False, default=str), flush=True)


def main():
    site, fixture = sys.argv[1], json.loads(open(sys.argv[2]).read().strip().splitlines()[-1])
    frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    try:
        from pearnly_erp_portal.inventory import locations, stock, warehouses
        from pearnly_erp_portal.masters import company
        from pearnly_erp_portal.products import service as products
        from pearnly_erp_portal.purchasing import entry as purchase
        from pearnly_erp_portal.sales import entry as sales
        from pearnly_erp_portal.inventory import documents
        from pearnly_erp_portal.team import service as team

        tag = uuid.uuid4().hex[:4]
        frappe.set_user(ADMIN)
        m = company.create_company(company_name=f"บริษัท หลายคลัง {tag} จำกัด", warehouses=[f"คลังหนึ่ง {tag}", f"คลังสอง {tag}"],
                                   payment_methods=["cash"])
        w1, w2 = m["warehouses"][0]["name"], m["warehouses"][1]["name"]
        company.set_active_company(m["name"])  # 库位清单按当前公司认仓
        locations.api_save_locations(warehouse=w1, rows=[{"location_name": "A-01"}, {"location_name": "A-02"}])
        a, b = fixture["company_a"], fixture["company_b"]
        b_warehouse = frappe.db.get_value("ERP Warehouse", {"company": b})
        product = products.create_product(product_name=f"สินค้าหลายคลัง {tag}", code=f"MW{tag}", unit="ชิ้น", unit_price=50,
                                          default_cost=20)["name"]
        supplier = frappe.db.get_value("ERP Supplier", {"supplier_name": ["like", "ผู้ขายทดสอบ%"]})
        customer = frappe.db.get_value("ERP Customer", {"customer_name": ["like", "ลูกค้าทดสอบ%"]})
        for user in (BUYER, SELLER, KEEPER):
            team.set_member_companies(user, companies=[a, m["name"]])
        frappe.db.commit()

        def qty(warehouse, location=""):
            return float(stock.current_balance(product, warehouse, location).qty or 0)

        def buy(user, kind="purchase_invoice", **head):
            frappe.set_user(user)
            company.set_active_company(head.pop("company", m["name"]))
            lines = head.pop("lines", None) or [{"product": product, "item_type": "goods", "description": "สินค้าหลายคลัง", "qty": 3, "unit": "ชิ้น", "unit_price": 20}]
            payload = {"company": m["name"], "supplier": supplier, "doc_date": "2026-10-27", "currency": "THB", "fx_rate": 1,
                       "has_vat": 0, "doc_kind": kind, "doc_no": f"P1-{tag}-{uuid.uuid4().hex[:4]}", "lines": lines, **head}
            if kind == "purchase_invoice":
                payload["update_stock"] = 1
            name = purchase.post(payload, request_id=str(uuid.uuid4()))["name"]
            frappe.db.commit()
            doc = frappe.get_doc("Purchase Document", name)
            return {"name": name, "head": [doc.get("warehouse"), doc.get("location")],
                    "lines": [[row.warehouse, row.location] for row in doc.lines]}

        # 1 单头仓带明细、入库落单头仓
        def c1():
            before = qty(w2)
            out = buy(BUYER, warehouse=w2)
            out["stock_w2_delta"] = qty(w2) - before
            out["pass"] = out["lines"] == [[w2, ""]] and out["stock_w2_delta"] == 3
            return out
        case("采购发票：单头仓 W2、行不写仓 → 行落 W2、W2 入库 3", "ok", c1)

        # 2 行上写了别的仓不被单头覆盖
        def c2():
            before1, before2 = qty(w1), qty(w2)
            out = buy(BUYER, warehouse=w2, lines=[
                {"product": product, "item_type": "goods", "description": "สินค้าหลายคลัง", "qty": 1, "unit": "ชิ้น", "unit_price": 20, "warehouse": w1},
                {"product": product, "item_type": "goods", "description": "สินค้าหลายคลัง", "qty": 2, "unit": "ชิ้น", "unit_price": 20}])
            out["delta"] = [qty(w1) - before1, qty(w2) - before2]
            out["pass"] = out["lines"] == [[w1, ""], [w2, ""]] and out["delta"] == [1, 2]
            return out
        case("采购发票：单头 W2、第 1 行写 W1 → 第 1 行留 W1，各自入库", "ok", c2)

        case("采购发票：多仓公司单头、明细都不写仓 → 拒", "error", lambda: buy(BUYER))
        case("采购发票：单头仓是别家公司的 → 拒", "error", lambda: buy(BUYER, warehouse=b_warehouse))

        def c5():
            before = qty(w1, "A-01")
            out = buy(BUYER, warehouse=w1, location="A-01")
            out["delta"] = qty(w1, "A-01") - before
            out["pass"] = out["head"] == [w1, "A-01"] and out["lines"] == [[w1, "A-01"]] and out["delta"] == 3
            return out
        case("采购发票：单头 W1 + 库位 A-01 → 行带 A-01、入到 A-01", "ok", c5)
        case("采购发票：单头库位不在该仓清单（W1 + ZZ）→ 拒", "error", lambda: buy(BUYER, warehouse=w1, location="ZZ-99"))

        case("采购订单：单头库位不在该仓清单 → 拒", "error",
             lambda: buy(BUYER, kind="purchase_order", warehouse=w1, location="ZZ-99"))

        def c8():
            frappe.set_user(BUYER)
            return {"prefill": warehouses.api_prefill(kind="purchase", company=m["name"]),
                    "pass": warehouses.api_prefill(kind="purchase", company=m["name"]).get("warehouse") in (w1, w2)}
        case("预填：采购岗在 M 建采购单 → 本人上次选的仓", "ok", c8)

        def c9():
            frappe.set_user(SELLER)
            return {"prefill": warehouses.api_prefill(kind="sales", company=m["name"]),
                    "pass": warehouses.api_prefill(kind="sales", company=m["name"]).get("warehouse") == ""}
        case("预填：销售岗在多仓 M、没选过 → 空（必须选）", "ok", c9)

        def c10():
            frappe.set_user(BUYER)
            return warehouses.api_prefill(kind="purchase", company=b)
        case("预填：采购岗问没开给他的 B 公司 → 拒", "error", c10)

        def c11():
            frappe.set_user(NOBODY)
            return warehouses.api_prefill(kind="purchase", company=a)
        case("预填：无岗位账号 → 拒", "error", c11)

        def c12():
            frappe.set_user(SELLER)
            company.set_active_company(m["name"])
            before = qty(w1, "A-01")
            name = sales.post({"company": m["name"], "customer": customer, "doc_date": "2026-10-27", "has_vat": 0, "source": "manual",
                               "doc_kind": "receipt", "warehouse": w1, "location": "A-01", "paid_amount": 50, "payment_method": "cash",
                               "payment_date": "2026-10-27",
                               "lines": [{"product": product, "description": "สินค้าหลายคลัง", "qty": 1, "unit": "ชิ้น", "unit_price": 50}]},
                              request_id=str(uuid.uuid4()))["name"]
            frappe.db.commit()
            doc = frappe.get_doc("ERP Sales Document", name)
            delta = qty(w1, "A-01") - before
            return {"name": name, "head": [doc.get("warehouse"), doc.get("location")],
                    "lines": [[row.warehouse, row.location] for row in doc.lines], "delta": delta,
                    "pass": [[row.warehouse, row.location] for row in doc.lines] == [[w1, "A-01"]] and delta == -1}
        case("销售单：单头 W1 + A-01 → 行带、从 A-01 出 1", "ok", c12)

        def c13():
            frappe.set_user(SELLER)
            company.set_active_company(m["name"])
            return sales.post({"company": m["name"], "customer": customer, "doc_date": "2026-10-27", "has_vat": 0, "source": "manual",
                               "doc_kind": "receipt", "paid_amount": 50, "payment_method": "cash", "payment_date": "2026-10-27",
                               "lines": [{"product": product, "description": "สินค้าหลายคลัง", "qty": 1, "unit": "ชิ้น", "unit_price": 50}]},
                              request_id=str(uuid.uuid4()))
        case("销售单：多仓公司单头、明细都不写仓 → 拒", "error", c13)

        def c13b():
            frappe.set_user(SELLER)
            company.set_active_company(m["name"])
            name = sales.post({"company": m["name"], "customer": customer, "doc_date": "2026-10-27", "has_vat": 0, "source": "manual",
                               "doc_kind": "receipt", "warehouse": w1, "location": "ZZ-99", "paid_amount": 50, "payment_method": "cash",
                               "payment_date": "2026-10-27",
                               "lines": [{"product": product, "description": "สินค้าหลายคลัง", "qty": 1, "unit": "ชิ้น", "unit_price": 50}]},
                              request_id=str(uuid.uuid4()))["name"]
            frappe.db.commit()
            return {"name": name, "balance_ZZ": qty(w1, "ZZ-99"), "pass": False,
                    "note": "从清单外库位出货被收下（复核建议 4：单头库位只在入库时按清单核）"}
        case("销售单：单头库位不在该仓清单（W1 + ZZ-99）", "error", c13b)

        def c13c():
            frappe.set_user(KEEPER)
            company.set_active_company(m["name"])
            draft = documents.create_entry(direction="in", posting_date="2026-10-27", warehouse=w1, location="A-01",
                                           lines=[{"product": product, "qty": 1, "unit": "ชิ้น"}])["name"]
            documents.update_entry(draft, warehouse=w2, lines=[{"product": product, "qty": 1, "unit": "ชิ้น"}])
            frappe.db.commit()
            doc = frappe.get_doc("ERP Stock Entry", draft)
            head = [doc.warehouse, doc.get("location")]
            return {"name": draft, "head": head, "lines": [[r.get("warehouse"), r.location] for r in doc.lines],
                    "pass": head == [w2, ""], "note": "只改仓不传库位时，单头库位应清空（复核建议 3）"}
        case("入库草稿：只把仓从 W1 改成 W2 → 单头库位清空", "ok", c13c)

        def c14():
            frappe.set_user(KEEPER)
            company.set_active_company(m["name"])
            out = documents.create_entry(direction="in", posting_date="2026-10-27", warehouse=w1, location="A-02",
                                         lines=[{"product": product, "qty": 4, "unit": "ชิ้น"}])
            frappe.db.commit()
            doc = frappe.get_doc("ERP Stock Entry", out["name"])
            pre = warehouses.api_prefill(kind="stock_in", company=m["name"])
            return {"name": doc.name, "head": [doc.warehouse, doc.get("location")], "lines": [[r.get("warehouse"), r.location] for r in doc.lines],
                    "prefill_after": pre, "pass": doc.get("location") == "A-02" and all(r.location == "A-02" for r in doc.lines)
                    and pre.get("warehouse") == w1}
        case("入库单：单头 W1 + A-02 → 行带 A-02；之后预填入库 = W1（本人上次）", "ok", c14)

        def c15():
            frappe.set_user(ADMIN)
            company.set_active_company(m["name"])
            name = products.create_product(product_name=f"สินค้าพร้อมยอดยกมา {tag}", code=f"OP{tag}", unit="ชิ้น", unit_price=10,
                                           opening=[{"warehouse": w1, "location": "A-01", "qty": 5, "unit_cost": 4},
                                                    {"warehouse": w2, "qty": 2, "unit_cost": 4}])["name"]
            frappe.db.commit()
            got = [float(stock.current_balance(name, w1, "A-01").qty), float(stock.current_balance(name, w2, "").qty)]
            return {"product": name, "balances": got, "pass": got == [5, 2]}
        case("建商品带期初：W1/A-01 放 5、W2 放 2 → 两处都有", "ok", c15)

        def c16():
            frappe.set_user(ADMIN)
            company.set_active_company(m["name"])
            code = f"OX{tag}"
            try:
                products.create_product(product_name=f"สินค้าผิดคลัง {tag}", code=code, unit="ชิ้น", unit_price=10,
                                        opening=[{"warehouse": w1, "location": "A-01", "qty": 5, "unit_cost": 4},
                                                 {"warehouse": w1, "location": "ZZ-99", "qty": 1, "unit_cost": 4}])
            finally:
                frappe.db.rollback()
            return {"left_behind": frappe.db.exists("ERP Product", {"code": code})}
        case("建商品带期初：一行库位不在清单 → 整件不建", "error", c16)
        leftover = frappe.db.exists("ERP Product", {"code": f"OX{tag}"})
        RESULTS.append({"case": "建商品带期初失败后商品没留下", "expect": "none", "actual": leftover or "none", "pass": not leftover,
                        "detail": ""})
        print(json.dumps(RESULTS[-1], ensure_ascii=False), flush=True)

        def _single(company_name):
            frappe.set_user(BUYER)
            company.set_active_company(company_name)
            sole = frappe.db.get_value("ERP Warehouse", {"company": company_name, "is_active": 1})
            lines = [{"product": product, "item_type": "goods", "description": "สินค้าหลายคลัง", "qty": 1, "unit": "ชิ้น", "unit_price": 20}]
            name = purchase.post({"company": company_name, "supplier": supplier, "doc_date": "2026-10-27", "currency": "THB", "fx_rate": 1,
                                  "has_vat": 0, "doc_kind": "purchase_invoice", "update_stock": 1, "doc_no": f"P1S-{uuid.uuid4().hex[:6]}",
                                  "lines": lines}, request_id=str(uuid.uuid4()))["name"]
            frappe.db.commit()
            doc = frappe.get_doc("Purchase Document", name)
            return {"name": name, "lines": [r.warehouse for r in doc.lines], "pass": [r.warehouse for r in doc.lines] == [sole]}
        case("单仓公司 A：采购发票不写仓 → 落唯一仓", "ok", lambda: _single(a))
        print("SUMMARY", json.dumps({"pass": sum(1 for r in RESULTS if r["pass"]), "total": len(RESULTS),
                                     "company_m": m["name"], "w1": w1, "w2": w2, "product": product}, ensure_ascii=False))
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main()
