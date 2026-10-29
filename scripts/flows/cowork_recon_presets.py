"""流程用例：Cowork 对账预设 —— 在银行对账页点「对账预设」，弹窗照库里列出这一类的预设，读接口不建、不改任何东西
（起因：10-29 GET 接口里顺手建站点归属被回滚，`recon_center.presets.listing` 改成只读）。

每套数据：新开户没有预设，弹窗出「还没有预设」；老站点有一条银行预设，弹窗里列出它的范围（`KBANK ···4821 · 1112-01`）。
库里预设与站点归属都不多不少。
"""
from __future__ import annotations

import json

from desk_list import ACTION, IDLE, requests

TITLE = "Cowork 对账预设：弹窗照库里列出、读接口不写库"
API = "pearnly_cowork.recon_center.presets.listing"
# 老站点那条银行预设给人看的范围（`presets.scope_label`）
SCOPE_LABEL = "KBANK ···4821 · 1112-01"
BUTTON, EMPTY = "对账预设", "暂无预设"


def prepare(lab, manifest):
    return {"total": manifest["presets"]["bank"], "workspaces": manifest["workspaces"], "workspace": manifest["workspace"],
            "all": sum(manifest["presets"].values())}


def steps(params, camera_code=None):
    # 实验室岗位账号的界面语言是中文（`lab_users.py`）：按钮「对账预设」、空态「暂无预设」（Cowork 术语表）
    return [{"name": "打开预设", "js": IDLE + ACTION + "await idle();"
             f"const via=await action({json.dumps(BUTTON)}); if(!via) return {{error:'no presets button'}};"
             "for(let k=0;k<80&&!(window.cur_dialog&&cur_dialog.$wrapper.is(':visible'));k++) await new Promise(r=>setTimeout(r,250)); await idle();"
             "const body=cur_dialog?(cur_dialog.$body.text()||'').replace(/\\s+/g,' ').trim():'';"
             + requests(API) + "return {body: body.slice(0, 600), calls, via};"}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") for r in results if r.get("step") == "打开预设"), None) or {}
    body = seen.get("body") or ""
    ok = SCOPE_LABEL in body and EMPTY not in body if params["total"] else EMPTY in body and SCOPE_LABEL not in body
    return [{"case": "dialog lists the stored presets", "seen": body[:200], "expect": params["total"], "ok": ok},
            {"case": "the dialog asked the presets", "seen": seen.get("calls"), "ok": bool(seen.get("calls"))}]


def verify(lab, manifest, params, camera_code=None):
    now = lab.python(manifest["site"], f"""
print(json.dumps({{"workspaces": frappe.db.count("Cowork Workspace"),
                   "presets": frappe.db.count("Cowork Recon Preset", {{"workspace": {params['workspace']!r}}})}}))
""")
    return [{"case": "no workspace created", "saved": now["workspaces"], "ok": now["workspaces"] == params["workspaces"]},
            {"case": "presets unchanged", "saved": now["presets"], "ok": now["presets"] == params["all"]}]
