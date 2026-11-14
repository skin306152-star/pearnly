"""实验栈核对：主档同步首次落库的批量写（`express/bulk.py`）与逐条 ``doc.insert`` 落库结果逐字段一样（公司接入重做第 1 批，2026-11-14）。

只在任务自己的回归实验室里跑（``PEARNLY_REGRESSION_LAB=1``），合成数据、自建三家 LAB 公司，跑完把这几家公司和它们的主档删掉。
不连外网、不读文件、不调识别。

    docker compose -p <栈名> -f infrastructure/compose/regression-lab.yml exec -T bench \\
        env/bin/python /workspace/scripts/erp-masters-bulk-lab-verify.py [--site erp.regression.localhost] [--customers 3000]

核对：
1. 同一份合成账套，一家公司照逐条写（批量写换成逐条那条退路）、另一家照批量写：客户 / 供应商 / 物料 / 物料单位子表，
   除了名字、时间、操作人、归属公司（和含公司的 ``code_key``）以外的每一列都相等；
2. 批量写拿到的编号互不相同、不和逐条写的撞号，之后网页上照常新建一条档（原生 ``insert``）拿到的号接在后面；
3. 两本账套（两家公司）在两个进程里同时首次落库：都落全、编号不撞、没有出错行；
4. 落完再用同一份账套落一次：0 条改动；换一个账套名再落一次：0 条改动（档上的 ``express_book`` 不比对）。
最后打印各段耗时。任何一项不过，退出码非 0。
"""
import argparse
import json
import os
import random
import subprocess
import sys
import time

SITE = "erp.regression.localhost"
TAXES = {"rows": "0105599990001", "bulk": "0105599990002", "twin_a": "0105599990003", "twin_b": "0105599990004"}
SKIP = {"name", "creation", "modified", "owner", "modified_by", "express_company", "code_key", "parent", "_user_tags",
        "_comments", "_assign", "_liked_by"}
TABLES = ("ERP Customer", "ERP Supplier", "ERP Product")


def facts(customers, suppliers, products):
    rnd = random.Random(11)
    return {
        "customers": [{"code": f"C{i:06d}", "name": f"ลูกค้า ทดสอบ {i}", "tax_id": f"01055{i:08d}" if i % 3 == 0 else "",
                       "branch": "00000" if i % 6 == 0 else ("00002" if i % 3 == 0 else ""),
                       "address": f"{i} ถนนทดสอบ" if i % 5 else "", "phone": f"08{rnd.randint(10000000, 99999999)}"}
                      for i in range(customers)],
        "suppliers": [{"code": f"S{i:05d}", "name": f"ผู้ขาย {i}", "tax_id": f"01075{i:08d}" if i % 2 else "",
                       "branch": "00000" if i % 4 else "00003", "address": "", "phone": "021234567"} for i in range(suppliers)],
        "products": [{"code": f"P{i:05d}", "name": f"สินค้า {i}", "unit": "01", "sale_unit": "02" if i % 4 == 0 else "01",
                      "sale_factor": "12" if i % 4 == 0 else "1", "purchase_unit": "03" if i % 7 == 0 else "01",
                      "purchase_factor": "24" if i % 7 == 0 else "1", "prices": [str(10 + i % 50)] if i % 9 else [],
                      "kind": "stock" if i % 5 else "service", "barcode": f"885{i:010d}" if i % 3 else ""}
                     for i in range(products)],
        "units": [{"code": "01", "name": "ชิ้น"}, {"code": "02", "name": "โหล"}, {"code": "03", "name": "ลัง"}],
        "warehouses": [{"code": "01", "name": "คลังหลัก"}], "settings": {"mainloc": "01"}, "stock": [], "mark": "m1"}


def connect(site):
    import frappe

    frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    frappe.set_user("Administrator")
    return frappe


def company(frappe, tax):
    from pearnly_erp_portal.express import companies

    name = frappe.db.get_value("ERP Company", {"tax_id": tax}, "name")
    if not name:
        name = companies.build(tax, "00000", "LAB BULK " + tax[-1])
        frappe.db.commit()
    return name


def land(frappe, tax, data, book="BOOK-A", rows=False):
    from pearnly_erp_portal.express import masters

    name = company(frappe, tax)
    original = masters.bulk.insert_new
    if rows:
        masters.bulk.insert_new = lambda doctype, records, one: sum(one(values) for values in records)
    try:
        started = time.monotonic()
        counts = masters.apply(name, book, data)
        return name, counts, round(time.monotonic() - started, 2)
    finally:
        masters.bulk.insert_new = original


def snapshot(frappe, name):
    out = {}
    for doctype in TABLES:
        rows = frappe.get_all(doctype, filters={"express_company": name}, fields=["*"], limit_page_length=0)
        out[doctype] = {row.express_code: {k: v for k, v in row.items() if k not in SKIP} for row in rows}
    units = frappe.db.sql("""select p.express_code, u.unit_name, u.factor_to_base, u.is_default_sell, u.idx, u.parentfield, u.parenttype
        from `tabERP Product Unit` u join `tabERP Product` p on p.name = u.parent where p.express_company = %s
        order by p.express_code, u.idx""", (name,), as_dict=True)
    out["units"] = [tuple(row.values()) for row in units]
    return out


