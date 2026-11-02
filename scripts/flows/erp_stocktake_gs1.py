"""流程用例：ERP 盘点扫 GS1 码（Data Matrix 带批号 / 有效期 / 序列号）按商品条码认出；系统没有的 GTIN 当场新建，条码记 GTIN
（主会话 10-30：码制一次覆盖全、GS1 按类解析，`products.gs1`）。

每套数据：开一份新盘点。
- 输一个 GS1 Data Matrix 读出来的原文（GTIN `08850001000019` + 有效期 + 批号 + 序列号，中间带 GS 分隔）：认出条码是
  `8850001000019` 的那件（新开户就是数据集里的条码商品，老数据先建一件本地的），填数量保存；
- 输一个系统里没有的 GTIN 的 GS1 原文：出「新建物料」，填品名、单位建好后认出这件，保存。库里新商品的条码是 13 位的 GTIN
  （不是那串原文），来由是这份盘点；
- 手机宽度另用假摄像头读 `scripts/smoke/codes/data-matrix-gs1.png`（同一个 GTIN 的 GS1 Data Matrix），认出同一件。
"""
from __future__ import annotations

import json
import random
import uuid

from stocktake_lib import HAS_SCAN, SCAN, type_code

TITLE = "ERP 盘点：GS1 Data Matrix 按条码认出、没有的 GTIN 当场新建"
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")
CAPTURE = ("window.__dlgs=window.__dlgs||[];if(!frappe.ui.Dialog.prototype.__cap){const o=frappe.ui.Dialog.prototype.show;"
           "frappe.ui.Dialog.prototype.show=function(){window.__dlgs.push(this);return o.apply(this,arguments)};frappe.ui.Dialog.prototype.__cap=1;}")
KNOWN_EAN = "8850001000019"
KNOWN_RAW = "01088500010000191727123110LOT7\x1d21SN12345"  # WASM 读 data-matrix-gs1.png 给的原文
PICTURE = "scripts/smoke/codes/data-matrix-gs1.png"


def check_digit(body: str) -> str:
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return str((10 - total % 10) % 10)


def prepare(lab, manifest):
    company = manifest.get("company") or manifest["companies"][0]
    warehouse = next(w["name"] for w in manifest["warehouses"] if w.get("company", company) == company)
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import count
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.products import service as products
frappe.set_user("admin@erp.regression")
companies.set_active_company({company!r})
known = frappe.db.get_value("ERP Product", {{"barcode": {KNOWN_EAN!r}}}, ["name", "product_name"], as_dict=True)
if not known:
    made = products.create_product(product_name="สินค้า GS1 flow", unit="ชิ้น", unit_price=100, default_cost=60, barcode={KNOWN_EAN!r})
    known = frappe.db.get_value("ERP Product", made["name"], ["name", "product_name"], as_dict=True)
