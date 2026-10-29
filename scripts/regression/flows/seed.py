"""业务流程回归实验室的两套数据（`lab.py` 建好站点后跑）。都走系统正式接口（金额、单号、库存由系统算 / 发），以管理员身份。

    ../env/bin/python /workspace/scripts/regression/flows/seed.py new erp-new.flows.localhost
    ../env/bin/python /workspace/scripts/regression/flows/seed.py old erp-old.flows.localhost

- new（像 polymer：新开户、空仓、多仓）：一家公司、7 个仓、一台收银台，**没有期初库存**；商品分别只靠一种码认：
  编码、条码（EAN-13）、二维码内容、内部编码、Express 同步（Express 编码 + 内部编码取自 Express 编码），再加一个按台管理的车。
- old（像 mrerp：有存量）：一家公司、2 个仓、一台收银台；同样几类商品，入库建库存，按台的车入了 3 台；
  采购发票（入库）、销售单、收银开班卖一张退一张交班、一张已过账的盘点、一张进行中的盘点。

可重复跑：公司与商品按名字 / 码沿用，单据每次加一批。最后一行 `JSON` 是各对象名字，供流程用例取。
"""
import json
import os
import sys
import uuid

if os.environ.get("PEARNLY_FLOWS_LAB") != "1":
    raise SystemExit("Requires the task-owned flows laboratory")

import frappe  # noqa: E402

# 码值：每一种只能靠它认出商品（流程用例扫 / 输这些码，断言认到的是哪件）
CODES = {
    "code": "PM-0001", "barcode": "8850001000019", "qr": "https://polymer.example/p?id=QR-7788",
    "internal": "INT-555", "express": "EXP-900", "vehicle_chassis": ["MR0LAB00000000001", "MR0LAB00000000002",
                                                                      "MR0LAB00000000003"],
}
WAREHOUSES_NEW = ["คลังหลัก", "คลังสาขา 1", "คลังสาขา 2", "คลังสาขา 3", "คลังวัตถุดิบ", "คลังสินค้าสำเร็จรูป", "คลังเสีย"]
WAREHOUSES_OLD = ["Main", "Branch"]


def product(name, **fields):
    from pearnly_erp_portal.products import service

    found = frappe.db.get_value("ERP Product", {"product_name": name}, "name")
    return found or service.create_product(product_name=name, unit="ชิ้น", unit_price=100, vat_applicable=1, **fields)["name"]


def products(company):
    from pearnly_erp_portal.inventory import serials

    made = {
        "code": product("สินค้าตามรหัส", code=CODES["code"]),
        "barcode": product("สินค้าตามบาร์โค้ด", barcode=CODES["barcode"]),
        "qr": product("สินค้าตามคิวอาร์", qr_payload=CODES["qr"]),
        "internal": product("สินค้าตามรหัสภายใน", internal_code=CODES["internal"]),
        "express": product("สินค้าจาก Express"),
        "vehicle": product("รถทดสอบ รุ่น A", serial_kind="vehicle"),
    }
    # Express 同步来的商品：同步写的是 Express 编码 + 所属公司，内部编码取自 Express 编码（13658fc6）
    frappe.db.set_value("ERP Product", made["express"], {"express_code": CODES["express"], "express_company": company,
                                                          "express_stock": 1, "internal_code": CODES["express"]})
    if not frappe.db.get_value("ERP Product", made["vehicle"], "track_serial"):
        serials.turn_on(made["vehicle"], kind="vehicle")
    frappe.db.commit()
    return made


def line_binding(user, line_user_id):
    """手机盘点页不在 LINE 里打开时回落到「本账号自己的 LINE 绑定」：实验室给管理员记一条（假的 LINE 用户号，不连 LINE）。"""
    if not frappe.db.exists("Pearnly LINE Binding", {"user": user, "status": "active"}):
        frappe.get_doc({"doctype": "Pearnly LINE Binding", "user": user, "line_user_id": line_user_id, "status": "active",
                        "display_name": "flows lab", "bound_at": frappe.utils.now()}).insert(ignore_permissions=True)
        frappe.db.commit()


def company(name, warehouses):
    from pearnly_erp_portal.masters import company as companies

    found = frappe.db.get_value("ERP Company", {"company_name": name}, "name")
    if not found:
        found = companies.create_company(company_name=name, warehouses=warehouses,
                                         terminals=[{"terminal_name": "หน้าร้าน 1", "warehouse": warehouses[0]}])["name"]
    frappe.defaults.set_user_default("company", found)
    frappe.db.commit()
    rows = frappe.get_all("ERP Warehouse", filters={"company": found}, fields=["name", "warehouse_name"], order_by="creation")
    return found, {row.warehouse_name: row.name for row in rows}


def new(site):
    co, whs = company("บริษัท โพลีเมอร์ แล็บ จำกัด", WAREHOUSES_NEW)
    made = products(co)
    return {"company": co, "company_name": "บริษัท โพลีเมอร์ แล็บ จำกัด", "warehouses": whs, "products": made, "codes": CODES}


