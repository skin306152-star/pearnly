"""流程用例：ERP 版 CC 对话里的收款引导卡（引导式 P0 接线 + P1 收款）——点输入框上方的「收款」，卡上一步步选填，复核后「写入」。

真页面、假智能体（只用来开一段对话：说一句话，假后端回一句）；收款数据同一套造法（`collection_lib.seed`：本次专用客户、两张未收清发票、
账套收款类型）。不连 Express、不发 LINE、不调模型。电脑与手机宽度各跑一遍，各截一张复核卡、一张结果。

页面上：业务菜单「收款」→ 对话里出引导卡（不是模型出的）：公司 / 日期 / 账套能定的进「已定」，按题点：客户（搜索框打客户码、点中）→
发票（勾第一张、本次收改成 600）→ 收款方式（现金 600）→ 提交 → 复核卡：客户、发票、合计 600.00 →「写入」→ 结果一行。
库里：一笔来源 cc、待写入、现金 600、同意记成 ``guide:<运行>:<版本>``（页面上这一下），值就是卡上点选 / 填的。
"""
from __future__ import annotations

import json
import uuid

from cc_approval_lib import JS, set_language
from collection_lib import restore, seed

TITLE = "ERP 版 CC 收款引导卡：点「收款」→ 卡上选客户、发票（收 600）、现金 → 复核 →「写入」→ 结果；值来自卡片，同意是页面上这一下"
USER = "admin@erp.regression"
CARD = "qa('[data-slot=\"cc-guide-run\"]').pop()"


def prepare(lab, manifest):
    site = manifest["site"]
    set_language(lab, site, USER, "zh")
    tag = "gc" + uuid.uuid4().hex[:6]
    seeded = seed(lab, manifest, tag)
    title = lab.python(site, f"print(json.dumps({{'t': frappe.db.get_value('ERP Company', {seeded['company']!r}, 'company_name')}}))")["t"]
    return {**seeded, "tag": tag, "title": title, "cases": []}


# 页面小工具：给受控输入框填值（同人打字：原生 setter + input 事件）、读此刻的卡
HELP = (
    "const setVal=(el,v)=>{const d=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el),'value'); d.set.call(el,v); "
    "el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true}));}; "
    "const card=()=>" + CARD + "; "
    "const keys=()=>[...(card()?.querySelectorAll('[data-slot=\"cc-guide-question\"]')||[])].map(x=>x.dataset.key); "
)


def steps(params, camera_code=None):
    # 每步自己的代码包进一个块：与公用片段 JS 里同名的 card / q / row… 各在各的作用域（不包时「点收款」起就报重复声明，整条跑不通）
    return [{**step, "js": JS + "{" + step["js"][len(JS):] + "}"} if step["js"].startswith(JS) else step for step in _steps(params)]


