"""流程用例：ERP 导出 —— 销售、采购、库存、已过账盘点、班次、首页账本，各点一次「导出」，拿到的文件要对
（起因：10-29 导出可能在 GET 里建导出任务再手动 commit；改成共用的 `p.export_file` 一律 POST、13 个导出接口只收 POST）。

每套数据、每个有数据可导的页面：在页面上点它自己的导出（页头按钮或「…」菜单、班次是勾选后的「导出选中」），
页面发出的导出请求是 POST、回 200、是能打开的 xlsx，文件里有库里这家公司的那些单号 / 商品码（老数据）；
导出不建导出任务（小的当场给文件）。没有可导的（新开户没有过账的盘点、没有班次）就不点那一页。
"""
from __future__ import annotations

import json

from desk_list import ACTION, AWAIT_EXPORT, CAPTURE_EXPORTS, IDLE, workbook_text

TITLE = "ERP 导出：各页点导出、走 POST、文件里有该有的单据"
# 页面（路由）、按钮文字（实验室账号界面是中文）、导出接口
PAGES = {
    "sales": ("pearnly-sales", "导出", "pearnly_erp_portal.sales.api.api_sales_export"),
    "purchase": ("pearnly-purchase", "导出", "pearnly_erp_portal.purchasing.api.api_purchase_export"),
    "inventory": ("pearnly-inventory", "导出", "pearnly_erp_portal.inventory.api.api_stock_export"),
    "stock_count": ("pearnly-stock-count/{count}", "导出", "pearnly_erp_portal.inventory.api.api_stock_count_export"),
    "shifts": ("pearnly-pos-shifts", "导出选中", "pearnly_erp_portal.cashier.api.api_shifts_export"),
    "ledger": ("pearnly-home", "导出明细", "pearnly_core.billing_www.export_records"),
}


def prepare(lab, manifest):
    found = lab.python(manifest["site"], """
from pearnly_erp_portal.masters import company as companies
company = companies.active_company("admin@erp.regression")
warehouses = frappe.get_all("ERP Warehouse", filters={"company": company}, pluck="name")
sales = frappe.get_all("ERP Sales Document", filters={"company": company}, pluck="system_no", limit_page_length=5)
purchases = frappe.get_all("Purchase Document", filters={"company": company, "status": "submitted"}, pluck="doc_no",
                           limit_page_length=5)
stocked = frappe.get_all("ERP Stock Balance", filters={"warehouse": ["in", warehouses or [""]], "qty_on_hand": ["!=", 0]},
                         pluck="product", limit_page_length=5)
codes = [frappe.db.get_value("ERP Product", name, "code") for name in stocked]
posted = frappe.get_all("ERP Stock Count", filters={"warehouse": ["in", warehouses or [""]], "status": "Posted"}, pluck="name",
                        order_by="creation asc", limit_page_length=1)
counted = frappe.get_all("ERP Stock Count Entry", filters={"count": posted[0], "voided": 0}, pluck="product",
                         limit_page_length=5) if posted else []
from pearnly_erp_portal.cashier import contracts as cashier
shifts = frappe.get_all("ERP Shift", filters={"status": cashier.CLOSED}, pluck="name", limit_page_length=1)
print(json.dumps({"company": company, "sales": sales, "purchase": [n for n in purchases if n], "inventory": codes,
                  "count": posted[0] if posted else "", "stock_count": [frappe.db.get_value("ERP Product", n, "code") for n in counted],
                  "shifts": shifts, "jobs": frappe.db.count("Pearnly Export")}))
""")
    pages = ["sales", "purchase", "inventory", "ledger"]
    if found["count"]:
        pages.append("stock_count")
    if found["shifts"]:
        pages.append("shifts")
    return {**found, "pages": pages}


def _click(key, params):
    route, label, _api = PAGES[key]
    route = route.format(count=params.get("count", ""))
    if key == "shifts":
        # 班次是勾选后右上角「导出选中的 N 个班次」：勾第一行
        click = ("const box=[...document.querySelectorAll('.result .list-row-checkbox')].find(e=>e.offsetParent);"
                 "if(!box) return {error:'no shift row'}; box.click(); await new Promise(r=>setTimeout(r,500));"
                 "const b=[...document.querySelectorAll('.page-head button, .page-actions button')].find(e=>e.offsetParent&&e.innerText.includes(LABEL));"
                 "if(!b) return {error:'no export selected button'}; b.click();").replace("LABEL", json.dumps(label))
    else:
        click = f"const via=await action({json.dumps(label)}); if(!via) return {{error:'no export action'}};"
    return (IDLE + ACTION + CAPTURE_EXPORTS + f"frappe.set_route({json.dumps(route)}); await new Promise(r=>setTimeout(r,500)); await idle();"
            "for(let k=0;k<80&&!document.querySelector('.page-head .page-title');k++) await new Promise(r=>setTimeout(r,250)); await idle();"
            + click + AWAIT_EXPORT + "return got||{error:'no export response'};")


def steps(params, camera_code=None):
    return [{"name": f"导出 {key}", "js": _click(key, params)} for key in params["pages"]]


def judge(params, results, camera_code=None):
    by_step = {r.get("step"): r.get("value") or {} for r in results}
    checks = []
    for key in params["pages"]:
        seen = by_step.get(f"导出 {key}") or {}
        api = PAGES[key][2]
        ok, detail = False, {k: seen.get(k) for k in ("url", "method", "status", "type", "size", "error")}
        if seen.get("b64") and api in str(seen.get("url")):
            try:
                sheets, text = workbook_text(seen["b64"])
                missing = [marker for marker in params.get(key, []) if marker and str(marker) not in text]
                detail.update(sheets=sheets, missing=missing)
                ok = seen.get("method") == "POST" and seen.get("status") == 200 and not missing
            except Exception as error:  # noqa: BLE001 - 打不开就是不通过
                detail["unreadable"] = str(error)[:200]
        checks.append({"case": f"export {key}", "seen": detail, "ok": ok})
    return checks


def verify(lab, manifest, params, camera_code=None):
    jobs = lab.python(manifest["site"], 'print(json.dumps({"jobs": frappe.db.count("Pearnly Export")}))')["jobs"]
    # 实验室的数据都很小：当场给文件，不建导出任务
    return [{"case": "small exports are served at once", "saved": jobs, "ok": jobs == params["jobs"]}]
