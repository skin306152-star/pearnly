"""流程用例：Cowork 对账中心任务列表（银行对账）—— 打开即列出这个站点的对账任务，读接口不建、不改任何东西
（起因：10-29 GET 接口里顺手建站点归属被回滚，`recon_center.api.tasks` 改成只读）。

每套数据：打开银行对账页，列表要与库里一致（新开户没有任务出空态和「新建」，有任务时行数与计数对得上），
页面请求过 `recon_center.api.tasks`；库里站点归属与对账任务都不多不少。
「进行中」入口与本人进行中的对账一致：没有就看不到（电脑页头、手机「…」菜单里都没有），有就只有一个、带件数
（11-08：手机宽度框架为页头按钮另加的菜单项原来一直挂着「进行中」）。
"""
from __future__ import annotations

from cowork_recon_run import RUNNING_ENTRIES
from desk_list import IDLE, READ_LIST, list_matches, requests

TITLE = "Cowork 对账中心：任务列表照库里列出、读接口不写库"
API = "pearnly_cowork.recon_center.api.tasks"


def prepare(lab, manifest):
    return {"kind": "bank", "total": manifest["recon_tasks"]["bank"], "workspaces": manifest["workspaces"],
            "workspace": manifest["workspace"]}


def steps(params, camera_code=None):
    return [{"name": "任务列表", "js": IDLE + RUNNING_ENTRIES + "await idle();" + READ_LIST + requests(API)
             + "await new Promise(r=>setTimeout(r,1500)); await idle();"
             "const t=await frappe.pearnly.texts('cowork.recon.');"
             "const active=((await frappe.call({method:'pearnly_cowork.recon_center.api.active', args:{kind:'bank'}})).message||[]).length;"
             "const entries=await running_entries(t('cowork.recon.running'));"
             "return {...list, calls, active, entries, label: active?t('cowork.recon.runningCount', active):''};"}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") for r in results if r.get("step") == "任务列表"), None) or {}
    return [{"case": "list matches the stored tasks", "seen": seen, "expect": params["total"],
             "ok": list_matches(seen, params["total"])},
            {"case": "the page asked the task list", "seen": seen.get("calls"), "ok": bool(seen.get("calls"))},
            {"case": "the running entry matches the reconciliations still running", "seen": {k: seen.get(k) for k in ("active", "entries", "label")},
             "ok": seen.get("entries") == ([seen["label"]] if seen.get("active") else [])}]


def verify(lab, manifest, params, camera_code=None):
    now = lab.python(manifest["site"], f"""
print(json.dumps({{"workspaces": frappe.db.count("Cowork Workspace"),
                   "tasks": frappe.db.count("Cowork Bank Reconciliation", {{"workspace": {params['workspace']!r}}})}}))
""")
    return [{"case": "no workspace created", "saved": now["workspaces"], "ok": now["workspaces"] == params["workspaces"]},
            {"case": "tasks unchanged", "saved": now["tasks"], "ok": now["tasks"] == params["total"]}]
