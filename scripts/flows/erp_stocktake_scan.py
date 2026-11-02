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

from stocktake_lib import SCAN, type_code

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
    # 打字选物料用的那件：本次盘点里有、别的用例没用到的（免得同一件记两条）；打它名称里的一段
    typed = lab.python(manifest["site"], f"""
row = frappe.db.sql("select product, product_name from `tabERP Stock Count Item` where `count`=%s and product not in %s "
                    "order by product_name limit 1", ({out['count']!r}, {tuple(c['product'] for c in found if c['product']) or ('',)!r}),
                    as_dict=True)
print(json.dumps(row[0] if row else {{}}))
""")
    return {"count": out["count"], "company": company, "warehouse": warehouse, "cases": found,
            "typed_product": typed.get("product"), "typed_name": typed.get("product_name") or "",
            "typed": (typed.get("product_name") or "")[:6]}


# 按条件等，不按固定时长（机器忙时请求慢，固定等待会在结果回来前点下一步）：请求都回来（jQuery 在途数连续两次为 0），最多 20 秒
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")


def steps(params, camera_code=None):
    """页面步骤：打开盘点详情 → 开始盘点 → 逐个码查找、检查、保存。"""
    out = [{"name": "开始盘点",
            "js": IDLE + "const btn=()=>[...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e=>e.offsetParent); "
                  # 站点刚迁移完第一次打开页面很慢：等按钮渲染出来再点（最多 20 秒）
                  "for(let k=0;k<80&&!btn();k++) await new Promise(r=>setTimeout(r,250)); const b=btn(); "
                  "if(!b) return {error:'no start button'}; b.click(); "
                  "for(let k=0;k<80&&!(window.cur_dialog&&cur_dialog.fields_dict.code);k++) await new Promise(r=>setTimeout(r,250)); "
                  "await idle(); return b.innerText.trim();"}]
    for i, case in enumerate(params["cases"]):
        if camera_code and case["scan"] != camera_code:
            continue
        if camera_code:
            # 框内右侧的扫码图标开扫码器（用户 11-02：扫码器平时收起，点图标才开）
            find = (SCAN + "for (let k=0;k<80 && !d.fields_dict.qty.$wrapper.is(':visible');k++) await new Promise(r=>setTimeout(r,250)); await idle();")
        else:
            # 扫码枪 / 手打的码：框里填上回车
            find = type_code(json.dumps(case['scan'])) + "await idle();"
        out.append({"name": f"查找 {case['key']}", "js": IDLE + "const d=cur_dialog; if(!d||!d.fields_dict.code) return {error:'no stocktake dialog'}; "
                    + find + (
            "return {entry: d.fields_dict.qty.$wrapper.is(':visible'), product_name: d.get_value('product_name') || '', "
            "product_code: d.get_value('product_code') || '', warehouse: d.get_value('warehouse') || '', "
            "camera_open: d.fields_dict.video.$wrapper.is(':visible'), "
            "message: (d.fields_dict.message.$wrapper.find('p').text()||'').trim().slice(0,200)};")})
        if case["expect"] == "entry":
            out.append({"name": f"保存 {case['key']}",
                        "js": IDLE + "const d=cur_dialog; if(!d||!d.fields_dict.qty.$wrapper.is(':visible')) return {error:'no entry to save'}; "
                              f"d.set_value('qty', {i + 2}); await new Promise(r=>setTimeout(r,300)); "
                              "d.get_primary_btn().trigger('click'); await new Promise(r=>setTimeout(r,300)); await idle(); "
                              "await new Promise(r=>setTimeout(r,800)); "
                              # 点「继续」后：扫码来的那件自动再开扫码器，打字来的回到框里、不开相机（用户 11-02）
                              "const camera_after = d.fields_dict.video.$wrapper.is(':visible'); "
                              "if (camera_after) d.fields_dict.close_camera.$input.trigger('click'); await new Promise(r=>setTimeout(r,300)); "
                              "return {saved: true, entry_after: d.fields_dict.qty.$wrapper.is(':visible'), camera_after, "
                              "button: d.get_primary_btn().text().trim(), focus_code: document.activeElement === d.fields_dict.code.input, "
                              "camera_closed: !d.fields_dict.video.$wrapper.is(':visible')};"})
    if not camera_code and params.get("typed_product"):
        # 打字找：框里打物料名称的一段，下拉出本次盘点的物料（服务端按页查），选中直接出这件的字段
        out.append({"name": "打字选物料", "js": IDLE + "const d=cur_dialog; if(!d||!d.fields_dict.code) return {error:'no stocktake dialog'}; "
                    "const c=d.fields_dict.code; c.$input.trigger('focus'); "
                    f"c.$input.val({json.dumps(params['typed'])}); c.input.dispatchEvent(new Event('input', {{bubbles: true}})); "
                    "for (let k=0;k<40 && !(c.awesomplete && c.awesomplete.ul && c.awesomplete.ul.children.length);k++) await new Promise(r=>setTimeout(r,250)); "
                    "const items=[...(c.awesomplete?.ul?.children||[])].map(li=>(li.innerText||'').trim()); "
                    f"const hit=[...(c.awesomplete?.ul?.children||[])].find(li=>(li.innerText||'').includes({json.dumps(params['typed_name'])})); "
                    "if (hit) { hit.click(); } "
                    "for (let k=0;k<40 && !d.fields_dict.qty.$wrapper.is(':visible');k++) await new Promise(r=>setTimeout(r,250)); await idle(); "
                    "const shown={items, entry: d.fields_dict.qty.$wrapper.is(':visible'), product_name: d.get_value('product_name')||'', "
                    "camera_open: d.fields_dict.video.$wrapper.is(':visible')}; "
                    "if (shown.entry) { d.set_value('qty', 1); await new Promise(r=>setTimeout(r,300)); d.get_primary_btn().trigger('click'); "
                    "await new Promise(r=>setTimeout(r,300)); await idle(); await new Promise(r=>setTimeout(r,500)); } "
                    "return {...shown, camera_after: d.fields_dict.video.$wrapper.is(':visible'), "
                    "focus_code: document.activeElement === d.fields_dict.code.input};"})
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
            # 实际仓库那格是仓库编号（账面行的仓）：写成仓库名称时实际仓位按它取选项会报「未找到仓库」（11-02 真机）
            ok = (bool(seen.get("entry")) and bool(seen.get("product_name")) and seen.get("product_code") == case["code"]
                  and seen.get("warehouse") == params["warehouse"])
            names[case["product"]] = seen.get("product_name")
        elif case["expect"] == "unit_refused":
            ok = not seen.get("entry") and bool(seen.get("message"))
        else:
            ok = not seen.get("entry") and bool(seen.get("message"))
        checks.append({"case": case["key"], "scan": case["scan"], "expect": case["expect"], "seen": seen, "ok": ok})
        if case["expect"] == "entry":
            # 读到物料扫码器就关；点「继续」后：扫码来的自动再开扫码器，打字来的回到框里、不开相机（用户 11-02）
            saved = (by_name.get(f"保存 {case['key']}") or {}).get("value") or {}
            checks.append({"case": f"{case['key']}: scanner closed once the product shows", "seen": seen.get("camera_open"),
                           "ok": seen.get("camera_open") is False})
            after = {"camera_after": saved.get("camera_after"), "focus_code": saved.get("focus_code"),
                     "camera_closed": saved.get("camera_closed")}
            checks.append({"case": f"{case['key']}: continue reopens the scanner after a scan, else back to the box",
                           "seen": after, "ok": (saved.get("camera_after") is True and saved.get("camera_closed") is True)
                           if camera_code else (saved.get("camera_after") is False and saved.get("focus_code") is True)})
    if not camera_code and params.get("typed_product"):
        seen = (by_name.get("打字选物料") or {}).get("value") or {}
        checks.append({"case": "typing lists products of this count and picking one opens its entry", "seen": seen,
                       "ok": any(params["typed_name"] in item for item in seen.get("items") or [])
                       and seen.get("entry") is True and seen.get("product_name") == params["typed_name"]
                       and seen.get("camera_open") is False})
        checks.append({"case": "after a typed item continue goes back to the box without the camera",
                       "seen": {k: seen.get(k) for k in ("camera_after", "focus_code")},
                       "ok": seen.get("camera_after") is False and seen.get("focus_code") is True})
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
    typed = params.get("typed_product") if not camera_code else None
    if typed:
        hit = [r for r in rows if r["product"] == typed]
        checks.append({"case": "typed pick saved", "saved": hit, "ok": len(hit) == 1 and hit[0]["qty"] == 1.0})
    # 录入框的物料查询（服务端按页查）：没有盘点权限的岗位查不到；有权限的只回这一页条数以内
    guard = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import api
out = {{}}
for user in ("sales@erp.regression", "stock@erp.regression"):
    frappe.set_user(user)
    try:
        out[user] = len(api.api_stock_count_product_search("ERP Product", "", "name", 0, 5, {{"count": {params['count']!r}}}))
    except frappe.PermissionError:
        out[user] = "denied"
print(json.dumps(out))
""")
    checks.append({"case": "product search refused without stocktake rights, paged with them", "saved": guard,
                   "ok": guard.get("sales@erp.regression") == "denied" and isinstance(guard.get("stock@erp.regression"), int)
                   and 0 < guard["stock@erp.regression"] <= 5})
    others = [r for r in rows if r["product"] not in {c["product"] for c in params["cases"] if c["expect"] == "entry"} | {typed}]
    checks.append({"case": "no stray entries", "saved": others, "ok": not others})
    return checks
