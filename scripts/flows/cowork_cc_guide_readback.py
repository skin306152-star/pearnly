"""流程用例：Cowork 版 CC「回读」引导真页面从菜单到结果（表单业务 readback，不写账、没有复核步骤）。

造数据同 `cc_conversation_ledger`（一条 Express 连接、同一税号两本同期账套 T.8 / T.9、从目录建的公司、录入人一台登记过的电脑），
直写对话建在这家、本月、账套 T.8（Express）。页面上（不调接口、不经模型）：

1. 打开对话，点输入框上方业务菜单「回读」→ 引导卡：账套 = 本对话 T.8 进「已定」，只问月份；
2. 月份填本月、点「提交」→ 题都答完、回读没有复核步骤 → 确定卡（已定的账套一行、「确定」）；
3. 点「确定」→ 结果卡：请电脑读（实验栈没有真 CC 桌面版：在读 / 电脑没来领都算对，不说读完）。
库里：引导运行 done、同意是页面上这一下；回读请求（若建了）只在 T.8、这个月；没有写账。
"""
from __future__ import annotations

import json
import uuid

import cc_conversation_ledger as ledger
from cc_lib import JS

TITLE = "Cowork 版 CC 回读引导：菜单「回读」→ 账套 = 本对话 T.8 已定、只问月份 → 提交 → 确定卡「确定」→ 结果卡（在读 / 电脑没来领）"
USER = ledger.USER
CARD = "qa('[data-slot=\"cc-guide-run\"]').pop()"
MONTH = ledger.CHAT_MONTH


def _seed() -> str:
    return ledger._mine(ledger.CLEANUP) + ledger.EXTRA_CLEANUP + ledger.SEED + ledger.LEDGER_IDS


