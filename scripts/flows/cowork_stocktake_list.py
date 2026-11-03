"""流程用例：Cowork 盘点列表 —— 打开即照库里列出这个站点的盘点（列表 + 总数两个读接口），读接口不建、不改任何东西
（起因：10-29 GET 接口里顺手建站点归属被回滚，`api_list_stock_counts` / `api_stock_count_total` 改成只读）。

每套数据：第一页的行数与表头计数对得上库里此刻的盘点数（新开户没有盘点时出空态和「新建」）；本套固定数据自己的盘点
（老站点上传导入的两份，清单 `dataset_counts`）逐份按标题在列表里找到、是那一份。库里站点归属与盘点都不多不少。
实验栈的数据卷是保留的，别的流程（盘点录入、网址码）每轮都会多建盘点：总数开跑时现读，标题按行逐份找、不够就点列表的
「加载更多」翻下一页（列表没有搜索框），不截页面文字的前 N 个字（11-03：累积的盘点把导入的两份挤出了截取范围）。
"""
from __future__ import annotations

import json

from desk_list import FIND_ROWS, IDLE, READ_LIST, list_matches, requests

TITLE = "Cowork 盘点：列表照库里列出、读接口不写库"
# 每套固定数据自己有几份盘点（`datasets.COWORK_COUNTS`：老站点上传导入两份，新开户没有）：清单里少了就不算过，不让标题那条空过
OWN_COUNTS = {"new": 0, "old": 2}
LIST, TOTAL = "pearnly_cowork.stocktake.desk_api.api_list_stock_counts", "pearnly_cowork.stocktake.desk_api.api_stock_count_total"


def prepare(lab, manifest):
    # 总数与站点归属数现读（清单是这一轮开跑时取的，同站点排在前面的流程会再建盘点）
    now = lab.python(manifest["site"], f"""
print(json.dumps({{"workspaces": frappe.db.count("Cowork Workspace"),
                   "counts": frappe.db.count("Cowork Stocktake Task", {{"workspace": {manifest['workspace']!r}}})}}))
""")
    return {"total": now["counts"], "workspaces": now["workspaces"], "workspace": manifest["workspace"], "dataset": manifest["dataset"],
            "own": {row["title"]: row["name"] for row in manifest["dataset_counts"]}}


def steps(params, camera_code=None):
    titles = json.dumps(list(params["own"]), ensure_ascii=False)
    return [{"name": "盘点列表", "js": IDLE + "await idle();" + READ_LIST + FIND_ROWS + requests(LIST)
             + "const listed=calls; " + requests(TOTAL).replace("const calls", "const totals")
             + f"const own=await find_rows({titles});"
             "return {...list, listed, totals, own};"}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") for r in results if r.get("step") == "盘点列表"), None) or {}
    own = seen.pop("own", None) or {}
    found = own.get("found") or {}
    return [{"case": "list matches the stored counts", "seen": seen, "expect": params["total"],
             "ok": list_matches(seen, params["total"])},
            {"case": "every count title is listed", "seen": {"missing": [t for t, name in params["own"].items() if found.get(t) != name],
                                                            "found": found, "pages": own.get("pages")},
             "ok": len(params["own"]) == OWN_COUNTS[params["dataset"]]
             and all(found.get(title) == name for title, name in params["own"].items())},
            {"case": "the page asked the list and the total", "seen": [seen.get("listed"), seen.get("totals")],
             "ok": bool(seen.get("listed")) and bool(seen.get("totals"))}]


def verify(lab, manifest, params, camera_code=None):
    now = lab.python(manifest["site"], f"""
print(json.dumps({{"workspaces": frappe.db.count("Cowork Workspace"),
                   "counts": frappe.db.count("Cowork Stocktake Task", {{"workspace": {params['workspace']!r}}})}}))
""")
    return [{"case": "no workspace created", "saved": now["workspaces"], "ok": now["workspaces"] == params["workspaces"]},
            {"case": "counts unchanged", "saved": now["counts"], "ok": now["counts"] == params["total"]}]
