"""流程用例：代录会话回放一致（统一对话框架，方案 cc-unified-plan 14.2：代录事件不另存，每次读都由任务记录投影，编号固定）——
同一段代录会话第一次打开、刷新后、换一台设备（同一个人另开一个页面实例）打开，事件逐条相同（编号、先后都一样），画出来的
也一样（回复、步骤摘要、卡片、报告文件卡片）；并且都与服务端以会话主人身份读到的那一串（`sessions.events`）逐条相同。

准备（站点里用产品自己的函数）：`sessions.create(mode=operator)` 建一段 ERP 代录会话（同一事务建任务），把任务记成交过单、
一张录成（带 Express 单号）、一张没录成要人选公司（需要你处理）——代录事件源投影出资料、开始前（已交）、两张单的步骤与结果、
要人处理的卡片、收尾消息与报告。单据用这家已提交的采购单（只读，卡片点开要按它预检）；不够两张时流程自己记两张服务费采购单
（不动库存、不推账本，11-09），不靠排在前面的流程留下的单。远端会话没开过：读事件不碰引擎，不花钱。
「换设备」在同一个无头浏览器里另开一个页面实例（iframe，独立的页面状态与会话控制器，同一个登录）。verify 删掉会话连任务。
"""
from __future__ import annotations

import json
import uuid

from cc_lib import JS

TITLE = "CC 代录会话回放一致：第一次打开、刷新、换设备，事件逐条相同、画出来相同，且与服务端读到的一致"
READY = "document.readyState === 'complete' && !!document.querySelector('[data-mode=\"operator\"]')"
USER = "admin@erp.regression"

# 一个页面（或 iframe 里的页面）此刻画出来的这段会话：回复、步骤摘要、代录卡片与步骤（data-slot 与文字）、报告文件卡片的地址
SIG = ("const sig=d=>{const t=e=>(e?.innerText||'').trim().replace(/\\s+/g,' ');"
       "return {users:[...d.querySelectorAll('[data-role=\"user\"]')].map(t), assistants:[...d.querySelectorAll('[data-role=\"assistant\"]')].map(t),"
       "summaries:[...d.querySelectorAll('.cc-steps-summary')].map(t),"
       "cards:[...d.querySelectorAll('[data-slot^=\"cc-op-\"]')].map(e=>e.dataset.slot+':'+t(e)).slice(0,60),"
       "report: d.querySelector('[data-slot=\"cc-op-export\"]')?.getAttribute('href')||''};};"
       "const ready=d=>!!(d&&d.querySelector('[data-slot=\"cc-op-choose\"]')&&d.querySelector('[data-slot=\"cc-op-export\"]'));")


