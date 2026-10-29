"""流程用例：ERP 销售单整页新建（手动录入）—— 单头选客户、明细在组合框里按码认商品（手机宽度另用假摄像头扫条码）→ 提交 → 出库
（fcb65cca 逐入口实测：销售与扫码、多仓 P1 单头仓）。

销售岗：新建先选「手动录入」，单头客户在组合框里输名字点下拉项（客户先按页面同一个接口建好），单头仓选这家公司的第一个仓；
明细第一行输条码那件的码选中（手机宽度那一遍用假摄像头读条码）→ 数量 1、单价 120 → 提交。
库里：这张销售单已提交、客户是建的那个、明细一行就是这件；这个仓这件的账面少 1。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, codes, company, js_value, stock_of, warehouses  # noqa: E402

TITLE = "ERP 销售单：手动录入、组合框按码认商品、提交出库"


def prepare(lab, manifest):
    item = codes(manifest)["by_barcode"]
    warehouse = warehouses(manifest)[0]["name"]
    name = lab.python(manifest["site"], f"print(json.dumps({{'name': frappe.db.get_value('ERP Product', {item['product']!r}, 'product_name')}}))")["name"]
    return {"company": company(manifest), "warehouse": warehouse, "product": item["product"], "scan": item["scan"], "name": name,
            "customer_name": "ลูกค้าโฟลว์ " + uuid.uuid4().hex[:6],
            "before": stock_of(lab, manifest["site"], item["product"], warehouse),
            "cases": [{"key": "by_barcode", "scan": item["scan"]}]}


def steps(params, camera_code=None):
    find = ("const btn=choice.querySelector('.pearnly-link-scan'); if(!btn) return {error:'no scan button'}; btn.click();"
            if camera_code else
            f"if(!await pick(choice.querySelector('input, textarea'), {js_value(params['scan'])}, {js_value(params['name'])})) return {{error:'no match in the item picker'}};")
    return [
        {"name": "单头", "js": JS + f"""
frappe.set_route('pearnly-sales', 'new');
const page=await until(()=>pageOf('pearnly-sales'), 20000); if(!page) return {{error:'page did not open'}};
await until(()=>page.querySelector('[data-fieldname="doc_date"] input'));
const manual=await until(()=>all('label, .checkbox, .pearnly-entry-choice, div', page).find(el=>el.children.length<6&&/^手动录入/.test(el.textContent.trim())), 8000);
if (manual) {{ (manual.querySelector('input[type=checkbox], input[type=radio]')||manual).click(); await sleep(1500); await idle(); }}
await call('pearnly_erp_portal.masters.api.api_create_customer', {{customer_name: {js_value(params['customer_name'])}}});
const head=[...page.querySelectorAll('[data-fieldname="entry_choice"]')].find(el=>!el.closest('[data-fieldname="lines"]'));
if(!head) return {{error:'no customer picker'}};
if(!await pick(head.querySelector('input, textarea'), {js_value(params['customer_name'])})) return {{error:'customer not in the picker'}};
await idle();
setInput(page.querySelector('[data-fieldname="warehouse"] input'), {js_value(params['warehouse'])}); await sleep(1200); await idle();
return {{manual: !!manual, customer: (page.querySelector('[data-fieldname="customer"] input')||{{}}).value||''}};"""},
        {"name": "明细", "js": JS + f"""
const page=pageOf('pearnly-sales'); const lines=page&&page.querySelector('[data-fieldname="lines"]'); if(!lines) return {{error:'no lines'}};
const choice=await until(()=>lines.querySelector('[data-fieldname="entry_choice"]'), 10000); if(!choice) return {{error:'no item picker'}};
{find}
const desc=()=>(lines.querySelector('[data-fieldname="description"] textarea, [data-fieldname="description"] input')||{{}}).value;
await until(desc, 25000); await idle();
setInput(lines.querySelector('[data-fieldname="qty"] input'), '1'); await sleep(300);
setInput(lines.querySelector('[data-fieldname="unit_price"] input'), '120'); await sleep(800); await idle();
return {{description: desc()||''}};"""},
        {"name": "提交", "js": JS + """
const btn=all('.page-actions .primary-action, .page-actions .btn-primary')[0]; if(!btn) return {error:'no submit button'};
btn.click(); await sleep(1500);
const yes=all('.modal.show .btn-primary').find(b=>/是|确定|提交|Yes|Submit/.test(b.textContent)); if (yes) yes.click();
const moved=await until(()=>frappe.get_route()[1] && frappe.get_route()[1]!=='new', 25000); await idle();
return {moved: !!moved, route: frappe.get_route(), msg: msg()};"""},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    head, line, sent = by.get("单头", {}), by.get("明细", {}), by.get("提交", {})
    params["sales_doc"] = (sent.get("route") or [None, None])[1]
    return [{"case": "手动录入、选客户", "seen": head, "ok": head.get("manual") is True},
            {"case": "按码认出商品", "seen": line, "ok": params["name"] in (line.get("description") or "")},
            {"case": "提交", "seen": sent, "ok": bool(sent.get("moved"))}]


def verify(lab, manifest, params, camera_code=None):
    row = lab.python(manifest["site"], f"""
doc = frappe.db.get_value("ERP Sales Document", {params.get('sales_doc')!r}, ["status", "customer"], as_dict=True) or {{}}
cust = frappe.db.get_value("ERP Customer", doc.get("customer"), "customer_name") if doc.get("customer") else None
lines = frappe.get_all("ERP Sales Document Line", filters={{"parent": {params.get('sales_doc')!r}}}, fields=["product", "qty"]) if doc else []
print(json.dumps({{"status": doc.get("status"), "customer": cust, "lines": [dict(r, qty=float(r.qty)) for r in lines]}}))
""")
    after = stock_of(lab, manifest["site"], params["product"], params["warehouse"])
    return [{"case": "销售单已提交、客户与明细", "seen": row, "ok": row["status"] == "submitted" and row["customer"] == params["customer_name"]
             and [r["product"] for r in row["lines"]] == [params["product"]]},
            {"case": "账面少 1", "seen": {"before": params["before"], "after": after}, "ok": after == params["before"] - 1}]
