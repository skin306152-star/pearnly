"""流程用例：ERP 入库 / 出库整页，明细行按码认商品 → 过账 → 账面变化 → 收货单 / 领料单打得出（fcb65cca 逐入口实测）。

每套数据的每一种认法（新开户：编码、条码、二维码内容、内部编码、Express 存货码；老数据：编码、条码）各走一遍：
入库新建页选仓 → 明细第一行在物料选择框里输码选中（手机宽度另用假摄像头扫条码那件）→ 带出品名 → 数量 4 → 提交；
再出库同一件 1。库里：这个仓这件商品的账面 +3；最新的入库单收货单（Goods Receipt Note）、出库单领料单（Goods Issue Slip）打得出、印着这件。
明细与打印显示内部编码（盘点菜单做完整 S6）在 `pearnly/stocktake-complete` 上，合入后在这条补断言。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, codes, company, js_value, ledger_case, stock_of, warehouses  # noqa: E402

TITLE = "ERP 入库 / 出库：按码认商品、过账、打印"


_CALLS = []  # 同一格里第几次 prepare：手输那遍、摄像头那遍各用一个仓（跑法在两遍开跑之前先各 prepare 一次）


def prepare(lab, manifest):
    """每遍用一个不带库位清单的仓（新开户：Express 同步来的 02、03 号仓；老数据：本地公司现建一个仓），记下每件的码与过账前账面。"""
    _CALLS.append(1)
    if manifest.get("express_linked"):
        code = "02" if len(_CALLS) == 1 else "03"
        warehouse = next(w for w in warehouses(manifest) if w.get("express_code") == code)
    else:
        warehouse = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import warehouses
frappe.set_user("admin@erp.regression")
made = warehouses.create_warehouse(warehouse_name="คลังโฟลว์ " + frappe.generate_hash(length=6), company={company(manifest)!r})
frappe.db.commit()
print(json.dumps({{"name": made["name"]}}))
""")
    cases = []
    for key, item in codes(manifest).items():
        info = lab.python(manifest["site"], f"""
row = frappe.db.get_value("ERP Product", {item['product']!r}, ["product_name", "internal_code", "express_code", "code"], as_dict=True)
print(json.dumps(row))
""")
        cases.append({"key": key, "scan": item["scan"], "product": item["product"], "name": info["product_name"],
                      "before": stock_of(lab, manifest["site"], item["product"], warehouse["name"])})
    return {"warehouse": warehouse["name"], "cases": cases}


def _fill(route, warehouse, case, qty, camera):
    find = ("const btn=line.querySelector('.pearnly-link-scan'); if(!btn) return {error:'no scan button'}; btn.click();"
            if camera else
            f"const ok=await pick(line.querySelector('input'), {js_value(case['scan'])}, {js_value(case['name'])}); if(!ok) return {{error:'no match in the product picker'}};")
    return JS + f"""
frappe.set_route({js_value(route)}, 'new');
const page=await until(()=>pageOf({js_value(route)}),20000); if(!page) return {{error:'page did not open'}};
const head=await until(()=>page.querySelector('[data-fieldname="warehouse"] input'));
// 先等页面请求都回来：表头默认仓（本人上次用的仓）的 Link 校验是异步的，晚到会把刚设的仓盖回默认（10-31 出库落到上一遍的仓）
await idle(); setInput(head, {js_value(warehouse)}); await sleep(1200); await idle();
const line=await until(()=>page.querySelector('[data-fieldname="lines"] [data-fieldname="product"]')); if(!line) return {{error:'no line'}};
{find}
const desc=()=>{{const d=page.querySelector('[data-fieldname="lines"] [data-fieldname="description"] textarea, [data-fieldname="lines"] [data-fieldname="description"] input');return d&&d.value;}};
await until(desc, 25000); await idle();
setInput(page.querySelector('[data-fieldname="lines"] [data-fieldname="qty"] input'), '{qty}'); await sleep(600);
return {{description: desc()||''}};
"""


def _submit(route):
    return JS + f"""
const page=pageOf({js_value(route)}); if(!page) return {{error:'page lost'}};
const btn=all('.page-actions .primary-action, .page-actions .btn-primary', page)[0]; if(!btn) return {{error:'no submit button'}};
btn.click();
const back=await until(()=>frappe.get_route().length===1 && frappe.get_route()[0]==={js_value(route)}, 25000);
await idle();
return {{submitted: !!back, msg: msg(), alerts: alerts()}};
"""


