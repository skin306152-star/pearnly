"""流程用例：ERP 导出 —— 销售、采购、库存、已过账盘点、班次、首页账本、收款（11-11），各点一次「导出」，拿到的文件要对
（起因：10-29 导出可能在 GET 里建导出任务再手动 commit；改成共用的 `p.export_file` 一律 POST、13 个导出接口只收 POST）。

每套数据、每个有数据可导的页面：在页面上点它自己的导出（页头按钮或「…」菜单、班次是勾选后的「导出选中」），
页面发出的导出请求是 POST、回 200、是能打开的 xlsx，文件里有库里这家公司的那些单号 / 商品码（老数据）；
导出不建导出任务（小的当场给文件）。公司取数据清单里的那家；各类单据先由 `erp_lib.ensure_documents` 保证各有一张
（新开户本来没有过账的盘点、班次），六页每次都点，不靠别的流程先建单、也不跟着别的流程切走的当前公司。
"""
from __future__ import annotations

import json

from desk_list import ACTION, AWAIT_EXPORT, CAPTURE_EXPORTS, IDLE, workbook_text
from erp_lib import company as dataset_company, ensure_documents

TITLE = "ERP 导出：各页点导出、走 POST、文件里有该有的单据"
# 页面（路由）、按钮文字（实验室账号界面是中文）、导出接口
PAGES = {
    "sales": ("pearnly-sales", "导出", "pearnly_erp_portal.sales.api.api_sales_export"),
    "purchase": ("pearnly-purchase", "导出", "pearnly_erp_portal.purchasing.api.api_purchase_export"),
    "inventory": ("pearnly-inventory", "导出", "pearnly_erp_portal.inventory.api.api_stock_export"),
    "stock_count": ("pearnly-stock-count/{count}", "导出", "pearnly_erp_portal.inventory.api.api_stock_count_export"),
    "shifts": ("pearnly-pos-shifts", "导出选中", "pearnly_erp_portal.cashier.api.api_shifts_export"),
    "ledger": ("pearnly-home", "导出明细", "pearnly_core.billing_www.export_records"),
    # 收款（11-11）：只读页「…」菜单里的导出
    "collections": ("pearnly-collections", "导出", "pearnly_erp_portal.collection.api.api_collections_export"),
}


def prepare(lab, manifest):
    ensure_documents(lab, manifest)  # 也把管理员的当前公司切到这家
    found = lab.python(manifest["site"], f"""
company = {dataset_company(manifest)!r}
warehouses = frappe.get_all("ERP Warehouse", filters={{"company": company}}, pluck="name")
sales = frappe.get_all("ERP Sales Document", filters={{"company": company}}, pluck="system_no", limit_page_length=5)
purchases = frappe.get_all("Purchase Document", filters={{"company": company, "status": "submitted"}}, pluck="doc_no",
                           limit_page_length=5)
stocked = frappe.get_all("ERP Stock Balance", filters={{"warehouse": ["in", warehouses or [""]], "qty_on_hand": ["!=", 0]}},
                         pluck="product", limit_page_length=5)
codes = [frappe.db.get_value("ERP Product", name, "code") for name in stocked]
posted = frappe.get_all("ERP Stock Count", filters={{"warehouse": ["in", warehouses or [""]], "status": "Posted"}}, pluck="name",
                        order_by="creation asc", limit_page_length=1)
counted = frappe.get_all("ERP Stock Count Entry", filters={{"count": posted[0], "voided": 0}}, pluck="product",
                         limit_page_length=5) if posted else []
from pearnly_erp_portal.cashier import contracts as cashier
shifts = frappe.get_all("ERP Shift", filters={{"status": cashier.CLOSED}}, pluck="name", limit_page_length=1)
# 收款（11-11）：这家公司一笔已写入的收款（收款只在 LINE / CC 录、电脑写回 RE 号；这里照记录的形状直接备一笔，不经写账）
marker = "REFLOW" + frappe.generate_hash(length=6).upper()
collection = frappe.get_all("ERP Collection", filters={{"company": company, "docnum": ["like", "REFLOW%"]}}, pluck="docnum",
                            limit_page_length=1)
if not collection:
    frappe.get_doc({{"doctype": "ERP Collection", "name": "COLFLOW-" + marker, "company": company, "doc_date": frappe.utils.nowdate(),
                    "collector": "admin@erp.regression", "source": "line", "customer": "FLOW", "customer_name": "flow export customer",
                    "status": "written", "docnum": marker, "draft_version": 1, "cash": 100, "total": 100,
                    "invoice_summary": "IVFLOW 100.00", "invoices": [{{"docnum": "IVFLOW", "remaining": 100, "amount": 100}}]
                    }}).insert(ignore_permissions=True, set_name="COLFLOW-" + marker)
    frappe.db.commit()
    collection = [marker]
print(json.dumps({{"company": company, "sales": sales, "purchase": [n for n in purchases if n], "inventory": codes,
                  "count": posted[0] if posted else "", "stock_count": [frappe.db.get_value("ERP Product", n, "code") for n in counted],
                  "shifts": shifts, "collections": collection, "jobs": frappe.db.count("Pearnly Export")}}))
""")
    return {**found, "pages": ["sales", "purchase", "inventory", "ledger", "stock_count", "shifts", "collections"]}


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


