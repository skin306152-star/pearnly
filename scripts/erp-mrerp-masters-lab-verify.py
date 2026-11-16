"""实验栈核对：MR.ERP 主档整读落进 ERP（公司接入第 3 批，2026-11-14）走与 Express 同一个落库。

只在任务自己的回归实验室里跑（``PEARNLY_REGRESSION_LAB=1``），不连外网：读的是 test01 · TEST2019 导出页与结存报表的原样 xlsx
（`tests/fixtures/mrerp/export_*.xlsx`、`report_stock_by_store.xlsx`，先 ``docker cp`` 进容器，目录作参数），自建一家 LAB 公司，
跑完把这家公司和它的主档、库存流水删掉。

    docker cp tests/fixtures/mrerp <bench 容器>:/tmp/mrerp-fixtures
    docker compose -p <栈名> -f infrastructure/compose/regression-lab.yml exec -T bench \\
        env/bin/python /workspace/scripts/erp-mrerp-masters-lab-verify.py --fixtures /tmp/mrerp-fixtures

核对：
1. 第一次落库：客户 7 / 供应商 14 / 物料 12 / 仓库 3 全部建出，档上 ``ledger`` = mrerp、``express_company`` = 这家公司、
   ``express_code`` = MR.ERP 编码；物料来由 ``mrerp``；税号写「-」的客户不存税号；仓库码照 MR.ERP 原样（4 位以上不截）；
2. 结存：库存品各仓的 ERP 结存 = MR.ERP 报表的数（服务档 EXPENSE 不进库存）；
3. 同一份再落一次：0 条改动；
4. 回填补丁（v1_30）：带编码、没有 ``ledger`` 的 Express 档补成 express，再落不算改动；
5. Express 的每分钟推进（`express.masters.advance`）不碰接 MR.ERP 的公司（状态停在排队中也不发电脑读取）。
任何一项不过，退出码非 0。
"""
import argparse
import os
import sys
from decimal import Decimal

SITE = "erp.regression.localhost"
TAX = "0105599990311"


def connect(site):
    import frappe

    frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    frappe.set_user("Administrator")
    return frappe


def read_facts(folder):
    from pearnly_core.mrerp import master_export as mx

    rows = lambda name: mx.xlsx_rows(open(os.path.join(folder, name), "rb").read())  # noqa: E731
    warehouses, stock = mx.stock(rows("report_stock_by_store.xlsx"))
    listed = [{"code": "0000", "name": "สนง ใหญ่"}, {"code": "0001", "name": "คลัง สาขา 1"},
              {"code": "0002", "name": "คลัง สาขา 2"}]  # 列表接口给的全部仓（TEST2019）
    known = {row["code"] for row in listed}
    return {"customers": mx.parties(rows("export_customers.xlsx"), customer=True),
            "suppliers": mx.parties(rows("export_suppliers.xlsx"), customer=False),
            "products": mx.products(rows("export_products.xlsx")),
            "units": [{"code": "คัน", "name": "คัน"}],
            "warehouses": listed + [row for row in warehouses if row["code"] not in known],
            "stock": stock, "settings": {"mainloc": "0000"}}