def steps(params, camera_code=None):
    out = []
    for case in params["cases"]:
        if camera_code and case["scan"] != camera_code:
            continue
        for route, qty in (("pearnly-stock-in", 4), ("pearnly-stock-out", 1)):
            label = "入库" if route == "pearnly-stock-in" else "出库"
            out.append({"name": f"{label}填单 {case['key']}", "js": _fill(route, params["warehouse"], case, qty, bool(camera_code))})
            out.append({"name": f"{label}提交 {case['key']}", "js": _submit(route)})
    # 带单号打开（ERP 版 CC「待处理」里的出入库单开到这里，评估 2026-11-02 §12 第 6 条）：回到列表、弹出这张单
    out.append({"name": "带单号打开", "js": JS + """
const rows=await call('pearnly_erp_portal.inventory.api.api_list_stock_entries', {direction:'in', status:'submitted', start:0, limit:1}, 'GET');
const row=(rows||[])[0]; if(!row) return {error:'no entry'};
frappe.set_route('pearnly-stock-in', row.name);
const shown=await until(()=>window.cur_dialog&&cur_dialog.$wrapper.is(':visible')&&frappe.get_route().length===1, 20000); await idle();
const text=shown?(cur_dialog.$body.text()||''):'';
const value=shown&&cur_dialog.fields_dict.doc_no?cur_dialog.get_value('doc_no'):'';
if(shown) cur_dialog.hide();
return {name: row.name, doc_no: row.doc_no, shown: !!shown, value, route: frappe.get_route(), express: text.includes('Express')};
"""})
    return out


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    checks = []
    for case in params["cases"]:
        if camera_code and case["scan"] != camera_code:
            continue
        for label in ("入库", "出库"):
            filled, sent = by.get(f"{label}填单 {case['key']}", {}), by.get(f"{label}提交 {case['key']}", {})
            checks.append({"case": f"{label} {case['key']}", "seen": {**filled, **sent},
                           "ok": filled.get("description") == case["name"] and bool(sent.get("submitted"))})
    opened = by.get("带单号打开", {})
    checks.append({"case": "带单号打开：回到列表并弹出这张单", "seen": opened,
                   "ok": bool(opened.get("shown")) and opened.get("value") == opened.get("doc_no")
                   and opened.get("route") == ["pearnly-stock-in"]})
    return checks


def verify(lab, manifest, params, camera_code=None):
    """账面 +3（入 4 出 1）；这件最新的收货单 / 领料单打得出、印着这件的品名。"""
    checks = []
    for case in params["cases"]:
        if camera_code and case["scan"] != camera_code:
            continue
        after = stock_of(lab, manifest["site"], case["product"], params["warehouse"])
        checks.append({"case": f"账面 {case['key']}", "seen": {"before": case["before"], "after": after}, "ok": after == case["before"] + 3})
        printed = lab.python(manifest["site"], f"""
out = {{}}
for direction, fmt in (("in", "Pearnly Goods Receipt Note"), ("out", "Pearnly Goods Issue Slip")):
    names = frappe.db.sql('''select e.name from `tabERP Stock Entry` e join `tabERP Stock Entry Line` l on l.parent = e.name
        where e.direction = %s and e.status = 'submitted' and l.product = %s order by e.creation desc limit 1''', (direction, {case['product']!r}))
    html = frappe.get_print("ERP Stock Entry", names[0][0], fmt, no_letterhead=1) if names else ""
    out[direction] = {{"entry": names[0][0] if names else None, "has_name": {case['name']!r} in html}}
print(json.dumps(out))
""")
        checks.append({"case": f"打印 {case['key']}", "seen": printed,
                       "ok": all(v["entry"] and v["has_name"] for v in printed.values())})
        # 账本栏是通用列：接了 Express 的公司，这两张单排进账本（ledger_target = express）；没接的不排
        checks.append(ledger_case(lab, manifest["site"], "ERP Stock Entry",
                                  [v["entry"] for v in printed.values() if v["entry"]], bool(manifest.get("express_linked"))))
    return checks
