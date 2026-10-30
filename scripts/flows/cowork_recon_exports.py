"""流程用例：Cowork 银行 / 收入对账的结果页导出 —— 打开那份对账的结果页点「导出」，拿到的复核表要对
（起因：10-29 导出一律 POST；`bank_recon_api.export` / `income_recon_api.export` 原来只收 GET）。

老站点有银行、收入对账各一份（`datasets.py old cowork` 照对账页解析好的两边内容走正式的建任务函数）：结果页点导出，
请求是 POST、回 200、是能打开的 xlsx，复核表里有这份对账的凭证号；小导出当场给文件、不建导出任务。
新开户没有对账任务：两类的任务数都是 0，不点导出。
"""
from __future__ import annotations

import json

from desk_list import ACTION, AWAIT_EXPORT, CAPTURE_EXPORTS, IDLE, workbook_text

TITLE = "Cowork 对账导出：银行 / 收入结果页点导出、走 POST、复核表对"
KINDS = {"bank": ("pearnly-recon-bank", "pearnly_cowork.reconciliation.bank_recon_api.export", ["GL-1"]),
         "income": ("pearnly-recon-income", "pearnly_cowork.reconciliation.income_recon_api.export", ["GL-1", "GL-2"])}
LABEL = "导出"  # 实验室账号界面是中文（`cowork.recon.export`）


def prepare(lab, manifest):
    names = manifest.get("recon_task_names") or {}
    tasks = {kind: (names.get(kind) or [""])[0] for kind in KINDS}
    jobs = lab.python(manifest["site"], 'print(json.dumps({"jobs": frappe.db.count("Pearnly Export")}))')["jobs"]
    return {"tasks": tasks, "pages": [kind for kind in KINDS if tasks[kind]], "jobs": jobs,
            "counts": {kind: manifest["recon_tasks"][kind] for kind in KINDS}}


def steps(params, camera_code=None):
    out = []
    for kind in params["pages"]:
        root, _api, _markers = KINDS[kind]
        route = json.dumps(f"{root}/{params['tasks'][kind]}")
        out.append({"name": f"导出 {kind}", "js": IDLE + ACTION + CAPTURE_EXPORTS
                    + f"frappe.set_route({route}); await new Promise(r=>setTimeout(r,800)); await idle();"
                    "for(let k=0;k<80&&!document.querySelector('.page-head .primary-action:not(.hide)');k++) await new Promise(r=>setTimeout(r,250));"
                    "await idle();"
                    f"const via=await action({json.dumps(LABEL)}); if(!via) return {{error:'no export action'}};"
                    + AWAIT_EXPORT + "return got||{error:'no export response'};"})
    return out


def judge(params, results, camera_code=None):
    if not params["pages"]:
        return [{"case": "no reconciliation to export", "seen": params["counts"], "ok": not any(params["counts"].values())}]
    by_step = {r.get("step"): r.get("value") or {} for r in results}
    checks = []
    for kind in params["pages"]:
        seen = by_step.get(f"导出 {kind}") or {}
        _root, api, markers = KINDS[kind]
        ok, detail = False, {k: seen.get(k) for k in ("url", "method", "status", "type", "size", "error")}
        if seen.get("b64") and api in str(seen.get("url")):
            try:
                sheets, text = workbook_text(seen["b64"])
                missing = [marker for marker in markers if marker not in text]
                detail.update(sheets=sheets, missing=missing)
                ok = seen.get("method") == "POST" and seen.get("status") == 200 and not missing
            except Exception as error:  # noqa: BLE001 - 打不开就是不通过
                detail["unreadable"] = str(error)[:200]
        checks.append({"case": f"export {kind}", "seen": detail, "ok": ok})
    return checks


def verify(lab, manifest, params, camera_code=None):
    jobs = lab.python(manifest["site"], 'print(json.dumps({"jobs": frappe.db.count("Pearnly Export")}))')["jobs"]
    return [{"case": "small exports are served at once", "saved": jobs, "ok": jobs == params["jobs"]}]
