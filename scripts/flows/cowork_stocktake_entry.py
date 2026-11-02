"""流程用例：Cowork 连续盘点录入（11-02 起一个框：打字选物料、回车按码认、框内扫码图标开扫码器，扫完自动接下一项）。

每套数据现场上传一份小盘点（两件：一件带条码，一件不带）：
- 输码回车认出那件 → 出录入区、扫码器没开；保存后（按钮叫「继续」）回到框里、不开相机；
- 框里打第二件名称的一段 → 下拉只出本次任务的物料（服务端按页查），选中直接出录入区；保存后同样回到框里；
- 手机宽另走一遍假摄像头：点框内扫码图标 → 读到条码 → 扫码器关、出录入区；点「继续」后扫码器自动再开（跳过刚扫的码），能手动关。
库里每件各一条记录，数量对。
"""
from __future__ import annotations

import json
import random
import uuid

from stocktake_lib import SCAN, type_code

TITLE = "Cowork 盘点：一个框打字 / 输码 / 扫码，扫完自动接下一项"


def _ean(seed: int) -> str:
    """店内码段（200 开头）的 EAN-13，假摄像头画面用它。"""
    first12 = "200" + str(seed).zfill(9)[-9:]
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12))
    return first12 + str((10 - total % 10) % 10)


def prepare(lab, manifest):
    tag = uuid.uuid4().hex[:6]
    barcode = _ean(random.randint(1, 999999999))
    rows = [(f"CWF{tag}A", f"Flow scan A {tag}", barcode, "pcs", 5), (f"CWF{tag}B", f"Flow typed B {tag}", "", "pcs", 3)]
    out = lab.python(manifest["site"], f"""
import io
from openpyxl import load_workbook
from pearnly_cowork.stocktake import upload
frappe.set_user("admin@cowork.regression")
book = load_workbook(io.BytesIO(upload.template()))
for code, name, barcode, unit, qty in {rows!r}:
    book.active.append([code, name, barcode, unit, qty, "", "Main", "A-1", ""])
data = io.BytesIO(); book.save(data)
task = upload.create("flow entry {tag}", {str(uuid.uuid4())!r}, data.getvalue())
frappe.db.commit()
items = frappe.get_all("Cowork Stocktake Item", filters={{"task": task["id"]}}, fields=["name", "product_code"],
                       limit_page_length=10)
print(json.dumps({{"count": task["id"], "items": {{r.product_code: r.name for r in items}}}}))
""")
    first, second = rows
    return {"count": out["count"], "first": {"code": first[0], "name": first[1], "item": out["items"][first[0]]},
            "second": {"name": second[1], "item": out["items"][second[0]]},
            # 相机那一遍读的码（`run.py` 按 key 找 by_barcode）
            "cases": [{"key": "by_barcode", "scan": barcode}]}


IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")
SHOWN = ("const shown=()=>({entry: d.fields_dict.qty.$wrapper.is(':visible'), product_name: d.get_value('product_name')||'', "
         "camera_open: d.fields_dict.video.$wrapper.is(':visible')});")
SAVE = ("d.set_value('qty', QTY); await new Promise(r=>setTimeout(r,300)); d.get_primary_btn().trigger('click'); "
        "await new Promise(r=>setTimeout(r,300)); await idle(); await new Promise(r=>setTimeout(r,800)); "
        "const camera_after=d.fields_dict.video.$wrapper.is(':visible'); "
        "if (camera_after) d.fields_dict.close_camera.$input.trigger('click'); await new Promise(r=>setTimeout(r,300)); "
        "return {...seen, saved: true, camera_after, focus_code: document.activeElement === d.fields_dict.code.input, "
        "camera_closed: !d.fields_dict.video.$wrapper.is(':visible'), button: d.get_primary_btn().text().trim()};")


