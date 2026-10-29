"""流程用例：Cowork 对账中心任务列表（银行对账）—— 打开即列出这个站点的对账任务，读接口不建、不改任何东西
（起因：10-29 GET 接口里顺手建站点归属被回滚，`recon_center.api.tasks` 改成只读）。

每套数据：打开银行对账页，列表要与库里一致（新开户没有任务出空态和「新建」，有任务时行数与计数对得上），
页面请求过 `recon_center.api.tasks`；库里站点归属与对账任务都不多不少。
"""
from __future__ import annotations

from desk_list import IDLE, READ_LIST, list_matches, requests

TITLE = "Cowork 对账中心：任务列表照库里列出、读接口不写库"
API = "pearnly_cowork.recon_center.api.tasks"


def prepare(lab, manifest):
    return {"kind": "bank", "total": manifest["recon_tasks"]["bank"], "workspaces": manifest["workspaces"],
            "workspace": manifest["workspace"]}


def steps(params, camera_code=None):
    return [{"name": "任务列表", "js": IDLE + "await idle();" + READ_LIST + requests(API) + "return {...list, calls};"}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") for r in results if r.get("step") == "任务列表"), None) or {}
    return [{"case": "list matches the stored tasks", "seen": seen, "expect": params["total"],
             "ok": list_matches(seen, params["total"])},
            {"case": "the page asked the task list", "seen": seen.get("calls"), "ok": bool(seen.get("calls"))}]


def verify(lab, manifest, params, camera_code=None):
    now = lab.python(manifest["site"], f"""
print(json.dumps({{"workspaces": frappe.db.count("Cowork Workspace"),
                   "tasks": frappe.db.count("Cowork Bank Reconciliation", {{"workspace": {params['workspace']!r}}})}}))
""")
    return [{"case": "no workspace created", "saved": now["workspaces"], "ok": now["workspaces"] == params["workspaces"]},
            {"case": "tasks unchanged", "saved": now["tasks"], "ok": now["tasks"] == params["total"]}]
