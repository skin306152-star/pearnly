"""业务流程用例里页面做不到、要在站点上直接做的几件事（步骤的 `exec`，在实验室容器里跑，最后一行 `JSON` 交给页面步骤）。

    ../env/bin/python /workspace/scripts/regression/flows/labctl.py <站点> <动作> [参数]

- `cc-connect <公司>`：模拟 CC 桌面版报上这家公司的账套（补 13 位税号、分店 00000，插一条账套行），让它「已连上」；
  不走上报流程（`books.replace` 会顺带按账套建公司），只写一行。
- `cc-unplug <公司>`：删掉上面那一行（回到「没连上」）。
- `count-sheet`：按正式盘点模板表头做一张 xlsx 放到站点 `/files/` 下：一行带品名的未知码、一行不带品名的未知码（码每次新发），
  页面取来走上传框同一条路（`upload_file` → 导入）。
- `product-sheet`：商品导入表：正式模板表头 + 旧表的「批次」「效期」两列，一行新商品（品名每次新发），放到 `/files/` 下；
  同时回模板表头，供页面核模板里没有这两列。
- `express-on <公司>` / `express-off <公司>`：勾「接 Express」并模拟连上 / 断开并撤掉模拟账套。
- `s3-prepare <仓>`：盘点就地新建用例要的码（桌面 / LINE / Express 公司 / 无权限各一个，每轮新发）、一件停用商品（条码新发），
  和一份给无权限账号用的进行中盘点；记在容器 `/tmp/flows-s3-<站点>.json`。
- `s3-inspect`：按上面那些码读回：商品（来由、条码 / 二维码、启用、Express 归属）、所在盘点行的来源与实盘、盘点状态与 Express 状态、
  停用商品的时间线。
- `vehicle-restock <车架号> <仓>`：实验数据复位（不是业务流程）：出过库的这台车回到「在库」，好让出库用例能重复扫同一个车架号。
  系统不让已登记过的号码再入库，这里按数量补入 1 件（按普通口径，账面跟着加）再把台账状态直接改回在库；没出过库就什么都不做。
- `express-send <公司>`：这家公司最近一份已过账、排着 Express 的盘点，照后台任务调一次 `push.send`（实验室没有后台进程）。
- `patch-company-settings`：建一家记着 Express 账套、没勾「接 Express」的公司，跑生产补丁 `v1_17_company_settings`，回读勾没勾上。
"""
import json
import os
import sys
import uuid

if os.environ.get("PEARNLY_FLOWS_LAB") != "1":
    raise SystemExit("Requires the task-owned flows laboratory")

import frappe  # noqa: E402

DEVICE = "flows-lab-device"


def _book_key(company):
    return f"flows-lab-{company}"


def cc_connect(company):
    from pearnly_core.cc_desktop import documents
    from pearnly_erp_portal.express import push

    row = frappe.db.get_value("ERP Company", company, ["tax_id", "branch"], as_dict=True)
    tax = "".join(ch for ch in str(row.tax_id or "") if ch.isdigit())
    if len(tax) != 13:
        tax = "0" + str(abs(hash(frappe.local.site + company)) % 10**12).zfill(12)
        frappe.db.set_value("ERP Company", company, {"tax_id": tax, "branch": "00000"}, update_modified=False)
    if not frappe.db.exists(documents.BOOK, {"book_key": _book_key(company)}):
        documents.new(documents.BOOK, frappe.generate_hash(length=20), {
            "device": DEVICE, "book_key": _book_key(company), "identity": _book_key(company), "path": f"C:\\flows\\{company}",
            "label": f"flows lab {company}", "company": company, "tax_id": tax, "branch": "00000", "writable": 1})
    frappe.db.commit()
    return {"connected": push.connected(company), "tax_id": tax}


def cc_unplug(company):
    from pearnly_core.cc_desktop import documents
    from pearnly_erp_portal.express import push

    for name in frappe.get_all(documents.BOOK, filters={"book_key": _book_key(company)}, pluck="name"):
        documents.remove(documents.BOOK, name)
    frappe.db.commit()
    return {"connected": push.connected(company)}