# 采购 / 销售导出里账本那几列（评估 2026-11-02 §12 第 6 条：列名改成通用的 ledger_*，表头标签与改列前相同，账本标记不导出）
LEDGER_COLUMNS = [["ledger_state", "Express Status"], ["ledger_docnum", "Express No."], ["ledger_reason", "Express Note"],
                  ["ledger_sent_at", "Express Sent At"], ["ledger_job", "Express Job"], ["ledger_book", "Express Account Set"],
                  ["ledger_needed", "Express Choices Needed"]]


def verify(lab, manifest, params, camera_code=None):
    jobs = lab.python(manifest["site"], 'print(json.dumps({"jobs": frappe.db.count("Pearnly Export")}))')["jobs"]
    columns = lab.python(manifest["site"], """
from pearnly_erp_portal.purchasing import output as purchase_output, records as purchase_records
from pearnly_erp_portal.sales import output as sales_output, records as sales_records
frappe.set_user("admin@erp.regression")
out = {}
for key, output, records, doctype in (("purchase", purchase_output, purchase_records, "Purchase Document"),
                                      ("sales", sales_output, sales_records, "ERP Sales Document")):
    found = output._columns(doctype)
    rows = records.rows({}, limit=1) if key == "purchase" else records.rows({}, start=0, limit=1)
    # 原生筛选按新列（ledger_state 是标准筛选）：与库里有账本状态的单是同一批
    filtered = sorted(frappe.get_all(doctype, filters={"ledger_state": ["is", "set"]}, pluck="name", limit_page_length=0))
    stored = sorted(r[0] for r in frappe.db.sql(f"select name from `tab{doctype}` where ifnull(ledger_state, '') != ''"))
    out[key] = {"ledger": [[f, l] for f, l in found if f.startswith(("ledger_", "express_"))],
                "list": sorted(k for k in (rows[0] if rows else {}) if k.startswith(("ledger_", "express_"))),
                "filter": filtered == stored, "filtered": len(filtered)}
print(json.dumps(out))
""")
    # 实验室的数据都很小：当场给文件，不建导出任务
    return [{"case": "small exports are served at once", "saved": jobs, "ok": jobs == params["jobs"]},
            {"case": "purchase / sales exports keep the Express column labels on the general ledger columns", "saved": columns,
             "ok": all(columns[key]["ledger"] == LEDGER_COLUMNS for key in ("purchase", "sales"))},
            {"case": "filtering by the general ledger state finds the documents that have one", "saved": columns,
             "ok": all(columns[key]["filter"] for key in ("purchase", "sales"))},
            {"case": "purchase / sales lists carry the general ledger columns", "saved": columns,
             # 列表接口行里带通用账本的状态与单号（界面上仍是原来那一栏「Express」）；原因可以一起回（11-22：状态词按状态 + 原因算，
             # 每分钟自己再试的原因显示「录入中」），不按字段个数判
             "ok": all(not columns[key]["list"] or _ledger_keys_ok(columns[key]["list"]) for key in ("purchase", "sales"))
             and _ledger_keys_ok(columns["purchase"]["list"])}]


def _ledger_keys_ok(keys) -> bool:
    return {"ledger_docnum", "ledger_state"} <= set(keys) <= {"ledger_docnum", "ledger_reason", "ledger_state"}
