"""流程用例：盘点扫到网址码（泰国工业标准认证 TIS 码，岳父 11-01 实测扫到过两次）。

网址不是商品码：认码那一层（`products.codes.refuse_web_address`）统一拒。盘点录入框输入 / 假摄像头读到
`https://appdb.tisi.go.th/Q/i.php?d=…`（码图 `scripts/smoke/codes/qr-tis-url.png`）→ 录入弹窗提示「这不是商品条码」、
不出「新建物料」、没有原生提示框挡着、相机那一遍画面还开着（接着扫，先跳过这个码）；库里没有因此建出物料、没有记数。
LINE 手机盘点页同一条规矩由 `erp.line.stocktake` 的「输网址码」一步断言。
"""
from __future__ import annotations

import json
import uuid

from stocktake_lib import SCAN, type_code

TITLE = "ERP 盘点：扫到网址码提示不是商品条码"
URL = "https://appdb.tisi.go.th/Q/i.php?d=PEARNLYFLOW0001"
PICTURE = "scripts/smoke/codes/qr-tis-url.png"
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")
CAPTURE = ("window.__dlgs=window.__dlgs||[];if(!frappe.ui.Dialog.prototype.__cap){const o=frappe.ui.Dialog.prototype.show;"
           "frappe.ui.Dialog.prototype.show=function(){window.__dlgs.push(this);return o.apply(this,arguments)};frappe.ui.Dialog.prototype.__cap=1;}")


def prepare(lab, manifest):
    company = manifest.get("company") or manifest["companies"][0]
    warehouse = next(w["name"] for w in manifest["warehouses"] if w.get("company", company) == company)
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import count
from pearnly_erp_portal.masters import company as companies
frappe.set_user("stock@erp.regression")
companies.set_active_company({company!r})
doc = count.create_count(title="flow web code " + {uuid.uuid4().hex[:6]!r}, warehouse={warehouse!r})
frappe.db.commit()
print(json.dumps({{"count": doc["name"], "products": frappe.db.count("ERP Product")}}))
""")
    # 相机那一遍喂的是 TIS 网址二维码图（`run.py` 认 `.png` 码图）
    return {**out, "company": company, "warehouse": warehouse, "cases": [{"key": "by_barcode", "scan": PICTURE}]}


def steps(params, camera_code=None):
    find = SCAN if camera_code else type_code(json.dumps(URL))
    return [
        {"name": "开始盘点", "js": IDLE + CAPTURE +
         "const b=[...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e=>e.offsetParent);"
         "if(!b) return {error:'no start button'}; b.click();"
         "for(let k=0;k<80&&!(window.cur_dialog&&cur_dialog.fields_dict.code);k++) await new Promise(r=>setTimeout(r,250));"
         "await idle(); return b.innerText.trim();"},
        {"name": "扫网址码", "js": IDLE + "const d=cur_dialog; if(!d||!d.fields_dict.code) return {error:'no stocktake dialog'};"
         + find +
         # 只认弹窗写的那一段提示（`<p>`）：这个 HTML 格里本来就有框架加的隐藏字段名（`tooltip-content`）
         "const box=d.fields_dict.message.$wrapper;"
         "for(let k=0;k<80&&!(box.find('p').text()||'').trim();k++) await new Promise(r=>setTimeout(r,250));"
         "await new Promise(r=>setTimeout(r,800)); await idle();"
         "return {message: (box.find('p').text()||'').trim(), expected: __('This is not a product barcode'),"
         " new_button: d.fields_dict.new_product.$wrapper.is(':visible'), entry: d.fields_dict.qty.$wrapper.is(':visible'),"
         " prompt: $('.modal.show .msgprint').length, camera_on: d.fields_dict.video.$wrapper.is(':visible')};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    seen = by.get("扫网址码") or {}
    return [
        {"case": "web address says it is not a product barcode", "seen": seen,
         "ok": bool(seen.get("message")) and seen.get("message") == seen.get("expected")},
        {"case": "no new product button, no entry, no prompt", "seen": seen,
         "ok": seen.get("new_button") is False and seen.get("entry") is False and not seen.get("prompt")},
        {"case": "camera keeps scanning", "seen": seen, "ok": not camera_code or seen.get("camera_on") is True},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
print(json.dumps({{"products": frappe.db.count("ERP Product"),
                  "by_url": frappe.db.count("ERP Product", {{"qr_payload": ["like", "https://appdb.tisi.go.th/%"]}}),
                  "entries": frappe.db.count("ERP Stock Count Entry", {{"count": {params['count']!r}}})}}))
""")
    return [{"case": "nothing created or counted from the web address", "saved": got,
             "ok": got["by_url"] == 0 and got["entries"] == 0}]