frappe.db.commit()
frappe.set_user("stock@erp.regression")
companies.set_active_company({company!r})
doc = count.create_count(title="flow gs1 " + {uuid.uuid4().hex[:6]!r}, warehouse={warehouse!r})
frappe.db.commit()
print(json.dumps({{"count": doc["name"], "known": known.name, "known_name": known.product_name}}))
""")
    body = "885" + "".join(random.choice("0123456789") for _ in range(9))
    gtin13 = body + check_digit(body)
    unknown_raw = "010" + gtin13 + "21SN" + uuid.uuid4().hex[:6].upper()
    return {**out, "company": company, "warehouse": warehouse, "gtin13": gtin13, "unknown_raw": unknown_raw,
            "name": "flow gs1 " + gtin13[-4:],
            # 手机宽度另用假摄像头跑一遍（`run.py` 按 key 为 by_barcode 的那一例取画面）
            "cases": [{"key": "by_barcode", "scan": PICTURE}]}


def _start():
    return {"name": "开始盘点", "js": IDLE + CAPTURE +
            "const b=[...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e=>e.offsetParent);"
            "if(!b) return {error:'no start button'}; b.click();"
            "for(let k=0;k<80&&!(window.cur_dialog&&cur_dialog.fields_dict.code);k++) await new Promise(r=>setTimeout(r,250));"
            "await idle(); return b.innerText.trim();"}


def _look(name, raw):
    return {"name": name, "js": IDLE + "const d=window.__dlgs.find(x=>x.fields_dict.code)||cur_dialog; if(!d||!d.fields_dict.code) return {error:'no stocktake dialog'};"
            + type_code(json.dumps(raw)) +
            "await new Promise(r=>setTimeout(r,500)); await idle();"
            "document.querySelectorAll('.msgprint-dialog .btn-modal-close').forEach(b=>b.click());"
            "const btn=d.fields_dict.new_product; return {entry: d.fields_dict.qty.$wrapper.is(':visible'), product_name: d.get_value('product_name')||'',"
            " new_button: !!(btn && btn.$wrapper.is(':visible')), message: (d.fields_dict.message.$wrapper.find('p').text()||'').trim().slice(0,200)};"}


def _save(name, qty):
    return {"name": name, "js": IDLE + "const d=window.__dlgs.find(x=>x.fields_dict.code)||cur_dialog; if(!d||!d.fields_dict.qty.$wrapper.is(':visible')) return {error:'no entry'};"
            f"d.set_value('qty', {qty}); await new Promise(r=>setTimeout(r,300)); d.get_primary_btn().trigger('click');"
            "await new Promise(r=>setTimeout(r,300)); await idle(); return {saved: true};"}


def steps(params, camera_code=None):
    if camera_code:
        return [_start(), {"name": "扫 GS1 Data Matrix", "js": IDLE + f"const d=cur_dialog; if(!d||!{HAS_SCAN}) return {{error:'no camera'}};"
                           + SCAN +
                           "for(let k=0;k<80&&!d.fields_dict.qty.$wrapper.is(':visible');k++) await new Promise(r=>setTimeout(r,250)); await idle();"
                           "return {entry: d.fields_dict.qty.$wrapper.is(':visible'), product_name: d.get_value('product_name')||''};"},
                _save("保存", 4)]
    name = json.dumps(params["name"])
    return [_start(), _look("认 GS1 原文", KNOWN_RAW), _save("保存认出的", 2), _look("没有的 GTIN", params["unknown_raw"]),
            {"name": "新建物料", "js": IDLE + "const d=window.__dlgs.find(x=>x.fields_dict.code); const n=window.__dlgs.length;"
             "d.fields_dict.new_product.$input.trigger('click');"
             "for(let k=0;k<40&&window.__dlgs.length===n;k++) await new Promise(r=>setTimeout(r,250));"
             "const f=window.__dlgs[window.__dlgs.length-1]; if(!f||!f.fields_dict.product_name) return {error:'no new product form'};"
             f"await f.set_value('product_name', {name}); await f.set_value('unit', 'ชิ้น'); f.get_primary_btn().trigger('click');"
             "for(let k=0;k<60&&!d.fields_dict.qty.$wrapper.is(':visible');k++) await new Promise(r=>setTimeout(r,250)); await idle();"
             "return {entry: d.fields_dict.qty.$wrapper.is(':visible'), product_name: d.get_value('product_name')||''};"},
            _save("保存新建的", 3)]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    if camera_code:
        seen = by.get("扫 GS1 Data Matrix", {})
        return [{"case": "camera Data Matrix finds the product", "seen": seen,
                 "ok": seen.get("entry") is True and seen.get("product_name") == params["known_name"]}]
    known, unknown, made = by.get("认 GS1 原文", {}), by.get("没有的 GTIN", {}), by.get("新建物料", {})
    return [{"case": "GS1 text finds the product by its barcode", "seen": known,
             "ok": known.get("entry") is True and known.get("product_name") == params["known_name"]},
            {"case": "unknown GTIN offers new product", "seen": unknown, "ok": bool(unknown.get("new_button")) and not unknown.get("entry")},
            {"case": "created product is recognised", "seen": made, "ok": made.get("entry") is True and made.get("product_name") == params["name"]}]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
made = frappe.db.get_value("ERP Product", {{"product_name": {params['name']!r}}}, ["name", "barcode", "qr_payload", "origin", "origin_name"], as_dict=True) or {{}}
rows = frappe.get_all("ERP Stock Count Entry", filters={{"count": {params['count']!r}, "voided": 0}}, fields=["product", "qty"], limit_page_length=20)
print(json.dumps({{"made": made, "entries": sorted([[r.product, float(r.qty)] for r in rows])}}, default=str))
""")
    if camera_code:
        return [{"case": "camera entry saved on the product", "saved": got, "ok": got["entries"] == [[params["known"], 4.0]]}]
    made = got["made"]
    return [{"case": "new product keeps the GTIN as its barcode", "saved": made,
             "ok": made.get("barcode") == params["gtin13"] and not made.get("qr_payload") and made.get("origin") == "stocktake"
             and made.get("origin_name") == params["count"]},
            {"case": "both entries saved", "saved": got["entries"],
             "ok": got["entries"] == sorted([[params["known"], 2.0], [made.get("name"), 3.0]])}]