def prepare(lab, manifest):
    site = manifest["site"]
    lab.python(site, f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    tag = "rb" + uuid.uuid4().hex[:6]
    out = lab.python(site, _seed() + f"""
from pearnly_core.assistant import sessions
made = sessions.create(project=company["id"], period={MONTH!r}, ledger=ids.get({ledger.T8!r}, ""))
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": made["id"]}}, "title", {tag!r})
frappe.db.commit()
print(json.dumps({{"company": company["id"], "connection": conn["name"], "session": made["id"], "t8": ids.get({ledger.T8!r}, "")}}))
""")
    return {**out, "tag": tag, "cases": []}


HELP = ("const card=()=>" + CARD + "; "
        "const setVal=(el,v)=>{const d=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el),'value'); d.set.call(el,v); "
        "el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true}));}; ")


def steps(params, camera_code=None):
    return [
        {"name": "打开对话", "wait_ms": 6000, "js": JS +
         f"setTimeout(()=>location.assign('/assistant?c='+encodeURIComponent({json.dumps(params['session'])})),0); return true;"},
        {"name": "点回读", "js": JS + HELP +
         "const row=()=>q('[data-slot=\"cc-business-menu\"]'); await until(()=>row(),160); "
         # 直接摆出来的就点；手机宽度收进「更多」的，先按开「更多」（Radix 菜单认 pointerdown），再点菜单里那一项
         "let b=row().querySelector(':scope > button[data-business=\"readback\"]'); const direct=!!b; "
         "if(!b){const more=row().querySelector(':scope > button[data-slot=\"cc-business-more\"]'); "
         " more?.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0,pointerType:'mouse'})); "
         " await until(()=>q('[data-slot=\"cc-business-more-menu\"] [data-business=\"readback\"]'),40); "
         " b=q('[data-slot=\"cc-business-more-menu\"] [data-business=\"readback\"]');} "
         "const label=b?b.innerText.trim():''; b?.click(); "
         "await until(()=>card()?.querySelector('[data-slot=\"cc-guide-question\"]'),160); await sleep(500); "
         "return {label, direct, clicked: !!b, business: card()?.dataset.business||'', status: card()?.dataset.status||'', "
         "keys: [...(card()?.querySelectorAll('[data-slot=\"cc-guide-question\"]')||[])].map(x=>x.dataset.key), "
         "decided: card()?.querySelector('[data-slot=\"cc-guide-decided\"]')?.innerText.trim()||''};"},
        {"name": "填月份提交", "js": JS + HELP +
         "const inp=card()?.querySelector('[data-slot=\"cc-guide-question\"][data-key=\"period\"] [data-slot=\"cc-guide-value\"]'); "
         f"const pre=inp?.value||''; if(inp) setVal(inp,{json.dumps(MONTH)}); await sleep(300); "
         "const next=card()?.querySelector('[data-slot=\"cc-askq-next\"]'); const enabled=!!next&&!next.disabled; next?.click(); "
         "await until(()=>card()?.dataset.stage==='ready',160); await sleep(400); const b=card()?.querySelector('[data-slot=\"cc-guide-confirm\"]'); "
         "return {pre, enabled, stage: card()?.dataset.stage||'', lines: [...(card()?.querySelectorAll('[data-slot=\"cc-guide-back\"]')||[])].map(x=>x.dataset.step), "
         "label: b?b.innerText.trim():'', confirm_enabled: !!b&&!b.disabled};"},
        {"name": "确定", "shot": True, "file": "1-result", "js": JS + HELP +
         "const run=card()?.dataset.run||''; card()?.querySelector('[data-slot=\"cc-guide-confirm\"]')?.click(); "
         "await until(()=>{const r=q('[data-slot=\"cc-guide-result\"]'); return r&&r.dataset.result;},200); await sleep(800); "
         "const res=q('[data-slot=\"cc-guide-result\"]'); return {run, status: card()?.dataset.status||'', result: res?.dataset.result||'', text: res?.innerText||''};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, month, done = (by.get(k) or {} for k in ("点回读", "填月份提交", "确定"))
    return [
        {"case": "业务菜单有「回读」，点了出引导卡：只问月份，账套 = 本对话 T.8 进「已定」", "seen": opened,
         "ok": bool(opened.get("clicked")) and opened.get("label") == "回读" and opened.get("business") == "readback"
         and opened.get("keys") == ["period"] and "已定 1" in (opened.get("decided") or "")},
        {"case": "月份提交后出确定卡（回读没有复核步骤）：账套一行、主按钮「确定」能点", "seen": month,
         "ok": bool(month.get("enabled")) and month.get("stage") == "ready" and "book" in (month.get("lines") or [])
         and month.get("label") == "确定" and bool(month.get("confirm_enabled"))},
        {"case": "点「确定」：结果卡是在读或电脑没来领（实验栈没有真电脑），不说读完", "seen": done,
         "ok": done.get("status") == "done" and done.get("result") in ("queued", "failed")},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], f"""
runs = frappe.get_all("Pearnly Assistant Guide Run", filters={{"session_id": {params['session']!r}, "business": "readback"}},
                      fields=["status", "consent"], limit_page_length=3)
rows = frappe.get_all("Cowork Express Readback", filters={{"connection": {params['connection']!r}}},
                      fields=["account_set_key", "period"], limit_page_length=5)
print(json.dumps({{"runs": runs, "rows": rows}}, default=str))
""")
    runs, rows = out.get("runs") or [], out.get("rows") or []
    done = runs[0] if runs else {}
    return [
        {"case": "引导运行办完（done），同意是页面上这一下（guide:<运行>:<版本>）", "seen": runs,
         "ok": len(runs) == 1 and done.get("status") == "done" and str(done.get("consent") or "").startswith("guide:")},
        {"case": "回读请求（若建了）只在 T.8、这个月", "seen": rows,
         "ok": all(str(r.get("account_set_key") or "").lower() == ledger.T8 and r.get("period") == MONTH for r in rows)},
    ]


def cleanup(lab, manifest, params):
    company = params.get("company") or "none"
    lab.python(manifest["site"], ledger._mine(ledger.CLEANUP) + ledger.EXTRA_CLEANUP + f"""
from pearnly_core.assistant import sessions
frappe.set_user({USER!r})
for sid in frappe.get_all("Pearnly Assistant Session", filters={{"user": {USER!r}, "project": {company!r}}},
                          pluck="session_id", limit_page_length=20):
    sessions.delete(sid)
gone = cleanup()
frappe.db.commit()
print(json.dumps({{"gone": gone, "left": leftovers()}}))
""")