def steps(params, camera_code=None):
    start = {"name": "开始盘点", "js": IDLE + "const btn=()=>[...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e=>e.offsetParent); "
             "for(let k=0;k<80&&!btn();k++) await new Promise(r=>setTimeout(r,250)); const b=btn(); if(!b) return {error:'no start button'}; b.click(); "
             "for(let k=0;k<80&&!(window.cur_dialog&&cur_dialog.fields_dict.code);k++) await new Promise(r=>setTimeout(r,250)); await idle(); "
             "const d=cur_dialog; return {camera_open: d.fields_dict.video.$wrapper.is(':visible'), "
             "scan_icon: d.fields_dict.code.$wrapper.find('.pearnly-link-scan').length};"}
    head = IDLE + "const d=cur_dialog; if(!d||!d.fields_dict.code) return {error:'no stocktake dialog'};" + SHOWN
    if camera_code:
        return [start, {"name": "扫码", "js": head + SCAN
                        + "for (let k=0;k<80 && !d.fields_dict.qty.$wrapper.is(':visible');k++) await new Promise(r=>setTimeout(r,250)); await idle();"
                        "const seen=shown();" + SAVE.replace("QTY", "4")}]
    return [start,
            {"name": "输码", "js": head + type_code(json.dumps(params["first"]["code"])) + "await idle();"
             "for (let k=0;k<40 && !d.fields_dict.qty.$wrapper.is(':visible');k++) await new Promise(r=>setTimeout(r,250));"
             "const seen=shown();" + SAVE.replace("QTY", "2")},
            {"name": "打字选物料", "js": head + "const c=d.fields_dict.code; c.$input.trigger('focus');"
             f"c.$input.val({json.dumps(params['second']['name'][:12])}); c.input.dispatchEvent(new Event('input', {{bubbles: true}}));"
             "for (let k=0;k<40 && !(c.awesomplete && c.awesomplete.ul && c.awesomplete.ul.children.length);k++) await new Promise(r=>setTimeout(r,250));"
             "const items=[...(c.awesomplete?.ul?.children||[])].map(li=>(li.innerText||'').trim());"
             f"const hit=[...(c.awesomplete?.ul?.children||[])].find(li=>(li.innerText||'').includes({json.dumps(params['second']['name'])}));"
             "if (hit) hit.click();"
             "for (let k=0;k<40 && !d.fields_dict.qty.$wrapper.is(':visible');k++) await new Promise(r=>setTimeout(r,250)); await idle();"
             "const seen={...shown(), items};" + SAVE.replace("QTY", "1")}]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    opened = by.get("开始盘点") or {}
    checks = [{"case": "the dialog opens with the box and its scan icon, camera closed", "seen": opened,
               "ok": opened.get("scan_icon") == 1 and opened.get("camera_open") is False}]
    if camera_code:
        seen = by.get("扫码") or {}
        checks += [{"case": "a scanned product shows its fields and the scanner closes", "seen": seen,
                    "ok": seen.get("entry") is True and seen.get("product_name") == params["first"]["name"] and seen.get("camera_open") is False},
                   {"case": "continue reopens the scanner and it can be closed", "seen": seen,
                    "ok": seen.get("camera_after") is True and seen.get("camera_closed") is True}]
        return checks
    for step, name in (("输码", params["first"]["name"]), ("打字选物料", params["second"]["name"])):
        seen = by.get(step) or {}
        checks += [{"case": f"{step}: the product shows its fields, camera stays closed", "seen": seen,
                    "ok": seen.get("entry") is True and seen.get("product_name") == name and seen.get("camera_open") is False},
                   {"case": f"{step}: continue goes back to the box without the camera", "seen": seen,
                    "ok": seen.get("camera_after") is False and seen.get("focus_code") is True}]
    items = (by.get("打字选物料") or {}).get("items") or []
    checks.append({"case": "typing lists only products of this count", "seen": items,
                   "ok": bool(items) and all(params["second"]["name"][:12] in item or "Flow" in item for item in items)
                   and any(params["second"]["name"] in item for item in items)})
    return checks


def verify(lab, manifest, params, camera_code=None):
    rows = lab.python(manifest["site"], f"""
rows = frappe.get_all("Cowork Stocktake Entry", filters={{"task": {params['count']!r}, "voided": 0}}, fields=["item", "quantity"],
                      limit_page_length=20)
print(json.dumps([{{"item": r.item, "qty": float(r.quantity)}} for r in rows]))
""")
    expect = {params["first"]["item"]: 4.0} if camera_code else {params["first"]["item"]: 2.0, params["second"]["item"]: 1.0}
    got = {r["item"]: r["qty"] for r in rows}
    # 录入框的物料查询：没有盘点权限的岗位查不到；有权限的只回本任务的行
    guard = lab.python(manifest["site"], f"""
from pearnly_cowork.stocktake import desk_api
out = {{}}
for user in ("work@cowork.regression", "stocktake@cowork.regression"):
    frappe.set_user(user)
    try:
        out[user] = [row[0] for row in desk_api.api_stock_count_item_search("Cowork Stocktake Item", "", "name", 0, 10,
                                                                         {{"count": {params['count']!r}}})]
    except frappe.PermissionError:
        out[user] = "denied"
print(json.dumps(out))
""")
    mine = {params["first"]["item"], params["second"]["item"]}
    return [{"case": "each counted product saved once with its quantity", "saved": rows, "ok": got == expect and len(rows) == len(expect)},
            {"case": "item search refused without stocktake rights, only this count's rows with them", "saved": guard,
             "ok": guard.get("work@cowork.regression") == "denied" and set(guard.get("stocktake@cowork.regression") or []) == mine}]