def count_sheet():
    from pearnly_core import browser_text
    from pearnly_erp_portal.inventory import workbook

    tag = uuid.uuid4().hex[:8].upper()
    named, bare = f"FLS{tag}N", f"FLS{tag}B"
    rows = [{"barcode": named, "product_name": f"แล็บนำเข้า {tag}", "unit": "ชิ้น", "counted_qty": 4},
            {"barcode": bare, "counted_qty": 3}]
    path = os.path.join(frappe.get_site_path("public", "files"), "flows-count-sheet.xlsx")
    with open(path, "wb") as handle:
        handle.write(workbook.build_bytes(workbook.template_headers(browser_text.label), rows))
    return {"url": "/files/flows-count-sheet.xlsx", "named": named, "bare": bare, "name": f"แล็บนำเข้า {tag}"}


def product_sheet():
    import openpyxl
    from pearnly_erp_portal.products import service

    headers = service._template_headers()
    name = f"แล็บนำเข้าสินค้า {uuid.uuid4().hex[:6]}"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append([*headers, "批次", "效期"])
    sheet.append([name if index == 0 else "" for index in range(len(headers))] + ["LOT-1", "2027-12-31"])
    path = os.path.join(frappe.get_site_path("public", "files"), "flows-product-sheet.xlsx")
    book.save(path)
    return {"url": "/files/flows-product-sheet.xlsx", "headers": headers, "name": name}


def express_on(company):
    out = cc_connect(company)
    frappe.db.set_value("ERP Company", company, "express_enabled", 1, update_modified=False)
    frappe.db.commit()
    from pearnly_erp_portal.express import push
    return {**out, "linked": push.linked(company)}


def express_off(company):
    frappe.db.set_value("ERP Company", company, "express_enabled", 0, update_modified=False)
    frappe.db.commit()
    return cc_unplug(company)


def _s3_file():
    return f"/tmp/flows-s3-{frappe.local.site}.json"


def s3_prepare(warehouse):
    from pearnly_erp_portal.inventory import count
    from pearnly_erp_portal.products import service

    tag = uuid.uuid4().hex[:6].upper()
    codes = {kind: f"S3{kind[0].upper()}{tag}" for kind in ("desk", "line", "express", "sheet", "noperm")}
    disabled = service.create_product(product_name=f"แล็บหยุดขาย {tag}", barcode=f"S3H{tag}", unit="ชิ้น")["name"]
    frappe.db.set_value("ERP Product", disabled, "is_active", 0)
    noperm_count = count.create_count(title=f"flows noperm {tag}", warehouse=warehouse)["name"]
    frappe.db.commit()
    data = {"codes": codes, "disabled": {"name": disabled, "code": f"S3H{tag}"}, "noperm_count": noperm_count}
    with open(_s3_file(), "w") as handle:
        json.dump(data, handle)
    return data


def s3_inspect():
    with open(_s3_file()) as handle:
        data = json.load(handle)
    item = "ERP Stock Count Item"

    def product_info(code):
        rows = frappe.get_all("ERP Product", filters=[["barcode", "=", code]], pluck="name") + \
            frappe.get_all("ERP Product", filters=[["qr_payload", "=", code]], pluck="name")
        out = []
        for name in sorted(set(rows)):
            doc = frappe.db.get_value("ERP Product", name, ["name", "product_name", "code", "barcode", "qr_payload", "is_active",
                                                            "origin", "origin_doctype", "origin_name", "express_company"], as_dict=True)
            lines = frappe.get_all(item, filters={"product": name}, fields=["count", "row_origin", "counted_qty", "warehouse"],
                                   order_by="creation desc", limit_page_length=5)
            counts = {line.count: frappe.db.get_value("ERP Stock Count", line.count, ["status", "express_state", "express_reason"],
                                                      as_dict=True) for line in lines}
            out.append({**doc, "lines": lines, "counts": counts})
        return out

    result = {kind: product_info(code) for kind, code in data["codes"].items()}
    disabled = data["disabled"]["name"]
    result["disabled"] = {"product": product_info(data["disabled"]["code"]),
                          "comments": frappe.get_all("Comment", filters={"reference_doctype": "ERP Product", "reference_name": disabled},
                                                     pluck="content", limit_page_length=10)}
    return {"prepared": data, "found": result}


