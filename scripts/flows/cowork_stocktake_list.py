"""流程用例：Cowork 盘点列表 —— 打开即照库里列出这个站点的盘点（列表 + 总数两个读接口），读接口不建、不改任何东西
（起因：10-29 GET 接口里顺手建站点归属被回滚，`api_list_stock_counts` / `api_stock_count_total` 改成只读）。

每套数据：新开户没有盘点出空态和「新建」；老站点两份上传导入的盘点，行数与表头计数对得上、标题都在。
库里站点归属与盘点都不多不少。
"""
from __future__ import annotations

from desk_list import IDLE, READ_LIST, list_matches, requests

TITLE = "Cowork 盘点：列表照库里列出、读接口不写库"
LIST, TOTAL = "pearnly_cowork.stocktake.desk_api.api_list_stock_counts", "pearnly_cowork.stocktake.desk_api.api_stock_count_total"


def prepare(lab, manifest):
    return {"total": len(manifest["stock_counts"]), "titles": [row["title"] for row in manifest["stock_counts"]],
            "workspaces": manifest["workspaces"], "workspace": manifest["workspace"]}


def steps(params, camera_code=None):
    return [{"name": "盘点列表", "js": IDLE + "await idle();" + READ_LIST + requests(LIST)
             + "const listed=calls; " + requests(TOTAL).replace("const calls", "const totals")
             + "const text=(document.querySelector('.result')?.innerText||'').slice(0,2000);"
             "return {...list, listed, totals, text};"}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") for r in results if r.get("step") == "盘点列表"), None) or {}
    text = seen.pop("text", "") or ""
    return [{"case": "list matches the stored counts", "seen": seen, "expect": params["total"],
             "ok": list_matches(seen, params["total"])},
            {"case": "every count title is listed", "seen": [t for t in params["titles"] if t not in text],
             "ok": all(title in text for title in params["titles"])},
            {"case": "the page asked the list and the total", "seen": [seen.get("listed"), seen.get("totals")],
             "ok": bool(seen.get("listed")) and bool(seen.get("totals"))}]


def verify(lab, manifest, params, camera_code=None):
    now = lab.python(manifest["site"], f"""
print(json.dumps({{"workspaces": frappe.db.count("Cowork Workspace"),
                   "counts": frappe.db.count("Cowork Stocktake Task", {{"workspace": {params['workspace']!r}}})}}))
""")
    return [{"case": "no workspace created", "saved": now["workspaces"], "ok": now["workspaces"] == params["workspaces"]},
            {"case": "counts unchanged", "saved": now["counts"], "ok": now["counts"] == params["total"]}]
