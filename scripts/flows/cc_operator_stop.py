"""流程用例：代录对话里点停止，执行的电脑真的收到停下（11-19 查出的现网缺陷：点停止写下的停止时刻与任务上的 stop 没提交，
随后作废允许卡一开头的回滚把它们整笔冲掉，写入计划读不到这次停止、执行代录的电脑一直录下去）。

真页面、假智能体：prepare 按正式函数给这家建一段代录对话（`assistant.sessions.create` 带 ``mode=operator``，同一事务建代录任务），
再把任务标成交出去在录（``running``，记一台实验电脑；真实是 CC 桌面版取走开录，网页上没有那座桥）。
开录之前先以本人身份和 CC 说过一句（``sessions.send``，真实顺序：先聊、电脑再开录），这一句开出远端会话（``remote_ids``）；
只有这样点停止才走「打断引擎 → 作废允许卡」那条路（没开过远端的代录对话停完就返回，碰不到那次回滚）。
页面上走用户真点的路：打开这段对话 → 电脑在录，输入框出停止键 → 点停止键 → 照执行的那台电脑的 CC 页面取控制（``express_operator.api.task_controls``，取走即清空，同 bridge.ts）拿到 ``stop``。
库里：这段对话记下了停止时刻（``last_stop_at``，写入计划据此不再放行）。对话与任务在收尾里删（先把任务标成已停，在录的删不掉）。
"""
from __future__ import annotations

import json
import uuid

from cc_lib import JS

TITLE = "代录对话点停止：执行的电脑取到 stop、对话记下停止时刻"
USER = "admin@erp.regression"
MACHINE = "LAB-PC-STOP"


def prepare(lab, manifest):
    site = manifest["site"]
    company = manifest.get("company") or manifest["companies"][0]
    tag = "ostop" + uuid.uuid4().hex[:6]
    out = lab.python(site, f"""
from pearnly_core.assistant import sessions
from pearnly_core.express_operator import tasks
frappe.db.set_value("User", {USER!r}, "language", "zh")
frappe.set_user({USER!r})
made = sessions.create(project={company!r}, period=frappe.utils.today()[:7], mode="operator")
frappe.db.commit()
# 开录之前先和 CC 说过一句（真实顺序）：这一句开出远端会话（remote_ids），之后点停止才走「打断引擎 → 作废允许卡」那条路
sessions.send(made["id"], {tag!r} + " [lab-slow]1[/lab-slow]")
frappe.db.commit()
task = frappe.db.get_value("Pearnly Assistant Session", {{"session_id": made["id"]}}, "operator_task")
remote = frappe.db.get_value("Pearnly Assistant Session", {{"session_id": made["id"]}}, "remote_ids")
frappe.db.set_value(tasks.DOCTYPE, task, {{"status": "running", "computer": {MACHINE!r}}}, update_modified=False)
frappe.db.commit()
print(json.dumps({{"sid": made["id"], "task": task, "remote": remote}}))
""")
    return {"tag": tag, "sid": out["sid"], "task": out["task"], "remote": out.get("remote") or "", "company": company}


def steps(params, camera_code=None):
    sid, task = params["sid"], params["task"]
    return [
        {"name": "打开代录对话", "js": f"location.href='/assistant?c={sid}'; return 1;", "wait_ms": 6000},
        {"name": "在录", "js": JS + "const running=await until(()=>q('button.aui-composer-cancel'), 120); "
         "return {running, c: new URL(location.href).searchParams.get('c')||''};"},
        {"name": "点停止键", "shot": True, "file": "1-stopped", "js": JS +
         "const stop=q('button.aui-composer-cancel'); if(stop) stop.click(); "
         "const ended=await until(()=>!q('button.aui-composer-cancel'), 80); await sleep(1500); "
         f"const got=await call('pearnly_core.express_operator.api.task_controls',{{name:{json.dumps(task)}}},true); "
         "return {clicked: !!stop, ended, controls: (got&&got.controls)||null};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    busy, stopped = by.get("在录") or {}, by.get("点停止键") or {}
    actions = [c.get("action") for c in stopped.get("controls") or [] if isinstance(c, dict)]
    return [
        {"case": "打开的是这段代录对话（开录前和 CC 说过一句、远端会话已开），电脑在录，输入框出停止键",
         "seen": {**busy, "remote": params.get("remote")},
         "ok": busy.get("c") == params["sid"] and bool(busy.get("running")) and params.get("remote", "[]") not in ("", "[]")},
        {"case": "点停止键：执行的电脑取控制拿到 stop", "seen": stopped,
         "ok": bool(stopped.get("clicked")) and "stop" in actions},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], f"""
row = frappe.db.get_value("Pearnly Assistant Session", {{"session_id": {params['sid']!r}}}, ["last_stop_at", "remote_ids"], as_dict=True) or {{}}
print(json.dumps({{"last_stop_at": row.get("last_stop_at"), "remote_ids": row.get("remote_ids")}}, default=str))
""")
    return [{"case": "库里：这段对话记下了停止时刻（写入计划据此不再放行）", "seen": out, "ok": bool(out.get("last_stop_at"))}]


def cleanup(lab, manifest, params):
    """成功、失败、超时都跑：任务先标成已停（在录的删不掉），再删这段对话（任务随之删）。"""
    if not params.get("sid"):
        return
    lab.python(manifest["site"], f"""
from pearnly_core.assistant import sessions
from pearnly_core.express_operator import tasks
if frappe.db.exists(tasks.DOCTYPE, {params['task']!r}):
    frappe.db.set_value(tasks.DOCTYPE, {params['task']!r}, "status", "stopped", update_modified=False)
frappe.db.commit()
frappe.set_user({USER!r})
if frappe.db.exists("Pearnly Assistant Session", {{"session_id": {params['sid']!r}}}):
    sessions.delete({params['sid']!r})
frappe.db.commit()
print("{{}}")
""")
