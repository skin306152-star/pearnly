"""流程用例：同一张票只写一次（复核 5 批之第 1 批，`pearnly_core.ledger_tickets`；11-08 生产 SincereIce 的形状）——
同一份 24 张票的 PDF 在代录里等着，其中 4 张已经从截图直写进了 Express（单号写法不同：截图读成 ``/``、PDF 读成 ``-``）。
代录开始前卡片标出这 4 张「已写入」和它们在 Express 里的单号；点「开始」只交另外 20 张；站点交单（服务端再判一次）同样
交出 20 张、跳过 4 张。只看我们自己的写入：会计在 Express 里手录的同号不在这里判。

数据：在有税号、接了 Express 的公司上跑（主会话 11-09：没有税号时身份只到「近似」，跳过这条路测不到）。这家公司没有税号就
开跑即报错，不悄悄跳过。单据按正式函数建（请求号固定、重跑复用），每次开跑前把 24 张放回「等着录」、4 张写过的放回「已写入」；
cleanup 把 24 张标成已在 Express 处理，不留在资料里（数据集不会越积越多）。网页上没有执行电脑：「开始」只截下交给站点的
start，站点交单在 verify 里以替身代替真正排写账件。
"""
from __future__ import annotations

import json
import uuid

from cc_lib import JS

TITLE = "同一张票只写一次：24 张里 4 张已从直写写进 Express，代录开始前卡片标出 4 张已写入、只交 20 张，站点交单同样跳过 4 张"
READY = "document.readyState === 'complete' && !!document.querySelector('[data-mode=\"operator\"]')"
USER = "admin@erp.regression"
TOTAL, WRITTEN = 24, 4
PREFIX = "TKONCE"