def cleanup(frappe, name):
    from pearnly_erp_portal.inventory import stock

    if not name:
        return
    warehouses = frappe.get_all("ERP Warehouse", filters={"company": name}, pluck="name")
    if warehouses:
        for doctype in (stock.MOVEMENT, stock.BALANCE):
            frappe.db.delete(doctype, {"warehouse": ["in", warehouses]})
    for doctype in ("ERP Customer", "ERP Supplier", "ERP Product"):
        frappe.db.delete(doctype, {"express_company": name})
    frappe.db.delete("ERP Warehouse", {"company": name})
    frappe.db.delete("ERP Company", {"name": name})
    frappe.db.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", default=SITE)
    parser.add_argument("--fixtures", default="/tmp/mrerp-fixtures")
    args = parser.parse_args()
    if os.environ.get("PEARNLY_REGRESSION_LAB") != "1":
        sys.exit("只在回归实验室里跑（PEARNLY_REGRESSION_LAB=1）")
    frappe = connect(args.site)
    from pearnly_erp_portal import mrerp
    from pearnly_erp_portal.express import masters
    from pearnly_erp_portal.inventory import stock

    mrerp.MRERP_LEDGER_ENABLED = True  # 只在本进程打开，不提交
    failures = []

    def check(ok, label):
        print(("ok  " if ok else "FAIL") + " " + label)
        if not ok:
            failures.append(label)

    data = read_facts(args.fixtures)
    cleanup(frappe, frappe.db.get_value("ERP Company", {"tax_id": TAX}, "name"))
    doc = frappe.get_doc({"doctype": "ERP Company", "company_name": "LAB MR.ERP TEST2019", "tax_id": TAX, "branch": "00000",
                          "is_active": 1, "pos_enabled": 0, "express_enabled": 0, "company_type": "general",
                          "mrerp_enabled": 1, "mrerp_account_set": "6:1"}).insert(ignore_permissions=True)
    from pearnly_erp_portal.inventory import warehouses as erp_warehouses

    erp_warehouses.ensure_for_company(doc.name, "Main")  # 同 `cc_service._build_mrerp`
    frappe.db.commit()
    name = doc.name
    try:
        counts = masters.apply(name, "TEST2019", data, ledger=masters.MRERP)
        print("first landing", counts)
        for doctype, expected in (("ERP Customer", 7), ("ERP Supplier", 14), ("ERP Product", 12)):
            rows = frappe.get_all(doctype, filters={"express_company": name}, fields=["ledger", "express_code"])
            check(len(rows) == expected and all(row.ledger == masters.MRERP for row in rows),
                  f"{doctype}: {len(rows)} rows, all ledger MR.ERP")
        person = frappe.db.get_value("ERP Customer", {"express_company": name, "express_code": "01-อนุรักษ์-001"},
                                     ["customer_name", "tax_id"], as_dict=True)
        check(person and person.customer_name == "นาย อนุรักษ์ ทดสอบ" and not person.tax_id, "customer without tax id")
        origins = set(frappe.get_all("ERP Product", filters={"express_company": name}, pluck="origin"))
        check(origins == {"mrerp"}, f"product origin {origins}")
        codes = set(frappe.get_all("ERP Warehouse", filters={"company": name, "express_code": ["is", "set"]},
                                   pluck="express_code"))
        check({"0000", "0001", "0002"} <= codes, f"warehouses {sorted(codes)}")
        unmapped = frappe.get_all("ERP Warehouse", filters={"company": name, "express_code": ["is", "not set"]}, pluck="name")
        check(not unmapped, f"the company's own Main warehouse is taken as MR.ERP 0000 ({unmapped})")
        warehouse = frappe.db.get_value("ERP Warehouse", {"company": name, "express_code": "0000"}, "name")
        for code, expected in (("P6506F369EE8", Decimal("88")), ("P3329A609F01", Decimal("5"))):
            product = frappe.db.get_value("ERP Product", {"express_company": name, "express_code": code}, "name")
            qty = sum(Decimal(str(row.qty_on_hand or 0)) for row in frappe.get_all(
                stock.BALANCE, filters={"product": product, "warehouse": warehouse}, fields=["qty_on_hand"]))
            check(qty == expected, f"stock {code} @0000 = {qty} (MR.ERP {expected})")
        service = frappe.db.get_value("ERP Product", {"express_company": name, "express_code": "EXPENSE"}, "express_stock")
        check(not service, "service item not stocked")

        again = masters.apply(name, "TEST2019", data, ledger=masters.MRERP)
        changed = {key: value for key, value in again.items() if key not in ("stock",) and value}
        check(not changed and not again.get("stock"), f"second landing changes nothing {again}")

        # 回填补丁：抹掉一条档的 ledger（存量 Express 档的样子），补丁补成 Express；改回 MR.ERP 后再落不算改动
        sample = frappe.db.get_value("ERP Customer", {"express_company": name}, "name")
        frappe.db.set_value("ERP Customer", sample, "ledger", None, update_modified=False)
        from pearnly_erp_portal.patches import v1_30_master_ledger_source

        v1_30_master_ledger_source.execute()
        check(frappe.db.get_value("ERP Customer", sample, "ledger") == masters.EXPRESS, "patch backfills Express")
        frappe.db.set_value("ERP Customer", sample, "ledger", masters.MRERP, update_modified=False)
        frappe.db.commit()

        # Express 的每分钟推进不碰接 MR.ERP 的公司
        frappe.db.set_value("ERP Company", name, "express_sync_state", masters.QUEUED, update_modified=False)
        masters.advance()
        check(frappe.db.get_value("ERP Company", name, "express_sync_state") == masters.QUEUED,
              "express advance leaves the MR.ERP company alone")
    finally:
        cleanup(frappe, name)
    print("FAILED" if failures else "ALL OK", len(failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
