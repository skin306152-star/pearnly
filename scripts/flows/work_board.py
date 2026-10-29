"""流程用例：任务管理看板（ERP 与 Cowork 同一份页面写法）—— 打开即显示默认看板和它的列表；整块看板走 POST，读接口不建、不改
（起因：10-29 `work.api.board` 等 12 个 GET 接口经 `boards.open_board` 兜底建默认看板 / 列表再手动 commit；改成只有 POST 的
`board` 补建（`boards.ready_board`），其余读接口只读）。

每套数据：打开任务管理页，页面上的列与库里默认看板的未归档列表一一对得上；页面请求整块看板用 POST（参数不在网址上）；
看板、列表数都不多不少（迁移时已建好，打开不再建）。
"""
from __future__ import annotations

import json

from desk_list import IDLE, requests

TITLE = "任务管理：打开即显示默认看板的列表、整块看板走 POST、只看不改"
TABLES = {"erp": ("ERP Work Board", "ERP Work Column", "pearnly_erp_portal.work.api.board"),
          "cowork": ("Cowork Work Board", "Cowork Work Column", "pearnly_cowork.work.api.board")}


def _product(manifest):
    return manifest.get("product") or "erp"


def prepare(lab, manifest):
    board, column, api = TABLES[_product(manifest)]
    state = lab.python(manifest["site"], f"""
default = frappe.get_all({board!r}, filters={{"is_default": 1}}, pluck="name", order_by="creation asc", limit_page_length=1)
lists = frappe.get_all({column!r}, filters={{"board": default[0], "archived": 0}}, pluck="name", limit_page_length=0) if default else []
print(json.dumps({{"board": default[0] if default else "", "lists": lists, "boards": frappe.db.count({board!r}),
                   "columns": frappe.db.count({column!r})}}))
""")
    return {**state, "api": api, "tables": [board, column]}


def steps(params, camera_code=None):
    return [{"name": "看板", "js": IDLE + "await idle();"
             "for(let k=0;k<80&&!document.querySelector('.pearnly-work-column');k++) await new Promise(r=>setTimeout(r,250)); await idle();"
             "const shown=[...document.querySelectorAll('.pearnly-work-column[data-list]')].map(e=>e.dataset.list);"
             + requests(params["api"]) + "return {shown, calls};"}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") for r in results if r.get("step") == "看板"), None) or {}
    calls = seen.get("calls") or []
    return [{"case": "the default board's lists are shown", "seen": seen.get("shown"), "expect": params["lists"],
             "ok": bool(params["lists"]) and sorted(seen.get("shown") or []) == sorted(params["lists"])},
            # POST 的参数在请求体里：网址上没有 `?`（GET 会把 board / fallback 拼在网址上）
            {"case": "the whole board is asked by POST", "seen": calls, "ok": bool(calls) and all("?" not in url for url in calls)}]


def verify(lab, manifest, params, camera_code=None):
    board, column = params["tables"]
    now = lab.python(manifest["site"], f"""
print(json.dumps({{"boards": frappe.db.count({board!r}), "columns": frappe.db.count({column!r})}}))
""")
    return [{"case": "opening creates no board or list", "saved": now,
             "ok": now == {"boards": params["boards"], "columns": params["columns"]}}]