def _steps(params):
    code = json.dumps(params["customer"])
    first = json.dumps(params["invoices"][0])
    # 一轮：照此刻卡上的题逐个答（客户按码搜、发票勾第一张收 600、方式现金 600、其余点第一个），点「提交」，等卡换版本
    answer = (HELP + "const q=(k)=>card()?.querySelector(`[data-slot=\"cc-guide-question\"][data-key=\"${k}\"]`); "
              "const radio=async(k,want)=>{const box=q(k); const items=[...box.querySelectorAll('[data-slot=\"cc-guide-option\"]')]; "
              "const hit=want?items.find(l=>l.innerText.includes(want)):items[0]; (hit?.querySelector('input')||hit)?.click(); await sleep(200); return !!hit;}; "
              "const done=[]; for(const k of keys()){ const box=q(k); "
              f"if(k==='customer'){{ setVal(box.querySelector('input'), {code}); "
              f"  await until(()=>[...q(k).querySelectorAll('[data-slot=\"cc-guide-option\"]')].some(l=>l.innerText.includes({code})), 40); done.push([k, await radio(k,{code})]); }}"
              f" else if(k==='invoices'){{ await until(()=>q(k).querySelectorAll('[role=\"checkbox\"]').length, 40); "
              f"  const rows=[...q(k).querySelectorAll('[data-slot=\"item\"]')]; const row=rows.find(r=>r.innerText.includes({first}))||rows[0]; "
              "  row?.querySelector('[role=\"checkbox\"]')?.click(); await sleep(300); const inp=row?.querySelector('input.cc-guide-amount'); if(inp) setVal(inp,'600'); "
              "  await sleep(200); done.push([k, !!row]); }"
              " else if(k==='methods'){ await until(()=>q(k).querySelectorAll('input.cc-guide-amount').length, 40); "
              "  const inp=q(k).querySelector('input.cc-guide-amount'); setVal(inp,'600'); await sleep(200); done.push([k, true]); }"
              " else if(k==='day'){ const inp=q(k).querySelector('input'); if(!inp.value) setVal(inp, new Date().toISOString().slice(0,10)); done.push([k,true]); }"
              " else { done.push([k, await radio(k)]); } } "
              "const before=card()?.innerText||''; const submit=[...card().querySelectorAll('button')].find(b=>b.innerText.split('\\n')[0].trim()==='提交'); "
              "const enabled=!!submit&&!submit.disabled; submit?.click(); "
              "await until(()=>card()&&card().innerText!==before, 60); await sleep(800); "
              "return {done, enabled, status: card()?.dataset.status||'', next: keys()};")
    return [
        # 同真实用户：新对话先在输入框下面选公司（月份、账套随之定好），再说话；没选公司的对话引导卡会先问公司
        {"name": "选公司", "js": JS + f"const want={json.dumps(params['title'])}; "
         "q('[data-slot=\"cc-chin-company\"]').click(); await until(()=>q('[data-slot=\"cc-company-picker\"] .cc-list-row'),80); await sleep(800); "
         "const row=qa('[data-slot=\"cc-company-picker\"] .cc-list-row').find(r=>r.innerText.includes(want)); row?.click(); "
         "const chosen=await until(()=>!q('[data-slot=\"cc-company-picker\"]')&&(q('[data-slot=\"cc-chin-company\"]')?.innerText||'').includes(want),80); "
         "return {row: !!row, chosen};"},
        {"name": "开对话", "js": JS + f"const sent=await say({json.dumps(params['tag'] + ' 你好')}); await sleep(1500); return {{sent}};"},
        {"name": "点收款", "js": JS + HELP + "const row=()=>q('[data-slot=\"cc-business-menu\"]'); await until(()=>row(),120); "
         "const labels=[...(row()?.querySelectorAll('button')||[])].map(b=>b.innerText.trim()); "
         "[...row().querySelectorAll('button')].find(b=>b.innerText.trim()==='收款')?.click(); "
         "await until(()=>card()&&keys().length, 120); "
         "return {labels, status: card()?.dataset.status||'', business: card()?.dataset.business||'', keys: keys(), "
         "decided: [...(card()?.querySelectorAll('[data-slot=\"cc-guide-decided\"]')||[])].map(x=>x.innerText.trim())};"},
        {"name": "答第一轮", "js": JS + answer},
        {"name": "答第二轮", "js": JS + answer},
        {"name": "答第三轮", "js": JS + "if(" + CARD + "?.dataset.status!=='asking') return {skipped: true}; " + answer},
        {"name": "复核", "shot": True, "file": "1-review", "js": JS + HELP +
         "await until(()=>card()?.dataset.status==='reviewing', 60); const r=card()?.querySelector('[data-slot=\"cc-guide-review\"]'); "
         "return {status: card()?.dataset.status||'', text: r?.innerText||'', total: r?.querySelector('[data-slot=\"cc-guide-total\"]')?.innerText||''};"},
        {"name": "写入", "shot": True, "file": "2-result", "js": JS + HELP +
         "const b=[...(card()?.querySelectorAll('button')||[])].find(x=>x.innerText.split('\\n')[0].trim()==='写入'); b?.click(); "
         "await until(()=>card()?.querySelector('[data-slot=\"cc-guide-result\"]'), 60); "
         "const res=card()?.querySelector('[data-slot=\"cc-guide-result\"]'); return {clicked: !!b, status: card()?.dataset.status||'', "
         "result: res?.dataset.result||'', text: res?.innerText||''};"},
        {"name": "删除会话", "js": JS + f"const mine=(await listed('chat')).filter(s=>(s.title||'').includes({json.dumps(params['tag'])})); "
         "for(const s of mine) await call('pearnly_core.assistant.api.delete',{session_id:s.id},true); return {ids: mine.map(s=>s.id)};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    menu, review, wrote = (by.get(k) or {} for k in ("点收款", "复核", "写入"))
    asked = [k for name in ("答第一轮", "答第二轮", "答第三轮") for k, _ok in ((by.get(name) or {}).get("done") or [])]
    return [
        {"case": "业务菜单有「收款」，点了对话里出引导卡（不是模型出的），公司 / 日期 / 账套进「已定」", "seen": menu,
         "ok": "收款" in (menu.get("labels") or []) and menu.get("status") == "asking" and menu.get("business") == "collection"
         and "customer" in (menu.get("keys") or []) and len(menu.get("decided") or []) >= 1},
        {"case": "卡上依次问了客户、发票、收款方式", "seen": asked,
         "ok": all(k in asked for k in ("customer", "invoices", "methods"))},
        {"case": "复核卡：客户码、第一张发票、合计 600.00", "seen": review,
         "ok": review.get("status") == "reviewing" and params["customer"] in review.get("text", "")
         and params["invoices"][0] in review.get("text", "") and "600.00" in review.get("total", "")},
        {"case": "点「写入」：结果一行（排队，等 CC 桌面版）", "seen": wrote,
         "ok": bool(wrote.get("clicked")) and wrote.get("result") in ("queued", "entered")},
        {"case": "删掉这段会话", "seen": by.get("删除会话"), "ok": bool((by.get("删除会话") or {}).get("ids"))},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], f"""
rows = frappe.get_all("ERP Collection", filters={{"customer": {params['customer']!r}, "source": "cc"}},
                      fields=["name", "status", "cash", "consent_ref"], order_by="creation desc", limit_page_length=3)
print(json.dumps({{"rows": rows}}, default=str))
""")
    rows = out.get("rows") or []
    top = rows[0] if rows else {}
    return [{"case": "库里：一笔来源 cc、待写入、现金 600、同意是页面上这一下（guide:<运行>:<版本>）", "seen": out,
             "ok": top.get("status") in ("queued", "writing", "written") and str(top.get("cash")).startswith("600")
             and str(top.get("consent_ref") or "").startswith("guide:")}]


def cleanup(lab, manifest, params):
    restore(lab, manifest, params)
