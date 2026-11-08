"""流程用例：ERP 收发存报表导出（照 MR.ERP 三种：合并全部仓 / 汇总 / 分仓；Excel / PDF，11-08）。

库存岗打开收发存报表页 → 页头「导出」→ 弹窗选报表、格式，物料「从 / 到」都选同一件（老数据商品多，限定一件才是当场给的小文件）→
导出：汇总 Excel、合并全部仓 Excel、分仓 PDF 各一次。页面发出的是 POST、回 200；Excel 里有报表名、这件的代码、
期末数量与库里这家公司各仓账面之和一致；PDF 是 PDF。小文件不建导出任务。prepare 保证这件在这家公司有结存（没有就入库 5 件），
不靠别的流程留下的库存。算法逐栏的对照在 tests/test_erp_stock_card_export.py（按 MR.ERP 源码手算）。
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk_list import AWAIT_EXPORT, CAPTURE_EXPORTS, workbook_text  # noqa: E402
from erp_lib import JS, codes, company, js_value  # noqa: E402

TITLE = "ERP 收发存导出：三种报表各导一次、文件对得上账面"
API = "pearnly_erp_portal.inventory.api.api_stock_card_export"
# (步骤名, 报表, 格式, 文件里要有的报表名（实验室账号界面是中文）)
EXPORTS = (("汇总 Excel", "summary", "xlsx", "收发存报表 - 汇总"),
           ("合并全部仓 Excel", "all", "xlsx", "收发存报表"),
           ("分仓 PDF", "by_warehouse", "pdf", ""))


def prepare(lab, manifest):
    """数据清单里按编码那件：这家公司各仓的结存之和；为 0 就按正式入库在第一个启用的仓记 5 件。库存岗的当前公司切到这家。"""
    co, product = company(manifest), codes(manifest)["by_code"]["product"]
    return lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import documents, warehouses
from pearnly_erp_portal.masters import company as companies
co, product = {co!r}, {product!r}
names = warehouses.names_of(co)
held = lambda: float(frappe.db.sql("select ifnull(sum(qty_on_hand), 0) from `tabERP Stock Balance` where product=%s and warehouse in %s",
                                   (product, tuple(names)))[0][0])
if not held():
    frappe.set_user("admin@erp.regression")
    companies.set_active_company(co)
    first = frappe.get_all("ERP Warehouse", filters={{"company": co, "is_active": 1}}, pluck="name", order_by="creation asc", limit=1)[0]
    shelf = frappe.get_all("ERP Warehouse Location", filters={{"parent": first, "parenttype": "ERP Warehouse"}}, pluck="location_name",
                           order_by="idx asc", limit=1)
    documents.create_entry(direction="in", posting_date=frappe.utils.nowdate(), warehouse=first, reason="flow stock card export",
                           submit=True, lines=[{{"product": product, "qty": 5, "unit_cost": 10, "location": shelf[0] if shelf else ""}}])
frappe.set_user("stock@erp.regression")
companies.set_active_company(co)
frappe.db.commit()
row = frappe.db.get_value("ERP Product", product, ["code", "express_code"], as_dict=True)
print(json.dumps({{"product": product, "code": row.express_code or row.code, "qty": held(),
                  "jobs": frappe.db.count("Pearnly Export"), "cases": []}}))
""")


def _export(params, report, output):
    values = {"report": report, "format": output, "date_from": "2000-01-01", "product_from": params["product"],
              "product_to": params["product"]}
    return JS + CAPTURE_EXPORTS + f"""
frappe.set_route('pearnly-stock-card');
const page=await until(()=>document.querySelector('[data-page-route="pearnly-stock-card"]'),20000);
if(!page) return {{error:'page did not open'}};
await idle();
const button=await until(()=>[...document.querySelectorAll('.page-head button, .page-actions button')].find(e=>e.offsetParent&&e.innerText.trim()==='导出'));
if(!button) return {{error:'no export button'}};
button.click();
const dialog=await until(()=>window.cur_dialog&&cur_dialog.fields_dict.report&&cur_dialog.display&&cur_dialog);
if(!dialog) return {{error:'no export dialog'}};
await dialog.set_values({js_value(values)});
await sleep(800);
dialog.get_primary_btn().click();
{AWAIT_EXPORT}
await sleep(500);
return got?{{...got, open: !!(window.cur_dialog&&cur_dialog.display)}}:{{error:'no export response'}};
"""


def steps(params, camera_code=None):
    return [{"name": name, "js": _export(params, report, output)} for name, report, output, _title in EXPORTS]


def judge(params, results, camera_code=None):
    by_step = {r.get("step"): r.get("value") or {} for r in results}
    checks = []
    for name, report, output, title in EXPORTS:
        seen = by_step.get(name) or {}
        detail = {key: seen.get(key) for key in ("url", "method", "status", "type", "size", "error", "open")}
        ok = API in str(seen.get("url")) and seen.get("method") == "POST" and seen.get("status") == 200 and not seen.get("open")
        if ok and output == "xlsx":
            try:
                _sheets, text = workbook_text(seen["b64"])
                detail["has"] = {"title": title in text, "code": params["code"] in text}
                ok = all(detail["has"].values())
            except Exception as error:  # noqa: BLE001 - 打不开就是不通过
                detail["unreadable"], ok = str(error)[:200], False
        if ok and output == "pdf":
            ok = base64.b64decode(seen.get("b64") or "")[:5] == b"%PDF-"
        checks.append({"case": f"export {report} {output}", "seen": detail, "ok": ok})
    return checks


def verify(lab, manifest, params, camera_code=None):
    found = lab.python(manifest["site"], f"""
from openpyxl import load_workbook
from io import BytesIO
from pearnly_erp_portal.inventory import card_export
from pearnly_erp_portal.masters import company as companies
frappe.set_user("stock@erp.regression")
frappe.local.lang = "zh"
built = card_export.build(report="summary", format="xlsx", date_from="2000-01-01", date_to=frappe.utils.nowdate(),
                          product_from={params["product"]!r}, product_to={params["product"]!r})
sheet = load_workbook(BytesIO(built["content"])).active
rows = [[cell.value for cell in row] for row in sheet.iter_rows(min_row=7)]
line = next((row for row in rows if row[0] == {params["code"]!r}), None)
print(json.dumps({{"closing": float(line[11]) if line else None, "jobs": frappe.db.count("Pearnly Export")}}))
""")
    return [{"case": "summary closing quantity equals the books of this company", "saved": found,
             "ok": found["closing"] == params["qty"]},
            {"case": "small exports are served at once", "saved": found, "ok": found["jobs"] == params["jobs"]}]


if __name__ == "__main__":
    print(json.dumps([step["name"] for step in steps({"product": "PRD-1", "code": "A1"})], ensure_ascii=False))