def prepare(lab, manifest):
    lab.python(manifest["site"], f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    company = manifest.get("company") or manifest["companies"][0]
    tag = "flow" + uuid.uuid4().hex[:6]
    out = lab.python(manifest["site"], f"""
from datetime import timedelta
from pearnly_core.assistant import sessions
from pearnly_core.express_operator import tasks
from pearnly_erp_portal.express import push
frappe.set_user({USER!r})
period = frappe.utils.add_months(frappe.utils.today(), -1)[:7]
def submitted():
    return frappe.get_all(push.PURCHASE, filters={{"company": {company!r}, "status": "submitted"}}, fields=["name", "system_no"],
                          order_by="name asc", limit_page_length=2)
docs = submitted()
if len(docs) < 2:
    # 这条流程自己备两张已提交的采购单（以前靠前面别的流程留下的单，新实验栈一起来就挂，11-09 第二个窗口撞上）：
    # 只记服务费、不动库存；临时关掉「接 Express」再记（同 erp.purchase.create 的做法），记完还原，不往账本推；
    # 请求编号固定，重跑认回同两张
    from pearnly_erp_portal.masters import company as companies
    from pearnly_erp_portal.purchasing import entry as purchase, suppliers
    if not frappe.db.exists("ERP Company", {company!r}):
        raise RuntimeError("数据集缺前置：没有公司 " + {company!r} + "（scripts/regression/datasets.py）")
    companies.set_active_company({company!r})
    supplier = frappe.db.get_value("ERP Supplier", {{"supplier_name": "flow replay supplier"}})
    if not supplier:
        supplier = suppliers.create_supplier(supplier_name="flow replay supplier")["name"]
    express = frappe.db.get_value("ERP Company", {company!r}, "express_enabled")
    frappe.db.set_value("ERP Company", {company!r}, "express_enabled", 0, update_modified=False)
    try:
        for i in (1, 2):
            purchase.post({{"company": {company!r}, "supplier": supplier, "doc_date": frappe.utils.today(), "currency": "THB", "fx_rate": 1,
                           "has_vat": 1, "price_mode": "exclusive", "doc_kind": "purchase_invoice", "update_stock": 0,
                           "doc_no": "FLOW-REPLAY-" + str(i),
                           "lines": [{{"item_type": "service", "description": "flow replay service " + str(i), "qty": 1, "unit": "งาน",
                                      "unit_price": 100, "vat_rate": 7}}]}},
                          request_id="flow-replay-po-" + {company!r} + "-" + str(i))
    finally:
        frappe.db.set_value("ERP Company", {company!r}, "express_enabled", express, update_modified=False)
    frappe.db.commit()
    docs = submitted()
if len(docs) < 2:
    raise RuntimeError("数据集缺前置：公司 " + {company!r} + " 记不出两张已提交的采购单")
made = sessions.create(project={company!r}, period=period, mode="operator")
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": made["id"]}}, "title", {tag!r} + " 回放")
doc = frappe.get_doc(tasks.DOCTYPE, made["task"])
now = frappe.utils.now_datetime().replace(microsecond=0)
doc.title = {tag!r} + " 回放"
doc.status = "attention"
doc.started_at = now - timedelta(minutes=10)
doc.finished_at = now - timedelta(minutes=2)
doc.append("documents", {{"doc_id": docs[0].name, "ref": docs[0].system_no or docs[0].name, "party": "Flow party", "doc_group": "purchase",
                          "state": "saved", "docnum": "EXP-" + {tag!r}, "queued_at": now - timedelta(minutes=9), "done_at": now - timedelta(minutes=6)}})
doc.append("documents", {{"doc_id": docs[1].name, "ref": docs[1].system_no or docs[1].name, "party": "Flow party", "doc_group": "purchase",
                          "state": "not_done", "reason": "[OPERATOR_COMPANY_PICK] flow", "queued_at": now - timedelta(minutes=9),
                          "done_at": now - timedelta(minutes=3)}})
doc.flags.ignore_permissions = True
doc.save()
# 任务早于交单建好（同真的：先建、准备、再交单），资料与开始前排在单据步骤前面
frappe.db.set_value(tasks.DOCTYPE, doc.name, "creation", now - timedelta(minutes=15), update_modified=False)
frappe.db.commit()
# 服务端以会话主人身份读到的整段（「另一个读者」）：页面三次读到、画出的都要与它一致
read = sessions.events(made["id"])
print(json.dumps({{"session": made["id"], "task": made["task"], "company": {company!r}, "period": period,
                  "expected": [event.get("id") for event in read["events"]],
                  "types": [event.get("type") for event in read["events"]],
                  "refs": [d.system_no or d.name for d in docs]}}))
""")
    return {**out, "tag": tag, "cases": []}


def steps(params, camera_code=None):
    sid = json.dumps(params["session"])
    collect = ("const body=await call('pearnly_core.assistant.api.events',{session_id:" + sid + "}); "
               "const ids=((body||{}).events||[]).map(e=>e.id); ")
    return [
        {"name": "第一次打开", "js": JS + SIG + "const ok=await until(()=>ready(document),160); await sleep(1500); " + collect +
         "return {ok, mode: mode(), ids, sig: sig(document)};"},
        {"name": "刷新", "js": "location.reload(); return 1;", "wait_ms": 4000},
        # 刷新后地址已是干净的 /assistant（深链接处理完就换掉）：同人一样切到代录、在侧栏点这一段
        {"name": "刷新后", "js": JS + SIG + "await until(()=>q('[data-slot=\"cc-mode-switch\"]'),80); "
         "const phone=innerWidth<768; if(phone&&q('.cc-mshell')?.dataset.open!=='true'){q('[data-slot=\"cc-nav-toggle\"]')?.click(); "
         "await until(()=>q('.cc-mshell')?.dataset.open==='true',40); await sleep(600);} "
         "const box=phone?q('.cc-mdrawer'):document; (box.querySelector('[data-mode=\"operator\"]')||q('[data-mode=\"operator\"]'))?.click(); "
         "await until(()=>mode()==='operator',40); "
         f"const row=()=>qa('[data-slot=\"aui_thread-list-item\"]').filter(seen).find(e=>e.innerText.includes({json.dumps(params['tag'])})); "
         "if(!await until(row,120)) return {error:'session row missing after refresh', rows: rows()}; "
         "(row().querySelector('[data-slot=\"aui_thread-list-item-trigger\"]')||row()).click(); "
         "const ok=await until(()=>ready(document),160); await sleep(1500); " + collect +
         "return {ok, mode: mode(), ids, sig: sig(document)};"},
        # 换设备：同一个人另开一个页面实例（独立的页面状态、会话控制器与实时连接），打开同一段会话
        {"name": "换设备", "js": JS + SIG + "const f=document.createElement('iframe'); f.style.cssText='position:fixed;left:0;top:0;width:'+innerWidth+'px;height:'+innerHeight+'px;border:0;z-index:2147483646;background:#fff'; "
         f"f.src='/assistant?c='+encodeURIComponent({sid}); document.body.append(f); "
         "const ok=await until(()=>ready(f.contentDocument),200); await sleep(1500); "
         "const d=f.contentDocument; const w=f.contentWindow; "
         f"const r=await w.fetch('/api/method/pearnly_core.assistant.api.events?'+new URLSearchParams({{session_id:{sid}}})); "
         "const ids=(((await r.json()).message||{}).events||[]).map(e=>e.id); const m=d.querySelector('[data-slot=\"cc-mode-switch\"] [aria-checked=\"true\"]')?.dataset.mode; "
         "const out={ok, mode: m, ids, sig: sig(d)}; f.remove(); return out;"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    first, again, other = (by.get(k) or {} for k in ("第一次打开", "刷新后", "换设备"))
    expected = params["expected"]
    sig = lambda step: step.get("sig") or {}
    cards = sig(first).get("cards") or []
    return [
        {"case": "服务端投影出这段会话应有的事件：资料、开始前（已交）、两张单的结果、要人处理的卡片、收尾消息、会话停着等人",
         "seen": {"types": params["types"], "n": len(expected)},
         "ok": len(expected) == len(set(expected)) and all(str(i).startswith(f"pop_{params['task']}_") for i in expected
                                                          if not str(i).startswith("ptoolrun_"))
         and {"agent.tool_use", "agent.custom_tool_use", "agent.tool_result", "user.custom_tool_result", "agent.message",
              "session.status_idle"} <= set(params["types"])},
        {"case": "第一次打开、刷新后、换设备：页面读到的事件逐条相同（编号、先后），且与服务端以会话主人身份读到的逐条相同",
         "seen": {"first": first.get("ids"), "again": again.get("ids"), "other": other.get("ids"), "expected": expected},
         "ok": bool(expected) and first.get("ids") == again.get("ids") == other.get("ids") == expected},
        {"case": "三次画出来的一样：回复、步骤摘要、卡片与步骤、报告文件卡片", "seen": {"first": sig(first), "again": sig(again), "other": sig(other)},
         "ok": bool(first.get("ok")) and bool(again.get("ok")) and bool(other.get("ok")) and sig(first) == sig(again) == sig(other)},
        {"case": "画出来的是这段会话的业务内容：要人处理的那张（单号）、报告文件卡片指向这个任务的报告", "seen": {"cards": cards[:12], "report": sig(first).get("report")},
         "ok": any(params["refs"][1] in c for c in cards if c.startswith("cc-op-choose:"))
         and sig(first).get("report") == f"/api/method/pearnly_core.express_operator.feed.report?name={params['task']}"},
        {"case": "三次都在代录模式打开", "seen": [first.get("mode"), again.get("mode"), other.get("mode")],
         "ok": first.get("mode") == again.get("mode") == other.get("mode") == "operator"},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], f"""
from pearnly_core.assistant import sessions
from pearnly_core.express_operator import tasks
frappe.set_user({USER!r})
again = [event.get("id") for event in sessions.events({params["session"]!r})["events"]]
sessions.delete({params["session"]!r})
frappe.db.commit()
print(json.dumps({{"again": again, "task": frappe.db.exists(tasks.DOCTYPE, {params["task"]!r}) or "",
                  "session": frappe.db.count("Pearnly Assistant Session", {{"session_id": {params["session"]!r}}})}}))
""")
    return [
        {"case": "页面看过三次后，服务端再读一次仍逐条相同（读事件不改记录）", "saved": {"again": out["again"]}, "ok": out["again"] == params["expected"]},
        {"case": "删掉会话连任务", "saved": {"task": out["task"], "session": out["session"]}, "ok": not out["task"] and out["session"] == 0},
    ]
