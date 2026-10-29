"""流程用例：ERP 盘点扫码 → 认出商品 → 出录入框 → 记数 → 落库（起因：10-28 polymer 手机盘点扫码查到商品却出不来录入框）。

每套数据都跑：用数据清单（`datasets.py` 的输出）里每一种认法的商品，在盘点详情页点「开始盘点」，在录入框里输码查找；
普通商品要出录入区、显示对的品名，「编码」栏显示内部编码（Express 同步来的是 Express 编码），都没有就是系统编码
（盘点菜单做完整 S2）；填数量保存后库里要有这条记录（商品、数量都对）。按台商品：新开户那家公司是普通类型，普通公司不按台（S0），
按数量照常出录入框记数；汽车 / 手机电器类型的公司没登记过台时才要说明、不出数量录入。
条码那件另走一遍假摄像头（条码画面），读码后同样要出录入区并能保存。全程页面不能有报错。
"""
from __future__ import annotations

import json
import uuid

TITLE = "ERP 盘点：扫码 / 输码认出商品、出录入框、记数落库"


def cases(manifest):
    """要试的码：(认法, 码, 应认出的商品, 预期)。"""
    out = []
    if manifest["dataset"] == "new":
        for key, item in manifest["products"].items():
            # 按台商品：新开户那家公司是普通类型，普通公司不按台、按数量盘（盘点菜单做完整 S0，用户 10-29），照样出录入框
            out.append({"key": key, "scan": item["scan"], "product": item["product"], "expect": "entry"})
    else:
        first, second = manifest["products"][0], manifest["products"][1]
        out.append({"key": "by_code", "scan": first["code"], "product": first["name"], "expect": "entry"})
        out.append({"key": "by_barcode", "scan": second["barcode"], "product": second["name"], "expect": "entry"})
    out.append({"key": "unknown", "scan": "NO-SUCH-CODE-" + uuid.uuid4().hex[:4], "product": None, "expect": "not_found"})
    return out


def prepare(lab, manifest):
    """开一份新盘点（库存岗，数据清单里那家公司的第一个仓），回参数。"""
    company = manifest.get("company") or manifest["companies"][0]
    warehouse = next(w["name"] for w in manifest["warehouses"] if w.get("company", company) == company)
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import count
from pearnly_erp_portal.masters import company as companies
frappe.set_user("stock@erp.regression")
companies.set_active_company({company!r})
doc = count.create_count(title="flow " + {uuid.uuid4().hex[:6]!r}, warehouse={warehouse!r})
frappe.db.commit()
print(json.dumps({{"count": doc["name"]}}))
""")
    found = cases(manifest)
    # 录入框「编码」栏应显示的值：内部编码 → Express 编码 → 系统编码（`products.contracts.count_code`，S2）
    codes = lab.python(manifest["site"], f"""
rows = frappe.get_all("ERP Product", filters={{"name": ["in", {[c["product"] for c in found if c["product"]]!r}]}},
                      fields=["name", "internal_code", "express_code", "code"], limit_page_length=50)
print(json.dumps({{r.name: r.internal_code or r.express_code or r.code or "" for r in rows}}))
""")
    for case in found:
        case["code"] = codes.get(case["product"] or "", "")
    return {"count": out["count"], "company": company, "warehouse": warehouse, "cases": found}


# 按条件等，不按固定时长（机器忙时请求慢，固定等待会在结果回来前点下一步）：请求都回来（jQuery 在途数连续两次为 0），最多 20 秒
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")


def steps(params, camera_code=None):
    """页面步骤：打开盘点详情 → 开始盘点 → 逐个码查找、检查、保存。"""
    out = [{"name": "开始盘点",
            "js": IDLE + "const b=[...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e=>e.offsetParent); "
                  "if(!b) return {error:'no start button'}; b.click(); "
                  "for(let k=0;k<80&&!(window.cur_dialog&&cur_dialog.fields_dict.code);k++) await new Promise(r=>setTimeout(r,250)); "
                  "await idle(); return b.innerText.trim();"}]
    for i, case in enumerate(params["cases"]):
        if camera_code and case["scan"] != camera_code:
            continue
        if camera_code:
            find = ("d.fields_dict.camera.$input.trigger('click'); "
                    "for (let k=0;k<80 && !d.fields_dict.qty.$wrapper.is(':visible');k++) await new Promise(r=>setTimeout(r,250)); await idle();")
        else:
            find = (f"d.set_value('code', {json.dumps(case['scan'])}); await new Promise(r=>setTimeout(r,300)); "
                    "d.fields_dict.find.$input.trigger('click'); await new Promise(r=>setTimeout(r,300)); await idle();")
        out.append({"name": f"查找 {case['key']}", "js": IDLE + "const d=cur_dialog; if(!d||!d.fields_dict.code) return {error:'no stocktake dialog'}; "
                    + find + (
            "return {entry: d.fields_dict.qty.$wrapper.is(':visible'), product_name: d.get_value('product_name') || '', "
            "product_code: d.get_value('product_code') || '', "
            "message: (d.fields_dict.message.$wrapper.text()||'').trim().slice(0,200)};")})
        if case["expect"] == "entry":
            out.append({"name": f"保存 {case['key']}",
                        "js": IDLE + "const d=cur_dialog; if(!d||!d.fields_dict.qty.$wrapper.is(':visible')) return {error:'no entry to save'}; "
                              f"d.set_value('qty', {i + 2}); await new Promise(r=>setTimeout(r,300)); "
                              "d.get_primary_btn().trigger('click'); await new Promise(r=>setTimeout(r,300)); await idle(); "
                              "return {saved: true, entry_after: d.fields_dict.qty.$wrapper.is(':visible')};"})
    return out


def judge(params, results, camera_code=None):
    """页面上看到的对不对：每个码一条判断。"""
    checks, by_name = [], {r.get("step"): r for r in results}
    names = {}
    for case in params["cases"]:
        if camera_code and case["scan"] != camera_code:
            continue
        seen = (by_name.get(f"查找 {case['key']}") or {}).get("value") or {}
        if case["expect"] == "entry":
            ok = bool(seen.get("entry")) and bool(seen.get("product_name")) and seen.get("product_code") == case["code"]
            names[case["product"]] = seen.get("product_name")
        elif case["expect"] == "unit_refused":
            ok = not seen.get("entry") and bool(seen.get("message"))
        else:
            ok = not seen.get("entry") and bool(seen.get("message"))
        checks.append({"case": case["key"], "scan": case["scan"], "expect": case["expect"], "seen": seen, "ok": ok})
    return checks


def verify(lab, manifest, params, camera_code=None):
    """库里：每个要出录入框的码都留下一条盘点记录，商品和数量对。"""
    rows = lab.python(manifest["site"], f"""
rows = frappe.get_all("ERP Stock Count Entry", filters={{"count": {params['count']!r}, "voided": 0}}, fields=["product", "qty"],
                      order_by="creation asc", limit_page_length=50)
print(json.dumps([dict(r, qty=float(r.qty)) for r in rows]))
""")
    checks = []
    for i, case in enumerate(params["cases"]):
        if case["expect"] != "entry" or (camera_code and case["scan"] != camera_code):
            continue
        hit = [r for r in rows if r["product"] == case["product"]]
        checks.append({"case": case["key"], "saved": hit, "ok": len(hit) == 1 and hit[0]["qty"] == float(i + 2)})
    others = [r for r in rows if r["product"] not in {c["product"] for c in params["cases"] if c["expect"] == "entry"}]
    checks.append({"case": "no stray entries", "saved": others, "ok": not others})
    return checks
