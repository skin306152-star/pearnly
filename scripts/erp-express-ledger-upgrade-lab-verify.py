"""实验栈核对：已同步过的 Express 账套升级到「主档带只读来源 ledger」（公司接入第 3 批）后再同步一次，主档不整本重写（2026-11-15）。

升级真实发生的顺序是：旧代码落过的档没有 ``ledger`` → 迁移加列并跑 v1_30 回填 → 新代码再同步一次。实验栈分两步走：

    # 1. 实验室挂旧源码（origin/main）：用旧代码首次落库一套合成 Express 账套
    PEARNLY_REGRESSION_SRC=<main 工作树> docker compose -p <栈名> -f infrastructure/compose/regression-lab.yml up -d bench
    ... exec -T bench env/bin/python /workspace/scripts/erp-express-ledger-upgrade-lab-verify.py seed
    # 2. 换挂本分支源码，清掉 v1_30 的补丁记录后 bench migrate（同生产升级），再核
    ... exec -T bench env/bin/python /workspace/scripts/erp-express-ledger-upgrade-lab-verify.py verify
    ... exec -T bench env/bin/python /workspace/scripts/erp-express-ledger-upgrade-lab-verify.py cleanup

只在任务自己的回归实验室里跑（``PEARNLY_REGRESSION_LAB=1``），合成数据、自建一家 LAB 公司，不连外网、不调电脑。
seed 只用旧代码也有的接口（`express.companies.build`、`express.masters.apply`）。verify 核对：
1. 回填后这家公司带 Express 编码的客户 / 供应商 / 物料 / 仓库 ``ledger`` 全是 express，没有空的；
2. 同一份账套再落一次：各类 0 条改动，这几类的修改记录（Version）条数不变，各档 ``modified`` 一条不变；
3. 对照：把客户的 ``ledger`` 清空（= 没跑回填）再落一次，客户整本被改写——证明第 2 条确实拦得住「不回填就整本重写」；
4. 收款的客户选择框（`collection.api.api_collection_customer_search`）按编码、按名字照常搜得到这家公司的账本客户。
任何一项不过，退出码非 0。
"""
import json
import os
import sys

SITE = "erp.regression.localhost"
TAX = "0105599990211"
BOOK = "LAB-UPGRADE"
TABLES = ("ERP Customer", "ERP Supplier", "ERP Product", "ERP Warehouse")


def facts(customers=1500, suppliers=300, products=1500):
    return {
        "customers": [{"code": f"UC{i:05d}", "name": f"ลูกค้า อัปเกรด {i}", "tax_id": f"01055{i:08d}" if i % 3 == 0 else "",
                       "branch": "00000" if i % 3 == 0 else "", "address": f"{i} ถนนทดสอบ" if i % 5 else "",
                       "phone": f"08{10000000 + i}"} for i in range(customers)],
        "suppliers": [{"code": f"US{i:04d}", "name": f"ผู้ขาย อัปเกรด {i}", "tax_id": f"01075{i:08d}" if i % 2 else "",
                       "branch": "00000", "address": "", "phone": "021234567"} for i in range(suppliers)],
        "products": [{"code": f"UP{i:05d}", "name": f"สินค้า อัปเกรด {i}", "unit": "01", "sale_unit": "02" if i % 4 == 0 else "01",
                      "sale_factor": "12" if i % 4 == 0 else "1", "purchase_unit": "01", "purchase_factor": "1",
                      "prices": [str(10 + i % 50)] if i % 9 else [], "kind": "stock" if i % 5 else "service",
                      "barcode": f"886{i:010d}" if i % 3 else ""} for i in range(products)],
        "units": [{"code": "01", "name": "ชิ้น"}, {"code": "02", "name": "โหล"}],
        "warehouses": [{"code": "01", "name": "คลังหลัก"}, {"code": "02", "name": "คลังสาขา"}],
        "settings": {"mainloc": "01"}, "stock": [], "mark": "u1"}