def vehicle_restock(serial, warehouse):
    from pearnly_erp_portal.inventory import documents

    row = frappe.db.get_value("ERP Serial No", {"serial_no": serial}, ["name", "product", "status"], as_dict=True)
    if not row or row.status == "in_stock":
        return {"serial": serial, "status": row and row.status, "restocked": False}
    company = frappe.db.get_value("ERP Warehouse", warehouse, "company")
    kind = frappe.db.get_value("ERP Company", company, "company_type")
    frappe.db.set_value("ERP Company", company, "company_type", "general", update_modified=False)
    try:
        documents.create_entry(direction="in", posting_date=frappe.utils.nowdate(), warehouse=warehouse, reason="flows lab restock",
                               lines=[{"product": row.product, "qty": 1, "unit_cost": 500000, "warehouse": warehouse}], submit=True)
    finally:
        frappe.db.set_value("ERP Company", company, "company_type", kind, update_modified=False)
    frappe.db.set_value("ERP Serial No", row.name, {"status": "in_stock", "warehouse": warehouse})
    frappe.db.commit()
    return {"serial": serial, "status": "in_stock", "restocked": True}


def express_send(company):
    from pearnly_erp_portal.express import push, stock_entry

    names = frappe.get_all("ERP Stock Count", filters={"express_state": "queued"}, pluck="name",
                           order_by="modified desc", limit_page_length=20)
    name = next((n for n in names if stock_entry.company_of(n, "ERP Stock Count") == company), None)
    if name:
        push.send("ERP Stock Count", name)
    row = frappe.db.get_value("ERP Stock Count", name, ["express_state", "express_reason"], as_dict=True) if name else None
    return {"count": name, **(row or {})}


def patch_company_settings():
    from pearnly_erp_portal.masters import company as companies
    from pearnly_erp_portal.patches import v1_17_company_settings

    linked = companies.create_company(company_name=f"แล็บแพตช์ {uuid.uuid4().hex[:6]}", warehouses=["คลังแพตช์"])["name"]
    frappe.db.set_value("ERP Company", linked, {"express_book": "flows-lab-patch", "express_enabled": 0, "company_type": None},
                        update_modified=False)
    plain = companies.create_company(company_name=f"แล็บแพตช์ปกติ {uuid.uuid4().hex[:6]}", warehouses=["คลังปกติ"])["name"]
    frappe.db.set_value("ERP Company", plain, {"express_enabled": 0, "company_type": None}, update_modified=False)
    frappe.db.commit()
    v1_17_company_settings.execute()
    read = lambda name: dict(frappe.db.get_value("ERP Company", name, ["express_enabled", "company_type"], as_dict=True))  # noqa: E731
    return {"linked": read(linked), "plain": read(plain)}


def main():
    site, action, args = sys.argv[1], sys.argv[2], sys.argv[3:]
    frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    try:
        assert frappe.conf.get("pearnly_product") == "erp"
        frappe.set_user(f"admin@{site.split('.')[0]}.flows")
        result = {"cc-connect": lambda: cc_connect(args[0]), "cc-unplug": lambda: cc_unplug(args[0]),
                  "count-sheet": count_sheet, "vehicle-restock": lambda: vehicle_restock(args[0], args[1]), "express-on": lambda: express_on(args[0]), "express-off": lambda: express_off(args[0]), "s3-prepare": lambda: s3_prepare(args[0]), "s3-inspect": s3_inspect, "product-sheet": product_sheet, "express-send": lambda: express_send(args[0]),
                  "patch-company-settings": patch_company_settings}[action]()
        frappe.db.commit()
        print("JSON", json.dumps(result, ensure_ascii=False, default=str))
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main()