def names(frappe, companies):
    found = []
    for doctype in TABLES:
        found += frappe.get_all(doctype, filters={"express_company": ["in", companies]}, pluck="name", limit_page_length=0)
    return found


def cleanup(frappe):
    made = frappe.get_all("ERP Company", filters={"tax_id": ["in", list(TAXES.values())]}, pluck="name")
    if not made:
        return
    products = frappe.get_all("ERP Product", filters={"express_company": ["in", made]}, pluck="name", limit_page_length=0)
    for chunk in range(0, len(products), 1000):
        frappe.db.delete("ERP Product Unit", {"parent": ["in", products[chunk:chunk + 1000]]})
    for doctype in TABLES:
        frappe.db.delete(doctype, {"express_company": ["in", made]})
    frappe.db.delete("ERP Warehouse", {"company": ["in", made]})
    frappe.db.delete("ERP Company", {"name": ["in", made]})
    frappe.db.commit()


def child(tax, size):
    """并发那一段的子进程：落一家公司，回 JSON。"""
    frappe = connect(SITE)
    name, counts, seconds = land(frappe, tax, facts(*size))
    errors = frappe.db.count("Error Log", {"method": "ERP Express master sync row failed"})
    print(json.dumps({"company": name, "counts": counts, "seconds": seconds, "errors": errors}, default=str))


def main():
    if os.environ.get("PEARNLY_REGRESSION_LAB") != "1":
        raise SystemExit("Requires the task-owned regression laboratory")
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", default=SITE)
    parser.add_argument("--customers", type=int, default=3000)
    parser.add_argument("--child")
    args = parser.parse_args()
    size = (args.customers, max(50, args.customers // 10), max(50, args.customers // 8))
    if args.child:
        return child(args.child, size)
    frappe = connect(args.site)
    cleanup(frappe)
    failures, report = [], {}
    data = facts(*size)
    errors_before = frappe.db.count("Error Log")
    try:
        rows_name, rows_counts, report["rows_seconds"] = land(frappe, TAXES["rows"], data, rows=True)
        bulk_name, bulk_counts, report["bulk_seconds"] = land(frappe, TAXES["bulk"], data)
        report["counts"] = bulk_counts
        if {k: v for k, v in rows_counts.items()} != {k: v for k, v in bulk_counts.items()}:
            failures.append(f"counts differ: {rows_counts} vs {bulk_counts}")
        one, other = snapshot(frappe, rows_name), snapshot(frappe, bulk_name)
        for key in (*TABLES, "units"):
            if one[key] != other[key]:
                if isinstance(one[key], dict):
                    diff = [(code, field, one[key][code].get(field), other[key].get(code, {}).get(field))
                            for code in one[key] for field in one[key][code]
                            if one[key][code].get(field) != other[key].get(code, {}).get(field)][:5]
                else:
                    diff = len(one[key]), len(other[key])
                failures.append(f"{key} differs: {diff}")
        made = names(frappe, [rows_name, bulk_name])
        if len(made) != len(set(made)):
            failures.append("duplicate names")
        after = frappe.get_doc({"doctype": "ERP Customer", "customer_name": "LAB web after bulk"}).insert(ignore_permissions=True)
        if after.name in made:
            failures.append(f"native insert reused {after.name}")
        frappe.delete_doc("ERP Customer", after.name, force=True, ignore_permissions=True)
        frappe.db.commit()
        for book in ("BOOK-A", "BOOK-OTHER"):
            _name, again, _seconds = land(frappe, TAXES["bulk"], data, book=book)
            touched = {key: value for key, value in again.items() if key in ("customers", "suppliers", "products") and value}
            if touched:
                failures.append(f"re-landing with {book} changed {touched}")
        # 公司先建好（原生 insert 发号；两个进程同时建公司是 Frappe 自己的并发发号，不是这里要核的），只让落库并发
        for key in ("twin_a", "twin_b"):
            company(frappe, TAXES[key])
        script = os.path.abspath(__file__)
        procs = [subprocess.Popen([sys.executable, script, "--site", args.site, "--customers", str(args.customers),
                                   "--child", TAXES[key]], stdout=subprocess.PIPE, text=True) for key in ("twin_a", "twin_b")]
        started = time.monotonic()
        results = []
        for proc in procs:
            out, _err = proc.communicate(timeout=3600)
            if proc.returncode:
                failures.append(f"concurrent landing exited {proc.returncode}")
                continue
            results.append(json.loads(out.strip().splitlines()[-1]))
        report["concurrent_seconds"] = round(time.monotonic() - started, 2)
        frappe.db.rollback()
        twins = [row["company"] for row in results]
        made = names(frappe, twins)
        if len(made) != len(set(made)) or len(made) != 2 * sum(size):
            failures.append(f"concurrent landing made {len(made)} names ({len(set(made))} distinct), expected {2 * sum(size)}")
        if frappe.db.count("Error Log") != errors_before:
            failures.append("new Error Log rows")
        report["concurrent"] = results
    finally:
        cleanup(frappe)
    report["failures"] = failures
    print(json.dumps(report, ensure_ascii=False, default=str, indent=1))
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