def connect():
    if os.environ.get("PEARNLY_REGRESSION_LAB") != "1":
        raise SystemExit("This check requires the regression laboratory")
    import frappe

    frappe.init(site=SITE, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    frappe.set_user("Administrator")
    return frappe


def company(frappe):
    return frappe.db.get_value("ERP Company", {"tax_id": TAX}, "name")


def apply(frappe, name):
    from pearnly_erp_portal.express import masters

    counts = masters.apply(name, BOOK, facts())
    frappe.db.commit()
    return counts


def changed(counts) -> int:
    return sum(int(value) for value in counts.values() if isinstance(value, int))


def seed(frappe):
    from pearnly_erp_portal.express import companies

    name = company(frappe) or companies.build(TAX, "00000", "LAB EXPRESS UPGRADE")
    frappe.db.set_value("ERP Company", name, "express_enabled", 1)  # 同线上已接 Express、同步过的公司（收款选择框只认接 Express 的）
    frappe.db.commit()
    first = apply(frappe, name)
    again = apply(frappe, name)
    has = {doctype: frappe.db.has_column(doctype, "ledger") for doctype in TABLES}
    print(json.dumps({"company": name, "first": first, "again": again, "ledger_column": has}, default=str, ensure_ascii=False))
    return 0 if changed(again) == 0 else 1


def state(frappe, name):
    out = {}
    for doctype in TABLES:
        key = "company" if doctype == "ERP Warehouse" else "express_company"
        rows = frappe.get_all(doctype, filters={key: name, "express_code": ["is", "set"]},
                              fields=["name", "modified", "ledger"], limit_page_length=0)
        out[doctype] = {row.name: (str(row.modified), row.ledger) for row in rows}
    out["versions"] = frappe.db.count("Version", {"ref_doctype": ["in", list(TABLES)]})
    return out


def verify(frappe):
    name = company(frappe)
    if not name:
        raise SystemExit("seed first")
    checks = []
    before = state(frappe, name)
    blank = {doctype: sum(1 for _m, ledger in rows.values() if ledger != "express") for doctype, rows in before.items()
             if doctype != "versions"}
    sizes = {doctype: len(rows) for doctype, rows in before.items() if doctype != "versions"}
    checks.append(("backfilled", all(value == 0 for value in blank.values()) and all(sizes.values()), {"not_express": blank,
                                                                                                     "rows": sizes}))
    again = apply(frappe, name)
    after = state(frappe, name)
    touched = {doctype: sum(1 for key, value in before[doctype].items() if after[doctype].get(key) != value)
               for doctype in TABLES}
    checks.append(("resync changes nothing", changed(again) == 0 and not any(touched.values()),
                   {"counts": again, "modified_changed": touched}))
    checks.append(("no new versions", after["versions"] == before["versions"],
                   {"before": before["versions"], "after": after["versions"]}))
    from pearnly_erp_portal.collection import api
    from pearnly_erp_portal.express import push

    # 「接上」要 CC 桌面版报过这家的账套（`push.linked`）；实验栈没有电脑，只把这一个判断当真，选择框的查询照原样
    linked, push.linked = push.linked, lambda company: company == name
    try:
        by_code = api.api_collection_customer_search("UC00042", company=name)
        by_name = api.api_collection_customer_search("อัปเกรด 1234", company=name)
    finally:
        push.linked = linked
    checks.append(("collection customer picker", any(row["value"] == "UC00042" for row in by_code)
                   and any(row["value"] == "UC01234" for row in by_name),
                   {"by_code": by_code[:2], "by_name": by_name[:2]}))
    # 对照：不回填（客户的 ledger 空着）再落 → 客户整本改写
    frappe.db.sql("update `tabERP Customer` set ledger = null where express_company = %s", (name,))
    frappe.db.commit()
    control = apply(frappe, name)
    checks.append(("without backfill the customers are rewritten", int(control.get("customers") or 0) == sizes["ERP Customer"],
                   {"counts": control}))
    for label, ok, seen in checks:
        print(("ok   " if ok else "FAIL ") + label, json.dumps(seen, default=str, ensure_ascii=False))
    failed = sum(1 for _label, ok, _seen in checks if not ok)
    print("ALL OK" if not failed else f"FAILED {failed}")
    return 1 if failed else 0


def cleanup(frappe):
    name = company(frappe)
    if not name:
        return 0
    products = frappe.get_all("ERP Product", filters={"express_company": name}, pluck="name", limit_page_length=0)
    for chunk in range(0, len(products), 1000):
        frappe.db.delete("ERP Product Unit", {"parent": ["in", products[chunk:chunk + 1000]]})
    for doctype in ("ERP Customer", "ERP Supplier", "ERP Product"):
        frappe.db.delete(doctype, {"express_company": name})
    frappe.db.delete("ERP Warehouse", {"company": name})
    frappe.db.delete("ERP Company", {"name": name})
    frappe.db.commit()
    print("cleaned", name)
    return 0


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else ""
    if step not in ("seed", "verify", "cleanup"):
        raise SystemExit("usage: seed | verify | cleanup")
    sys.exit(globals()[step](connect()))
