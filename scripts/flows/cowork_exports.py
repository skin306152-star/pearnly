"""流程用例：Cowork 导出 —— 已结束的盘点、首页账本，各点一次「导出」，拿到的文件要对
（起因：10-29 导出可能在 GET 里建导出任务再手动 commit；改成共用的 `p.export_file` 一律 POST、导出接口只收 POST）。

每套数据：首页账本点「导出明细」；老站点再打开那份已结束的盘点点「导出」。页面发出的导出请求是 POST、回 200、
是能打开的 xlsx，盘点导出里有那份盘点的商品码；小导出当场给文件、不建导出任务。
"""
from __future__ import annotations

import json

from desk_list import ACTION, AWAIT_EXPORT, CAPTURE_EXPORTS, IDLE, workbook_text

TITLE = "Cowork 导出：盘点与首页账本点导出、走 POST、文件对"
PAGES = {
    "stock_count": ("pearnly-stocktake/{count}", "导出", "pearnly_cowork.stocktake.desk_api.api_stock_count_export"),
    "ledger": ("pearnly-home", "导出明细", "pearnly_core.billing_www.export_records"),
}


def prepare(lab, manifest):
    # 本套固定数据自己的那份已结束盘点（不取实验栈里别的流程累积的盘点）
    closed = next((row["name"] for row in manifest["dataset_counts"] if row.get("status") == "closed"), "")
    found = lab.python(manifest["site"], f"""
codes = frappe.get_all("Cowork Stocktake Item", filters={{"task": {closed!r}}}, pluck="product_code", limit_page_length=5) if {closed!r} else []
print(json.dumps({{"stock_count": codes, "jobs": frappe.db.count("Pearnly Export")}}))
""")
    pages = ["ledger"] + (["stock_count"] if closed else [])
    return {**found, "count": closed, "pages": pages}


def _click(key, params):
    route, label, _api = PAGES[key]
    return (IDLE + ACTION + CAPTURE_EXPORTS
            + f"frappe.set_route({json.dumps(route.format(count=params.get('count', '')))}); await new Promise(r=>setTimeout(r,500)); await idle();"
            "for(let k=0;k<80&&!document.querySelector('.page-head .page-title');k++) await new Promise(r=>setTimeout(r,250)); await idle();"
            f"const via=await action({json.dumps(label)}); if(!via) return {{error:'no export action'}};"
            + AWAIT_EXPORT + "return got||{error:'no export response'};")


def steps(params, camera_code=None):
    return [{"name": f"导出 {key}", "js": _click(key, params)} for key in params["pages"]]


def judge(params, results, camera_code=None):
    by_step = {r.get("step"): r.get("value") or {} for r in results}
    checks = []
    for key in params["pages"]:
        seen = by_step.get(f"导出 {key}") or {}
        ok, detail = False, {k: seen.get(k) for k in ("url", "method", "status", "type", "size", "error")}
        if seen.get("b64") and PAGES[key][2] in str(seen.get("url")):
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
    return [{"case": "small exports are served at once", "saved": jobs, "ok": jobs == params["jobs"]}]
