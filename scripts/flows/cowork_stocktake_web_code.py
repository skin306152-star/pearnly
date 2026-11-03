"""流程用例：Cowork 盘点扫到网址码（泰国工业标准认证 TIS 码，同 ERP `erp.stocktake.web-code`，11-03）。

网址不是商品码：判断在共用层 `pearnly_core.desk.scan_codes`，两个产品同一份。盘点录入框输入 / 假摄像头读到
`https://appdb.tisi.go.th/Q/i.php?d=…`（码图 `scripts/smoke/codes/qr-tis-url.png`）→ 录入弹窗提示「这不是商品条码」、
不出录入区、没有原生提示框挡着、相机那一遍扫码器还开着（接着扫，先跳过这个码）；库里没有因此记数。
"""
from __future__ import annotations

import json
import uuid

from stocktake_lib import SCAN, type_code

TITLE = "Cowork 盘点：扫到网址码提示不是商品条码"
URL = "https://appdb.tisi.go.th/Q/i.php?d=PEARNLYFLOW0001"
PICTURE = "scripts/smoke/codes/qr-tis-url.png"
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")


def prepare(lab, manifest):
    tag = uuid.uuid4().hex[:6]
    out = lab.python(manifest["site"], f"""
import io
from openpyxl import load_workbook
from pearnly_cowork.stocktake import upload
frappe.set_user("admin@cowork.regression")
book = load_workbook(io.BytesIO(upload.template()))
book.active.append(["CWW{tag}", "Flow web code {tag}", "", "pcs", 5, "", "Main", "A-1", ""])
data = io.BytesIO(); book.save(data)
task = upload.create("flow web code {tag}", {str(uuid.uuid4())!r}, data.getvalue())
frappe.db.commit()
print(json.dumps({{"count": task["id"]}}))
""")
    # 相机那一遍喂的是 TIS 网址二维码图（`run.py` 认 `.png` 码图）
    return {**out, "cases": [{"key": "by_barcode", "scan": PICTURE}]}


def steps(params, camera_code=None):
    find = SCAN if camera_code else type_code(json.dumps(URL))
    return [
        {"name": "开始盘点", "js": IDLE +
         "const btn=()=>[...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e=>e.offsetParent);"
         "for(let k=0;k<80&&!btn();k++) await new Promise(r=>setTimeout(r,250)); const b=btn(); if(!b) return {error:'no start button'}; b.click();"
         "for(let k=0;k<80&&!(window.cur_dialog&&cur_dialog.fields_dict.code);k++) await new Promise(r=>setTimeout(r,250));"
         "await idle(); return b.innerText.trim();"},
        {"name": "扫网址码", "js": IDLE + "const d=cur_dialog; if(!d||!d.fields_dict.code) return {error:'no stocktake dialog'};"
         + find +
         # 只认弹窗写的那一段提示（`<p>`）：这个 HTML 格里本来就有框架加的隐藏字段名
         "const box=d.fields_dict.message.$wrapper;"
         "for(let k=0;k<80&&!(box.find('p').text()||'').trim();k++) await new Promise(r=>setTimeout(r,250));"
         "await new Promise(r=>setTimeout(r,800)); await idle();"
         "return {message: (box.find('p').text()||'').trim(), expected: __('This is not a product barcode'),"
         " entry: d.fields_dict.qty.$wrapper.is(':visible'), prompt: $('.modal.show .msgprint').length,"
         " camera_on: d.fields_dict.video.$wrapper.is(':visible')};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    seen = by.get("扫网址码") or {}
    return [
        {"case": "web address says it is not a product barcode", "seen": seen,
         "ok": bool(seen.get("message")) and seen.get("message") == seen.get("expected")
         and seen.get("expected") != "This is not a product barcode"},
        {"case": "no entry, no prompt", "seen": seen, "ok": seen.get("entry") is False and not seen.get("prompt")},
        {"case": "camera keeps scanning", "seen": seen, "ok": not camera_code or seen.get("camera_on") is True},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
print(json.dumps({{"entries": frappe.db.count("Cowork Stocktake Entry", {{"task": {params['count']!r}}})}}))
""")
    return [{"case": "nothing counted from the web address", "saved": got, "ok": got["entries"] == 0}]