def prepare(lab, manifest):
    lab.python(manifest["site"], f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    company = manifest.get("company") or manifest["companies"][0]
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.express import contracts, operator, push, tickets
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.purchasing import entry as purchase, suppliers
co = {company!r}
frappe.set_user({USER!r})
if not str(frappe.db.get_value("ERP Company", co, "tax_id") or "").strip() or not push.enabled(co):
    raise RuntimeError("the dataset company " + co + " needs a tax number and a connected Express: without them a ticket is only near and is never skipped")
companies.set_active_company(co)
period = frappe.utils.add_months(frappe.utils.today(), -1)[:7]
day = period + "-07"
base = "010553600063"  # 泰国税号：第 13 位是校验位
tax = base + str((11 - sum(int(d) * (13 - i) for i, d in enumerate(base)) % 11) % 10)
supplier = frappe.db.get_value("ERP Supplier", {{"supplier_name": "ผู้ขายตั๋วเดียว"}}) or suppliers.create_supplier(
    supplier_name="ผู้ขายตั๋วเดียว", tax_id=tax)["name"]
line = {{"item_type": "service", "description": "ticket once", "qty": 1, "vat_rate": 7}}
def made(number, price, key):
    found = frappe.db.get_value(push.PURCHASE, {{"company": co, "doc_no": number}})
    if found:
        return found
    return purchase.post({{"company": co, "supplier": supplier, "doc_date": day, "currency": "THB", "fx_rate": 1, "has_vat": 1,
                          "price_mode": "exclusive", "doc_kind": "purchase_invoice", "update_stock": 0, "doc_no": number,
                          "lines": [{{**line, "unit_price": price}}]}}, request_id="ticket-once-" + co + "-" + key)["name"]
waiting, twins = [], []
for i in range(1, {TOTAL} + 1):
    waiting.append(made("{PREFIX}-%02d" % i, 100 + i, "w%02d" % i))
for i in range(1, {WRITTEN} + 1):
    twins.append(made("{PREFIX}/%02d" % i, 100 + i, "t%02d" % i))  # 同一张票、截图读出的写法
for name in waiting + twins:
    doc = frappe.get_doc(push.PURCHASE, name)
    key, level = tickets.purchase_identity(doc)
    frappe.db.set_value(push.PURCHASE, name, {{"ticket_key": key, "ticket_level": level, "doc_date": day}}, update_modified=False)
for name in waiting:  # 等着录：电脑离线停下（真实的停下状态之一，资料里列的是排着、停下、失败的）
    frappe.db.set_value(push.PURCHASE, name, {{"ledger_state": contracts.HELD, "ledger_job": "", "ledger_docnum": "",
                                              "ledger_reason": contracts.OFFLINE}}, update_modified=False)
for i, name in enumerate(twins, start=1):
    frappe.db.set_value(push.PURCHASE, name, {{"ledger_state": contracts.WRITTEN, "ledger_job": "",
                                              "ledger_docnum": "RR-ONCE-%02d" % i, "ledger_reason": ""}}, update_modified=False)
frappe.db.commit()
levels = sorted(set(frappe.get_all(push.PURCHASE, filters={{"name": ["in", waiting + twins]}}, pluck="ticket_level")))
docs = operator.sources(co, period, 0, 500)["documents"]
ours = ["P:" + name for name in waiting]
print(json.dumps({{"title": frappe.db.get_value("ERP Company", co, "company_name") or co, "period": period, "waiting": ours,
                  "twins": ["P:" + name for name in twins], "skipped": ours[:{WRITTEN}], "levels": levels,
                  "listed": [d["doc_id"] for d in docs if d["doc_id"] in ours]}}))
""")
    tag = "flow" + uuid.uuid4().hex[:6]
    return {**out, "company": company, "tag": tag, "line": f"{tag} 录这 24 张", "cases": []}


def steps(params, camera_code=None):
    title, line = params["title"], params["line"]
    return [
        {"name": "开始前卡片", "js": JS + "await toMode('operator'); await until(()=>q('[data-slot=\"cc-chin-company\"]')); "
         f"const picked=await pickCompany({json.dumps(title)}); const sent=await say({json.dumps(line)}); "
         "const card=await until(()=>q('[data-slot=\"cc-op-before\"] .cc-op-before-body'),160); await sleep(800); "
         "for(let k=0;k<5;k++){const more=q('[data-slot=\"cc-op-before\"] .cc-show-more'); if(!more) break; more.click(); await sleep(600);} "
         "const written=qa('[data-slot=\"cc-op-before\"] [data-slot=\"cc-op-written\"]').map(e=>e.innerText.trim()); "
         "return {picked, sent, card, written};"},
        # 11-11 一句话下令：开始前卡片没有「开始」；说一句让假智能体照真实引擎调 pearnly_write_plan，计划按完整清单算、写过的票
        # 先核账本（实验栈没有 CC 桌面版：核不了），不在计划里、进跳过清单，CC 复述时问跳过还是再录（计划内容在 verify 里读回）
        {"name": "说一句建写入计划", "js": JS + f"const sent=await say({json.dumps(params['tag'] + ' 录吧 [lab-tool]' + json.dumps({'name': 'pearnly_write_plan', 'input': {}}) + '[/lab-tool]')}); "
         "const found=()=>qa('[data-role=\"assistant\"]').find(e=>e.innerText.includes('pearnly_write_plan {')); "
         "await until(()=>!!found()&&found().innerText.includes('\"status\"'),240); "
         # 回话里的工具结果原文按括号配对取出整段 JSON（ERP 实验栈的单都在等电脑读主档，计划是 nothing_to_write、不存库，只能从这里读）
         "const text=found()?found().innerText:''; const at=text.indexOf('{', text.indexOf('pearnly_write_plan {')); let depth=0, end=-1, quote=false; "
         "for(let k=at;k>=0&&k<text.length;k++){ const ch=text[k]; if(quote){ if(ch==='\\\\') k++; else if(ch==='\"') quote=false; continue; } "
         "if(ch==='\"') quote=true; else if(ch==='{') depth++; else if(ch==='}'){ depth--; if(!depth){ end=k; break; } } } "
         "let plan=null; try{ plan=JSON.parse(text.slice(at, end+1)); }catch(e){} "
         "return {sent, replied: !!found(), status: plan&&plan.status, documents: plan?plan.documents:null, skipped: plan?plan.skipped:null};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    card, made = by.get("开始前卡片") or {}, by.get("说一句建写入计划") or {}
    written = card.get("written") or []
    planned = {"documents": made.get("documents") or [],
               "skipped": [item for item in made.get("skipped") or [] if item.get("reason") == "already_entered"]}
    ours = set(params["waiting"])
    entered = {item["doc_id"]: item for item in planned["skipped"]}
    return [
        {"case": "数据：24 张等着录的票都在资料里，身份都是精确的（这家公司有税号）", "seen": {"listed": len(params["listed"]),
         "levels": params["levels"]}, "ok": len(params["listed"]) == len(params["waiting"]) and params["levels"] == ["exact"]},
        {"case": "开始前卡片标出 4 张已写入，各带它在 Express 里的单号", "seen": {"written": written},
         "ok": len(written) == len(params["skipped"]) and all(f"RR-ONCE-{i:02d}" in " ".join(written) for i in range(1, len(params["skipped"]) + 1))},
        {"case": "说一句：CC 建了写入计划", "seen": {k: made.get(k) for k in ("sent", "replied", "status")}, "ok": bool(made.get("replied"))},
        {"case": "写入计划：已写入的 4 张不在计划里、进跳过清单（我们写过、账本核不了：实验栈没有 CC 桌面版），各带 Express 单号；"
                 "另外 20 张都有着落（在计划里，或因等电脑读主档另列原因跳过：ERP 实验栈没读过主档）",
         "seen": {"status": made.get("status"), "documents": len(planned["documents"]), "entered": entered,
                  "others": sorted({item.get("reason") for item in made.get("skipped") or [] if item.get("doc_id") not in entered})},
         "ok": sorted(entered) == sorted(params["skipped"])
         and all(item.get("in_ledger") == "could_not_check" and str(item.get("docnum") or "").startswith("RR-ONCE-")
                 for item in entered.values())
         and not set(planned["documents"]) & set(params["skipped"])
         and ours - set(params["skipped"]) <= set(planned["documents"]) | {item.get("doc_id") for item in made.get("skipped") or []}},
    ]


def verify(lab, manifest, params, camera_code=None):
    """站点交单（服务端再判一次，不信页面）：24 张一起交，4 张回「已写入」带单号、20 张交出；说了再写一次的那张照交。
    真正排写账件换成替身（不建写账件、不改单据）。"""
    company = manifest.get("company") or manifest["companies"][0]
    out = lab.python(manifest["site"], f"""
from unittest import mock
from pearnly_erp_portal.express import operator, push
frappe.set_user({USER!r})
sent = []
with mock.patch.object(push, "send", lambda doctype, name, operator=None: sent.append(name) or {{"status": "queued"}}), \\
        mock.patch.object(push, "release_operator", lambda doctype, name: None):
    results = operator.submit({company!r}, {params["waiting"]!r}, {{"task": "flow"}}, "PC-FLOW")
    again = operator.submit({company!r}, {params["skipped"][:1]!r}, {{"task": "flow", "again": {params["skipped"][:1]!r}}}, "PC-FLOW")
frappe.db.rollback()
plan = frappe.get_all("Pearnly Assistant Write Plan", filters={{"user": {USER!r}}}, fields=["documents", "skipped"],
                      order_by="creation desc", limit_page_length=1)
planned = {{"documents": [d["doc_id"] for d in json.loads(plan[0].documents or "[]")] if plan else [],
           "skipped": [s for s in json.loads(plan[0].skipped or "[]") if s.get("reason") == "already_entered"] if plan else []}}
print(json.dumps({{"planned": planned, "written": [r["doc_id"] for r in results if r["status"] == "written"],
                  "docnums": [r["reason"] for r in results if r["status"] == "written"],
                  "sent": len(sent) - len(again), "again": [r["status"] for r in again]}}))
""")
    return [
        {"case": "站点交单同样跳过这 4 张（带 Express 单号）、交出 20 张", "saved": out,
         "ok": out["written"] == params["skipped"] and out["sent"] == len(params["waiting"]) - len(params["skipped"])
         and all(n.startswith("RR-ONCE-") for n in out["docnums"])},
        {"case": "人说了再写一次的那张照交", "saved": out["again"], "ok": out["again"] == ["queued"]},
    ]


def cleanup(lab, manifest, params):
    """24 张标成已在 Express 处理（不留在资料里，下次开跑放回）；写过的 4 张照旧。"""
    lab.python(manifest["site"], f"""
from pearnly_erp_portal.express import contracts, push
for doc_id in {params.get("waiting") or []!r}:
    frappe.db.set_value(push.PURCHASE, doc_id.split(":", 1)[1], {{"ledger_state": contracts.HANDLED, "ledger_job": ""}},
                        update_modified=False)
frappe.db.commit()
print("{{}}")
""")