def old(site):
    from pearnly_erp_portal.cashier import sales as pos, shifts
    from pearnly_erp_portal.inventory import count, documents
    from pearnly_erp_portal.purchasing import entry as purchase
    from pearnly_erp_portal.sales import entry as sales

    co, whs = company("บริษัท เอ็มอาร์ แล็บ จำกัด", WAREHOUSES_OLD)
    made = products(co)
    main, branch = whs["Main"], whs["Branch"]
    plain = [made[k] for k in ("code", "barcode", "qr", "internal", "express")]
    tag = uuid.uuid4().hex[:6]
    out = {"company": co, "company_name": "บริษัท เอ็มอาร์ แล็บ จำกัด", "warehouses": whs, "products": made, "codes": CODES}
    # 存量：两个仓各入一批普通商品；按台的车入 3 台（车架号 / 发动机号 / 颜色）
    for wh, qty in ((main, 20), (branch, 5)):
        e = documents.create_entry(direction="in", posting_date=frappe.utils.nowdate(), warehouse=wh, reason="opening lab",
                                   lines=[{"product": p, "qty": qty, "unit_cost": 50, "warehouse": wh} for p in plain], submit=True)
        out.setdefault("stock_in", []).append(e["name"])
    have = frappe.db.count("ERP Serial No", {"product": made["vehicle"], "status": "in_stock"}) if frappe.db.table_exists("ERP Serial No") else 0
    if not have:
        units = [{"serial_no": n, "secondary_no": f"ENG{n[-5:]}", "color": "ขาว"} for n in CODES["vehicle_chassis"]]
        e = documents.create_entry(direction="in", posting_date=frappe.utils.nowdate(), warehouse=main, reason="vehicles lab",
                                   lines=[{"product": made["vehicle"], "qty": 3, "unit_cost": 500000, "warehouse": main,
                                           "serials": units}], submit=True)
        out["vehicles_in"] = e["name"]
    # 采购发票（入库）、销售单
    supplier_lines = [{"item_type": "goods", "product": made["code"], "description": "สินค้าตามรหัส", "qty": 3, "unit": "ชิ้น",
                       "unit_price": 40, "vat_rate": 7, "warehouse": main, "location": ""}]
    out["purchase"] = purchase.post(payload={"company": co, "doc_kind": "purchase_invoice", "supplier_name": "ผู้ขายแล็บ",
                                             "doc_no": "INV-" + tag, "doc_date": frappe.utils.nowdate(), "currency": "THB",
                                             "fx_rate": 1, "has_vat": 1, "price_mode": "exclusive", "update_stock": 1,
                                             "lines": supplier_lines}, request_id=str(uuid.uuid4()))["name"]
    out["sale"] = sales.post(payload={"company": co, "doc_kind": "receipt", "doc_date": frappe.utils.nowdate(), "has_vat": 1,
                                      "vat_rate": 7, "source": "manual", "buyer_name": "ลูกค้าแล็บ",
                                      "lines": [{"product": made["barcode"], "description": "สินค้าตามบาร์โค้ด", "qty": 2,
                                                 "unit": "ชิ้น", "unit_price": 100, "vat_applicable": 1, "warehouse": main,
                                                 "location": ""}]}, request_id=str(uuid.uuid4()))["name"]
    # 收银：开班卖一张退一张交班
    terminal = frappe.db.get_value("ERP POS Terminal", {"company": co})
    current = shifts.current_shift(terminal=terminal)
    if current:
        shifts.close_shift(name=current["shift"]["name"], counted_cash=current["expected_cash"])
    shift = shifts.open_shift(terminal=terminal, opening_float=1000)["name"]
    sale = pos.create_sale(lines=[{"product": made["qr"], "qty": 2, "unit_price": 100}],
                           payments=[{"method": "cash", "amount": 214}], shift=shift, terminal=terminal,
                           client_uuid=str(uuid.uuid4()))
    detail = pos.get_sale(sale["name"])
    first = detail["lines"][0]
    pos.refund(name=sale["name"], lines=[{"original_line": first["name"], "qty": 1}],
               payments=[{"method": "cash", "amount": first["line_total"] / first["qty"]}], reason="ชำรุด",
               client_uuid=str(uuid.uuid4()))
    summary = shifts.summary(shift)
    shifts.close_shift(name=shift, counted={"cash": summary["expected_cash"]}, note="")
    out.update(pos_sale=sale["name"], shift=shift)
    # 盘点：一张进行中（branch）、一张已过账（main 的 code 商品多点 1）
    active = count.create_count(title="นับสต็อก แล็บ " + tag, warehouse=branch)
    out["count_active"] = active["name"]
    frappe.db.commit()
    return out


def main():
    kind, site = sys.argv[1], sys.argv[2]
    frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    try:
        assert frappe.conf.get("pearnly_product") == "erp"
        frappe.set_user(f"admin@{site.split('.')[0]}.flows")
        result = {"dataset": kind, **{"new": new, "old": old}[kind](site)}
        prefix = site.split(".")[0]
        line_binding(f"admin@{prefix}.flows", f"Ulab-flows-{prefix}")
        frappe.db.commit()
        print("JSON", json.dumps(result, ensure_ascii=False, default=str))
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main()
