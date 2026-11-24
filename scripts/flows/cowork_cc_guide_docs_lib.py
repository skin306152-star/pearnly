"""Cowork 版 CC 采购 / 销售引导（documents 源，直写对话）真页面流程的共用片段：`cowork_cc_guide_purchase.py`、
`cowork_cc_guide_sales.py` 各调一次，只差业务。

造数据同 `cc_conversation_ledger`（一条 Express 连接、同一税号两本同期账套 T.8 / T.9、从目录建的公司、录入人一台登记过的电脑），
再造一张本业务识别完成的税票（采购：卖方是别家；销售：卖方是本公司；推送目标照真实上传写成这家公司现用的连接与账套 T.8）。采购的费用科目在 T.8
记住、付款方式不记（题卡问付款）；销售都不记（题卡问收入科目与付款）。T.8 记一份科目表（同电脑读到主档）。
实验栈没有识别进程（识别只排队），「交文件 → 识别」那一段不走：用 documents 源的公开入口 ``run.start(doc_ids=[单据], at="asking")``
（别的引导 / 代录开始前把已有单据交进来的同一个入口）在这家、本月、账套 T.8 的直写对话里开这次引导。

之后全在页面上点卡（不调接口、不经模型）：
1. 打开对话，卡是服务端出的，业务 = 本业务，题卡上逐题照推荐或第一项点、点「下一步 / 提交」；
2. 复核卡（documents）：这张票一行、票面单号与合计；
3. 点「立即录入」（``cc-guide-confirm``）→ 结果卡：这张已交出（排队等 CC 桌面版，实验栈没有真电脑来领）；
4. 会话（连引导运行）由 cleanup 删，verify 先读库。
库里：引导运行 done、同意是页面上这一下（``guide:<运行>:<版本>``）；这张票的推送目标是 T.8、有推送记录；没有写向 T.9 的。
"""
from __future__ import annotations

import json
import uuid

import cc_conversation_ledger as ledger
from cc_lib import JS

USER = ledger.USER
CARD = "qa('[data-slot=\"cc-guide-run\"]').pop()"
OTHER_TAX = "0105561000021"   # 编造的对方税号（13 位）
WORD = {"purchase": "采购", "sales": "销售"}
PREFER = {"purchase": ["5300"], "sales": ["S"]}   # 没有推荐的选项题点哪项（科目表里给本业务记的那个科目）

# 本业务的一张识别完成的税票（同 `cc_conversation_ledger.SEED` 的 recognized，方向按业务）；科目记在 T.8，付款方式不记
DOC = """
# 这份账套的科目表（同电脑读到主档后记下的那一处 `pool.put_facts`，同 `cc_ask`）：销售要选的收入 / 收款科目靠科目表，
# 合成账套本来没读到过主档、会等 CC 桌面版读（实验栈没有真电脑）
pool.put_facts(frappe.get_doc("Cowork ERP Connection", conn["name"]), "flowt8",
               {"reported_accounts": [{"code": code, "name": code} for code in ("A", "V", "P", "S", "O", "R", "5300")]},
               device="lab", routine=False, now=frappe.utils.now_datetime())
frappe.db.commit()


def guided_doc(business, number, day="2026-06-09"):
    mine = {"tax": %(my)r, "name": "Flow Ledger Co"}
    other = {"tax": %(other)r, "name": "Flow party " + number}
    seller, buyer = (other, mine) if business == "purchase" else (mine, other)
    fields = {"direction": business, "seller_name": seller["name"], "seller_tax": seller["tax"], "buyer_tax": buyer["tax"],
              "buyer_name": buyer["name"], "subtotal": "100", "vat": "7", "total_amount": "107", "date": day,
              "document_type": "tax_invoice", "invoice_number": number}
    pages = [{"page_index": 1, "fields": fields}]
    made = storage.create_recognized(ws, pages, uuid4().hex, source="manual", confidence="high", ai_raw=pages)
    # 推送目标同真实上传（`operator_source.intake`：按这家公司现用的连接与账套交识别）
    frappe.db.set_value("Cowork OCR History", made["name"], {"push_connection": conn["name"],
        "push_account_set": frappe.db.get_value("Cowork Company", company["id"], "seed_account_set")})
    # 采购的费用科目按卖方记住（题卡上只问付款方式）；销售什么都不记（同第一次录这家：收入科目、付款方式都在题卡上问——
    # 11-23 前出题不在对话里、按网页口径算定了这两项不出题，推送时在对话里又要，被跳过；`guide.documents.in_conversation` 修）
    if business == "purchase":
        application.save_choices(made["name"], conn["name"], "FLOWT8", json.dumps([
            {"kind": "account", "subject": "purchase|" + other["tax"], "value": "5300", "label": other["name"]}]))
    frappe.db.commit()
    return made["name"]
""" % {"my": ledger.MY_TAX, "other": OTHER_TAX}

