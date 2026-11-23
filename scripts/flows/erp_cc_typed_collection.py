"""流程用例：ERP 版 CC 对话里打字说要收款 → 只打开收款卡（用户 11-16「按建议做」）。

真页面、假智能体（`lab_agents.py` 的 ``[lab-tool]``：照真实引擎发一次 ``erp_collection_guide`` 调用，入参是用户说到的客户码），
不调付费模型；收款数据同一套造法（`collection_lib.seed`）。真模型那一半（三种说法都只开卡、不复述数值，查询照旧直接答）另批跑。

断言的是应有状态：
- 工具回 opened 与本次引导编号，说明里叫模型别问、别复述数值；
- 对话里出收款引导卡（不是模型出的），第一道题是客户，搜索框初值（服务端 ``query``）是说到的客户码，选项只剩这位客户；
- 卡上什么都没答（客户、发票、金额都等用户点）；
- 库里没有新收款（打字不建草稿）。
电脑与手机宽度各跑一遍，各截一张开卡后的样子。
"""
from __future__ import annotations

import json
import uuid

from cc_approval_lib import JS, set_language
from collection_lib import restore, seed

TITLE = "ERP 版 CC 打字说要收款：只打开收款卡，客户搜索框预填说到的客户码，卡上什么都不替用户答，库里不建草稿"
USER = "admin@erp.regression"
CARD = "qa('[data-slot=\"cc-guide-run\"]').pop()"


def prepare(lab, manifest):
    site = manifest["site"]
    set_language(lab, site, USER, "zh")
    tag = "tc" + uuid.uuid4().hex[:6]
    seeded = seed(lab, manifest, tag)
    # 同收款引导卡流程：新对话先在输入框下面选公司（没选公司的对话引导卡会先问公司）
    title = lab.python(site, f"print(json.dumps({{'t': frappe.db.get_value('ERP Company', {seeded['company']!r}, 'company_name')}}))")["t"]
    return {**seeded, "tag": tag, "title": title}


def steps(params, camera_code=None):
    tag, code = params["tag"], params["customer"]
    line = tag + " 收款 [lab-tool]" + json.dumps({"name": "erp_collection_guide", "input": {"hint": code}},
                                                 ensure_ascii=False) + "[/lab-tool]"
    return [
        {"name": "选公司", "js": JS + "{" + f"const want={json.dumps(params['title'])}; "
         "q('[data-slot=\"cc-chin-company\"]').click(); await until(()=>q('[data-slot=\"cc-company-picker\"] .cc-list-row'),80); await sleep(800); "
         "const row=qa('[data-slot=\"cc-company-picker\"] .cc-list-row').find(r=>r.innerText.includes(want)); row?.click(); "
         "const chosen=await until(()=>!q('[data-slot=\"cc-company-picker\"]')&&(q('[data-slot=\"cc-chin-company\"]')?.innerText||'').includes(want),80); "
         "return {row: !!row, chosen};}"},
        {"name": "开对话", "js": JS + f"{{const sent=await say({json.dumps(tag + ' 你好')}); await sleep(1500); return {{sent}};}}"},
        {"name": "打字说要收款", "shot": True, "file": "1-opened", "js": JS + "{" +
         f"const sent=await say({json.dumps(line, ensure_ascii=False)}); "
         "await until(()=>!!toolResult('erp_collection_guide'),240); const r=toolResult('erp_collection_guide')||{}; "
         f"await until(()=>{CARD}&&{CARD}.querySelector('[data-slot=\"cc-guide-question\"]'),120); await sleep(800); "
         f"const c={CARD}; const keys=[...(c?.querySelectorAll('[data-slot=\"cc-guide-question\"]')||[])].map(x=>x.dataset.key); "
         "return {sent, status: r.status||'', run: r.run||'', note: String(r.note||'').slice(0,300), "
         "card: c?.dataset.status||'', business: c?.dataset.business||'', keys};}"},
        {"name": "卡的内容", "js": JS + "{" +
         "const r=toolResult('erp_collection_guide')||{}; if(!r.run) return {error:'no run'}; "
         "const v=await call('pearnly_core.assistant.api.guide_view',{run_name:r.run}); const card=(v||{}).card||{}; "
         "const first=(card.questions||[])[0]||{}; "
         "return {status: v?.status||'', first: first.key||'', query: first.query||'', "
         "options: (first.options||[]).map(o=>o.code), answers: card.answers||{}};}"},
        {"name": "删除会话", "js": JS + "{" + f"const mine=(await ownChats({json.dumps(tag)},'chat')); "
         "for(const s of mine) await call('pearnly_core.assistant.api.delete',{session_id:s.id},true); return {ids: mine.map(s=>s.id)};}"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, card = by.get("打字说要收款") or {}, by.get("卡的内容") or {}
    code = params["customer"]
    return [
        {"case": "在输入框下面选了这家公司", "seen": by.get("选公司"), "ok": bool((by.get("选公司") or {}).get("chosen"))},
        {"case": "工具回 opened 与引导编号，说明叫模型别问、别复述数值", "seen": opened,
         "ok": opened.get("status") == "opened" and bool(opened.get("run")) and "names no customer, invoice or amount and asks for none" in opened.get("note", "")},
        {"case": "对话里出收款引导卡，第一道题是客户", "seen": opened,
         "ok": opened.get("business") == "collection" and opened.get("card") == "asking" and (opened.get("keys") or [""])[0] == "customer"},
        {"case": "客户搜索框初值是说到的客户码，选项只剩这位客户", "seen": card,
         "ok": card.get("first") == "customer" and card.get("query") == code and card.get("options") == [code]},
        {"case": "卡上什么都没替用户答", "seen": card.get("answers"), "ok": card.get("answers") == {}},
        {"case": "删掉这段会话", "seen": by.get("删除会话"), "ok": bool((by.get("删除会话") or {}).get("ids"))},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], f"""
rows = frappe.get_all("ERP Collection", filters={{"customer": {params['customer']!r}}}, pluck="name", limit_page_length=5)
print(json.dumps({{"rows": rows}}, default=str))
""")
    return [{"case": "库里没有这位客户的收款（打字只开卡，不建草稿）", "seen": out, "ok": out.get("rows") == []}]


def cleanup(lab, manifest, params):
    restore(lab, manifest, params)