# 销售票买方不是本公司：`EXTRA_CLEANUP` 按买方税号收不到，另按卖方税号收
SALES_CLEANUP = f"""
_by_buyer = cleanup
def cleanup():
    gone = _by_buyer()
    left = frappe.get_all("Cowork OCR History", filters={{"seller_tax": {ledger.MY_TAX!r}, "buyer_tax": {OTHER_TAX!r}}},
                          pluck="name", limit_page_length=0)
    if left:
        frappe.db.delete("Cowork OCR Correction", {{"source_history": ["in", left]}})
        frappe.db.delete("Cowork Push Record", {{"history": ["in", left]}})
        frappe.db.delete("Cowork OCR History", {{"name": ["in", left]}})
        frappe.db.commit()
    return {{**gone, "documents": gone["documents"] + len(left)}}
"""


def _seed_code() -> str:
    return ledger._mine(ledger.CLEANUP) + ledger.EXTRA_CLEANUP + SALES_CLEANUP + ledger.SEED + ledger.LEDGER_IDS + DOC


def prepare(lab, manifest, business):
    site = manifest["site"]
    lab.python(site, f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    tag = "gd" + uuid.uuid4().hex[:6]
    number = "FLOW-GD-" + uuid.uuid4().hex[:6].upper()
    out = lab.python(site, _seed_code() + f"""
from pearnly_core.assistant import sessions
from pearnly_core.assistant.guide import run
name = guided_doc({business!r}, {number!r})
made = sessions.create(project=company["id"], period={ledger.CHAT_MONTH!r}, ledger=ids.get({ledger.T8!r}, ""))
sid = made["id"]
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": sid}}, "title", {tag!r})
frappe.db.commit()
try:
    started = run.start(sid, {business!r}, doc_ids=[name], at="asking")
    frappe.db.commit()
    started = {{"run": started["run"], "status": started["status"], "stage": started.get("stage") or ""}}
except Exception as error:
    frappe.db.rollback()
    started = {{"run": "", "error": repr(error)[:300]}}
print(json.dumps({{"company": company["id"], "title": company["title"], "doc": name, "connection": conn["name"],
                  "t8": ids.get({ledger.T8!r}, ""), "t9": ids.get({ledger.T9!r}, ""), "session": sid, "started": started}}, default=str))
""")
    return {**out, "business": business, "number": number, "tag": tag, "run": (out.get("started") or {}).get("run") or "", "cases": []}


HELP = "const card=()=>" + CARD + "; "


def steps(params, camera_code=None):
    business = params["business"]
    return [
        {"name": "打开对话", "wait_ms": 6000, "js": JS +
         f"setTimeout(()=>location.assign('/assistant?c='+encodeURIComponent({json.dumps(params['session'])})),0); return true;"},
        {"name": "题卡", "js": JS + HELP + f"const PREFER={json.dumps(PREFER[business])}; " +
         "await until(()=>card()&&card().querySelector('[data-slot=\"cc-askq-option\"]'),160); await sleep(600); "
         "const head={business: card()?.dataset.business||'', status: card()?.dataset.status||'', stage: card()?.dataset.stage||''}; "
         # 题卡（第 1 期那张卡）一页一题：有推荐点推荐，没有点第一项，点主按钮（下一步 / 提交），直到这张卡不再是问题卡
         "const asked=[]; for(let k=0;k<12;k++){const c=card(); const opts=[...(c?.querySelectorAll('[data-slot=\"cc-askq-option\"]')||[])]; "
         " if(!opts.length) break; const title=c.querySelector('[data-slot=\"cc-askq-question\"]')?.innerText.trim()||''; "
         " const hit=opts.find(o=>o.querySelector('[data-slot=\"badge\"]'))||opts.find(o=>PREFER.includes(o.innerText.trim().split('\\n')[0]))||opts[0]; asked.push({title, picked: hit.innerText.trim().split('\\n')[0]}); "
         " if(!hit.hasAttribute('data-checked')) hit.click(); await sleep(250); "
         " const next=c.querySelector('[data-slot=\"cc-askq-next\"]'); if(!next||next.disabled) return {...head, asked, stuck: title}; "
         " const before=c.innerText; next.click(); await until(()=>!card()||card().innerText!==before,80); await sleep(500);} "
         "return {...head, asked};"},
        {"name": "复核", "shot": True, "file": "1-review", "js": JS + HELP +
         "await until(()=>card()?.querySelector('[data-slot=\"cc-guide-review\"][data-source=\"documents\"]'),160); await sleep(500); "
         "const r=card()?.querySelector('[data-slot=\"cc-guide-review\"]'); const b=card()?.querySelector('[data-slot=\"cc-guide-confirm\"]'); "
         "return {review: !!r, docs: [...(r?.querySelectorAll('[data-slot=\"cc-guide-doc\"]')||[])].map(d=>({doc: d.dataset.doc, blocking: !!d.dataset.blocking})), "
         "text: r?.innerText||'', confirm: b?b.innerText.trim():'', enabled: !!b&&!b.disabled};"},
        {"name": "确定", "shot": True, "file": "2-result", "js": JS + HELP +
         "const b=card()?.querySelector('[data-slot=\"cc-guide-confirm\"]'); b?.click(); "
         "await until(()=>card()?.querySelector('[data-slot=\"cc-guide-result\"]'),200); await sleep(800); "
         "const res=card()?.querySelector('[data-slot=\"cc-guide-result\"]'); "
         "return {clicked: !!b, status: card()?.dataset.status||'', result: res?.dataset.result||'', text: res?.innerText||'', "
         "rows: [...(res?.querySelectorAll('[data-doc]')||[])].map(x=>({doc: x.dataset.doc, status: x.dataset.status||''}))};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    asked, review, done = (by.get(k) or {} for k in ("题卡", "复核", "确定"))
    business, doc = params["business"], params["doc"]
    rows = done.get("rows") or []
    return [
        {"case": f"开出来的是{WORD[business]}引导卡（服务端出的，不经模型），题卡上问到付款方式，逐题点完没有卡住",
         "seen": {"started": params.get("started"), **asked},
         "ok": asked.get("business") == business and not asked.get("stuck")
         and any("付款" in (item.get("title") or "") for item in asked.get("asked") or [])},
        {"case": "复核卡：只有这一张票、不挡写入，票面带单号与合计 107.00，「立即录入」能点", "seen": review,
         "ok": bool(review.get("review")) and [d.get("doc") for d in review.get("docs") or []] == [doc]
         and not any(d.get("blocking") for d in review.get("docs") or []) and params["number"] in review.get("text", "")
         and "107" in review.get("text", "") and bool(review.get("enabled"))},
        {"case": "点「立即录入」：结果卡这一张已交出（排队等 CC 桌面版；实验栈没有真电脑来领，不说已录入）", "seen": done,
         "ok": bool(done.get("clicked")) and done.get("status") == "done" and [r.get("doc") for r in rows] == [doc]
         and all(r.get("status") in ("queued", "entered") for r in rows)},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], f"""
runs = frappe.get_all("Pearnly Assistant Guide Run", filters={{"name": {params['run']!r}}}, fields=["status", "consent", "business"],
                      limit_page_length=1)
doc = frappe.db.get_value("Cowork OCR History", {params['doc']!r}, ["push_connection", "push_account_set"], as_dict=True) or {{}}
records = frappe.get_all("Cowork Push Record", filters={{"history": {params['doc']!r}}}, fields=["account_key", "status"],
                         limit_page_length=10)
print(json.dumps({{"runs": runs, "doc": doc, "records": records}}, default=str))
""")
    run = (out.get("runs") or [{}])[0]
    doc, records = out.get("doc") or {}, out.get("records") or []
    t8 = ledger.T8
    return [
        {"case": "引导运行办完（done），同意是页面上这一下（guide:<运行>:<版本>）", "seen": run,
         "ok": run.get("status") == "done" and run.get("business") == params["business"]
         and str(run.get("consent") or "").startswith("guide:" + params["run"] + ":")},
        {"case": "这张票推向本对话账套 T.8：推送目标与推送记录都在 T.8，没有写向 T.9 的", "seen": out,
         "ok": str(doc.get("push_account_set") or "").lower() == t8 and bool(records)
         and all(str(r.get("account_key") or "").lower() == t8 for r in records)},
    ]


def cleanup(lab, manifest, params):
    company = params.get("company") or "none"
    lab.python(manifest["site"], ledger._mine(ledger.CLEANUP) + ledger.EXTRA_CLEANUP + SALES_CLEANUP + f"""
from pearnly_core.assistant import sessions
frappe.set_user({USER!r})
for sid in frappe.get_all("Pearnly Assistant Session", filters={{"user": {USER!r}, "project": {company!r}}},
                          pluck="session_id", limit_page_length=20):
    sessions.delete(sid)
gone = cleanup()
# 推送途中会留一条账套键为空、没有持有者的空闲写者锁（不挂连接，`CLEANUP` 收不到）：只删空闲的那条
frappe.db.delete("Cowork Push Writer Lock", {{"account_key": "", "holder": ["is", "not set"]}})
frappe.db.commit()
print(json.dumps({{"gone": gone, "left": leftovers()}}))
""")
