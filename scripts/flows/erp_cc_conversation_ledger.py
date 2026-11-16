"""流程用例：ERP 的 CC 本对话账套（A 线 11-12，D2：同 Cowork `cc_conversation_ledger.py`）——右栏选了 T.8，对话里的写入只进 T.8：
建计划不问账套、点「写入」开始、推送写向 T.8；网页按日期准备成同期 T.9 的单在对话里重推时改投 T.8；开始之后把右栏换成 T.9、
不建新计划直接推 → 被拒（``ledger_changed``）。

ERP 的模型给不了账套（`erp_push_documents` 没有账套参数，写哪本由服务端 `express.conversation` 定），所以 Cowork 那条「模型另填 T.9
被拒」在 ERP 是：网页路径（不在对话里）已把单记成 T.9（``ledger_book``），对话里推时照样写向本对话账套（施工记录第 14 节）。

做法（实验室假智能体 `lab_agents.py`：原文带 ``[lab-tool]{…}[/lab-tool]`` 就照真实引擎调那个工具，回「[lab-agent] 工具名 <结果原文>」）：
1. 造数据（产品自己的函数）：一家 ERP 公司（税号、分店 00001，勾「接 Express」）；一台合成电脑（`cc_desktop.books.replace`，同
   `datasets.py` 新开户的做法）报这家两本同期账套 T.8、T.9（会计期间 2025、次年段到 2026-12-31、可写），两本各放一份账套资料
   （`express.facts`：供应商、存货、科目），按 T.8 落一次主档（`express.masters.apply`：建出 Express 码对得上的供应商、存货、仓，
   记下「接上时刻」）。三张已提交的采购单（票据日期 2026-06-09，同一供应商同一存货）：
   - A：网页路径照常排上、按日期准备（同期两本、没选过 → 停在「选账套」），就是 R2-5 的样子；
   - B、C：网页按日期准备成了 T.9（``ledger_book`` = T.9，停在「仓库没有对应」，不在每分钟重试的原因里，后台不会动它）；
   - D：票据日期 2024-12-15，在两本的会计期间（2025 起、次年段到 2026-12-31）之外（Express 原生录单拒，Message#70）。
   写账规则要人定的项（对方、存货、科目…）在 T.8 上先记住（同网页选过一次），判定没有要问的，T.9 被拒只能是因为不是本对话账套。
   实验室没有真的 CC 桌面版来领，推出去的单停在已排队（写账件 ``book_key`` 就是写向哪本）。
2. 新建页（还没建会话）选这家（月份默认当月）：账套胶囊未定（两本同期、没记住），点开选 T.8，再说第一句建对话 → 接口
   读回的本对话账套就是 T.8、不出提问卡；右栏「ชุดบัญชี」一行显示 T.8（测试机1 11-12：第一句之前就要能选）。
3. 说一句建写入计划（A、D）→ ready、写 A 1 张、目标 T.8，D 跳过、原因期间不符（``account_period_mismatch``，不另选账套），出「写入」；
   不出任何提问卡。点「写入」、按计划开始 → started；推 A → 排上了（网页留在 A 上的「选哪本」不再挡：对话里按本对话账套重新准备）。
4. 说一句建写入计划（B、C）→ ready、2 张、目标 T.8；点「写入」、开始；推 B → 排上了（改投：原来记的是 T.9）。
5. 右栏改成 T.9（不建新计划）→ 推 C → 不推（``ledger_changed``），C 不动。
6. verify：会话 ``project_ledger`` = T.9（最后选的）；两份计划记的账套都是 T.8、都已开始；A、B 的 ``ledger_book`` = T.8、各有一件
   写向 T.8 的写账件；C、D 没有写账件，C 的 ``ledger_book`` 还是网页记的 T.9；没有任何写向 T.9 的写账件。cleanup 删会话（计划随之删）、
   作废三张单（撤掉没写的写账件）。不连 Express、不调模型。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cc_conversation_ledger import CHIP, CLOSE_PANEL, OPEN_PANEL, RESULT as _RESULT, new_page_ledger  # noqa: E402

# 认工具结果只认假智能体回的那一段（「[lab-agent] 工具名 <结果原文>」）：气泡里步骤行也写着「工具名 {入参}」，按工具名认会把
# 还没回结果时的入参当结果（11-12 手机格读到 {documents} 当成推送结果，结果全空）
# 新的那条回复按内容认、不按第几条认：手机上对话长了，前面的气泡不一定都在页面里（11-12 手机格第二次推送按序号认不到，等满 120 秒）；
# 推送还要回包里带着这次推的那张单（前一次推送的气泡后来又添了字，按「之前没见过的文字」会把它认成这一次的）
RESULT = _RESULT.replace("text.indexOf(tool+' {')", "text.indexOf('[lab-agent] '+tool+' {')").replace(
    "const toolTurn=async(line,tool)=>", "const toolTurn=async(line,tool,must)=>").replace(
    "const before=replies().filter(e=>e.innerText.includes(tool+' {')).length;",
    "const mark=e=>e.innerText.includes('[lab-agent] '+tool+' {'); const before=new Set(replies().filter(mark).map(e=>e.innerText));").replace(
    "const mine=()=>replies().filter(e=>e.innerText.includes(tool+' {'))[before];",
    "const mine=()=>replies().filter(e=>mark(e)&&!before.has(e.innerText)&&(!must||e.innerText.includes(must))).at(-1);")
assert RESULT.count("[lab-agent] '+tool") == 2 and "before.has" in RESULT and "tool,must" in RESULT, \
    "cc_conversation_ledger.RESULT changed: match its tool-result reader again"
from cc_lib import JS  # noqa: E402
from erp_lib import void_documents  # noqa: E402

# 新对话默认的月份：当月（CC 11-16 起，原来是上个月）；实验室与本机同一个时钟
CHAT_MONTH = __import__("datetime").date.today().strftime("%Y-%m")
TITLE = ("ERP CC 本对话账套（R2-5）：新建页选 T.8 再发第一句、右栏显示 T.8，建计划不问账套、点写入开始推进 T.8；网页记成 T.9 的单在对话里改投 T.8；"
         "开始后右栏换 T.9 不建新计划推被拒，库里没有写向 T.9 的写账件")
READY = "document.readyState === 'complete' && !!document.querySelector('textarea[aria-label]')"
USER = "admin@erp.regression"
MY_NAME = "Flow Ledger ERP Co"
MY_TAX = "0994000777043"  # 过泰国税号校验位
BRANCH = "00001"
DEVICE = "flow-erp-conversation-ledger"  # 合成电脑（只在账套表里有它报的行，同 `datasets.py`）
SELLER_TAX = "0107561000013"
T8_PATH, T9_PATH = "D:\\FLOWL\\2568\\T8LEDGER", "D:\\FLOWL\\2568\\T9LEDGER"

# 站点里造数据（可重复跑：公司、账套、账套资料、主档、记住的选择有就复用；三张单每次新开）
SEED = f'''
import time
from frappe.utils import now_datetime
from pearnly_core.cc_desktop import books
from pearnly_erp_portal.express import api as express_api, choices, contracts, facts, masters, push, target
from pearnly_erp_portal.masters import cc_projects, company as companies
from pearnly_erp_portal.purchasing import entry
frappe.set_user({USER!r})
co = frappe.db.get_value("ERP Company", {{"tax_id": {MY_TAX!r}, "branch": {BRANCH!r}}})
if not co:
    co = companies.create_company(company_name={MY_NAME!r}, tax_id={MY_TAX!r}, branch={BRANCH!r}, payment_methods=["cash"])["name"]
if not frappe.db.get_value("ERP Company", co, "express_enabled"):
    from pearnly_erp_portal.masters import cc_service
    cc_service.link_express(co)  # 正式接入路径（第 2 批起表单不再改「接 Express」）
frappe.db.commit()
mapping = {{"fallback_acc": "51-03-00-00", "vat_input_acc": "11-06-01-00", "ap_acc": "21-01-01-00", "revenue_acc": "41-01-00-00",
           "vat_output_acc": "21-04-01-00", "ar_acc": "11-03-01-00"}}
def year(code, label, path):
    return {{"code": code, "name": label, "company": {MY_NAME!r}, "tax_id": {MY_TAX!r}, "branch": {BRANCH!r}, "path": path,
            "root": "D:\\\\FLOWL", "row": 1, "writable": True, "uid": "flow:" + code, "period_start": "2025-01-01",
            "period_end": "2025-12-31", "next_end": "2026-12-31", "mapping": mapping}}
books.replace({DEVICE!r}, [year("T8", "T.8", {T8_PATH!r}), year("T9", "T.9", {T9_PATH!r})], now_datetime())
frappe.db.commit()
keys = {{row.path: row.book_key for row in frappe.get_all("Pearnly CC Book", filters={{"device": {DEVICE!r}}},
                                                          fields=["path", "book_key"], limit_page_length=10)}}
t8, t9 = keys[{T8_PATH!r}], keys[{T9_PATH!r}]
data = {{"version": 2, "customers": [],
        "suppliers": [{{"code": "FS01", "name": "Flow seller", "tax_id": {SELLER_TAX!r}, "branch": "00000"}}],
        "products": [{{"code": "FLP-01", "name": "Flow ledger item", "kind": "stock", "unit": "ชิ้น", "prices": ["100"]}}],
        "warehouses": [{{"code": "01", "name": "คลัง Flow"}}], "settings": {{"mainloc": "01"}}, "stock": [], "departments": [],
        "facts": {{"accounts": [{{"code": code, "name": code, "type": code[0]}} for code in (*mapping.values(), "53-30-00-00")],
                  "mapping": mapping, "catalog": {{"isacc_candidates": []}}}}}}
for key in (t8, t9):
    facts.save(key, data)
masters.apply(co, t8, data)  # 落一次主档（供应商、存货、仓带 Express 码），记下「接上时刻」
frappe.db.commit()
supplier = frappe.db.get_value("ERP Supplier", {{"express_code": "FS01", "express_company": co}}) or \\
    frappe.db.get_value("ERP Supplier", {{"tax_id": {SELLER_TAX!r}}})
product = frappe.db.get_value("ERP Product", {{"express_code": "FLP-01", "express_company": co}})
house = frappe.db.get_value("ERP Warehouse", {{"company": co, "express_code": "01", "is_active": 1}})
companies.set_active_company(co)
def bought(number, day="2026-06-09"):
    doc = entry.post({{"company": co, "supplier": supplier, "doc_date": day, "currency": "THB", "fx_rate": 1,
                      "has_vat": 1, "price_mode": "exclusive", "doc_kind": "purchase_invoice", "update_stock": 1,
                      "doc_no": number, "warehouse": house,
                      "lines": [{{"product": product, "item_type": "goods", "description": "flow", "qty": 1, "unit": "ชิ้น",
                                 "unit_price": 100, "vat_rate": 7, "warehouse": house, "location": ""}}]}},
                     request_id="flow-erp-ledger-" + number)
    frappe.db.commit()
    return doc["name"]
def settled(name):
    # 提交后排上的网页写账准备在后台进程里跑（不在对话里：按票据日期选账套）：等它停下再往下（同 `erp_document_express.py`）
    for _ in range(90):
        frappe.db.rollback()
        if frappe.db.get_value("Purchase Document", name, "ledger_state") != contracts.QUEUED:
            break
        time.sleep(1)
    return frappe.db.get_value("Purchase Document", name, ["ledger_state", "ledger_reason", "ledger_book", "ledger_job"],
                               as_dict=True)
def remember_on(book, name):
    # 写账规则要人定的项按这本账套先记住（同网页「选一次」，记在公司上）：在对话外只留这一本候选（按它判），逐项记第一个可选值
    real = target.candidates
    target.candidates = lambda company_row, day: [item for item in real(company_row, day) if item["key"] == book]
    try:
        for _ in range(6):
            result, _vat, _unmapped = push._prepare_body("Purchase Document", co, push.document("Purchase Document", name))
            if not str(result.get("hold") or "").startswith(contracts.CHOICE):
                return result
            for item in result.get("needed") or []:
                rows = [row for row in express_api._options(item, facts.load(book)) if row.get("code") and row["code"] != "__new__"]
                rows = rows or express_api._options(item, facts.load(book))
                choices.remember(co, item["kind"], item["subject"], rows[0]["code"], rows[0].get("name") or "")
            frappe.db.commit()
        return result
    finally:
        target.candidates = real
'''


def prepare(lab, manifest):
    site = manifest["site"]
    lab.python(site, f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    tag = "el" + uuid.uuid4().hex[:6]
    out = lab.python(site, SEED + f"""
a, b, c = (bought("FLOW-EL-" + {tag!r}.upper() + suffix) for suffix in ("-A", "-B", "-C"))
d = bought("FLOW-EL-" + {tag!r}.upper() + "-D", "2024-12-15")  # 在两本的会计期间（2025 起、次年段到 2026-12-31）之外
web = {{name: settled(name) for name in (a, b, c, d)}}
ready = remember_on(t8, a)
# B、C：网页按日期准备成了 T.9（记下 ledger_book = T.9），停在「仓库没有对应」（不在每分钟重试的原因里，后台不再动它）
for name in (b, c):
    frappe.db.set_value("Purchase Document", name, {{"ledger_state": contracts.HELD, "ledger_reason": contracts.WAREHOUSE_UNMAPPED,
                        "ledger_book": t9, "ledger_needed": None, "ledger_job": None}}, update_modified=False)
frappe.db.commit()
# 右栏下拉每一项的名称（账套名 · 位置，`target.option_name`）；默认值读 provider（旧代码没有它时记成缺，流程照跑、在页面上红）
labels = {{key: target.option_name(books.find(key)) for key in (t8, t9)}}
ledgers = getattr(cc_projects, "ledgers", None)
options = ledgers(co, {CHAT_MONTH!r}, "", {USER!r}) if ledgers else {{"default": "missing"}}
print(json.dumps({{"company": co, "title": frappe.db.get_value("ERP Company", co, "company_name"), "t8": t8, "t9": t9,
                  "label8": labels.get(t8, ""), "label9": labels.get(t9, ""), "default": options["default"],
                  "docs": [a, b, c], "outside": d, "web": web, "prepared": sorted(k for k in ready if k != "book") if isinstance(ready, dict) else [],
                  "hold": str((ready or {{}}).get("hold") or "")}}, default=str))
""")
    a, b, c = out["docs"]
    ids = {"a": f"P:{a}", "b": f"P:{b}", "c": f"P:{c}", "d": f"P:{out['outside']}"}

    def tool(name, documents):
        return f"{tag} [lab-tool]{json.dumps({'name': name, 'input': {'documents': documents}})}[/lab-tool]"

    return {**out, "tag": tag, "cases": [], "ids": ids,
            "open_line": f"{tag} 这家这个月的单",
            "plan_a": tool("pearnly_write_plan", [ids["a"], ids["d"]]),
            "plan_bc": tool("pearnly_write_plan", [ids["b"], ids["c"]]),
            "push_a": tool("erp_push_documents", [ids["a"]]),
            "push_b": tool("erp_push_documents", [ids["b"]]),
            "push_c": tool("erp_push_documents", [ids["c"]])}


# 右栏账套一行点开、选名称为 label 的那一项，读回服务端的本对话账套（电脑 / 手机同 Cowork 流程的开法）
def _pick_ledger(label):
    return (JS + OPEN_PANEL + "const session=new URLSearchParams(location.search).get('c')||''; "
            "const before=shown(); const row=ledgerRow(); if(!row) return {found:false, before}; press(row); "
            "await until(()=>qa('.cc-menu-item').length>0,40); const options=qa('.cc-menu-item').map(e=>e.innerText.trim()); "
            f"const item=qa('.cc-menu-item').find(e=>e.innerText.trim()==={json.dumps(label)}); item?.click(); "
            f"const changed=await until(()=>shown()==={json.dumps(label)},80); await sleep(800); "
            "const read=session?((await call('pearnly_core.assistant.api.ledgers',{session_id:session}))||{}):{}; "
            "const after=shown(); " + CLOSE_PANEL +
            "return {found:true, before, options, picked: !!item, changed, after, current: read.current||''};")


def _plan(line):
    return (JS + RESULT + f"const turn=await toolTurn({json.dumps(line)},'pearnly_write_plan'); "
            "const r=turn.result||{}; window.__plan=r.plan||''; "
            f"const chip=await until(()=>!!q({json.dumps(CHIP)}),80); await sleep(800); "
            "return {sent: turn.sent, status: r.status||'', plan: r.plan||'', count: r.count, documents: r.documents||[], "
            f"skipped: r.skipped||[], account_sets: r.account_sets||[], chip, label: q({json.dumps(CHIP)})?.innerText.trim()||'', "
            "asked: asked(), raw: r.unparsed||''};")


# 点输入框上方的快捷回复（现在是「写入」）= 这句话照常发出
CLICK = (JS + RESULT + f"const b=q({json.dumps(CHIP)}); if(!b) return {{clicked:false}}; const label=b.innerText.trim(); "
         "const users=()=>qa('[data-role=\"user\"]').map(e=>e.innerText.trim()); const before=users().length; b.click(); "
         "const bubble=await until(()=>users().length>before&&users().at(-1).includes(label),60); "
         "const replied=await until(()=>replies().some(e=>e.innerText.includes('[lab-agent] '+label)),240); "
         "await until(()=>!q('[data-slot=\"cc-run-indicator\"]'),80); "
         "return {clicked:true, label, bubble, replied, asked: asked()};")


def _start(tag):
    # 实验室的模型不会自己开始：照 CC 听到「写入」后的做法，用原文带 [lab-tool] 让假智能体调 pearnly_write_start
    return (JS + RESULT + "const plan=window.__plan||''; if(!plan) return {started:false, reason:'no plan'}; "
            f"const line={json.dumps(tag + ' [lab-tool]')}+JSON.stringify({{name:'pearnly_write_start', input:{{plan}}}})+'[/lab-tool]'; "
            "const turn=await toolTurn(line,'pearnly_write_start'); const r=turn.result||{}; "
            f"await until(()=>!q({json.dumps(CHIP)}),60); "
            "return {sent: turn.sent, status: r.status||'', count: r.count, documents: r.documents||[], asked: asked(), raw: r.unparsed||''};")


def _push(line, document):
    return (JS + RESULT + f"const turn=await toolTurn({json.dumps(line)},'erp_push_documents',{json.dumps(document)}); "
            "const r=((turn.result||{}).results||[])[0]||{}; "
            "return {sent: turn.sent, document: r.document||'', status: r.status||'', reason: r.reason||r.reason_code||'', "
            "asked: asked(), raw: (turn.result||{}).unparsed||''};")


def steps(params, camera_code=None):
    sid = "new URLSearchParams(location.search).get('c')||''"
    return [
        {"name": "新建页选 T.8 后开对话", "js": JS + RESULT + "const switched=await toMode('chat'); await until(()=>q('[data-slot=\"cc-chin-company\"]')); "
         f"const picked=await pickCompany({json.dumps(params['title'])}); " + new_page_ledger(params["label8"]) +
         f"const sent=await say({json.dumps(params['open_line'])}); "
         f"const replied=await until(()=>replies().some(e=>e.innerText.includes('[lab-agent]')&&e.innerText.includes({json.dumps(params['tag'])})),240); "
         "await until(()=>!q('[data-slot=\"cc-run-indicator\"]'),80); "
         f"await until(()=>!!({sid}),80); const session={sid}; "
         "const row=(await listed('chat')).find(s=>s.id===session)||{}; "
         "const read=session?((await call('pearnly_core.assistant.api.ledgers',{session_id:session}))||{}):{}; "
         "return {switched, picked, chip_found, chip_before, chip_options, chip_after, sent, replied, session, "
         "period: row.period||'', project: row.project||'', current: read.current||'', asked: asked()};"},
        {"name": "右栏显示 T.8", "js": _pick_ledger(params["label8"])},
        {"name": "A 建写入计划", "js": _plan(params["plan_a"])},
        {"name": "A 点写入", "js": CLICK},
        {"name": "A 按计划开始", "js": _start(params["tag"])},
        {"name": "A 推送", "js": _push(params["push_a"], params["ids"]["a"])},
        {"name": "B、C 建写入计划", "js": _plan(params["plan_bc"])},
        {"name": "B、C 点写入", "js": CLICK},
        {"name": "B、C 按计划开始", "js": _start(params["tag"])},
        {"name": "B 推送（网页记的是 T.9）", "js": _push(params["push_b"], params["ids"]["b"])},
        {"name": "右栏换 T.9", "js": _pick_ledger(params["label9"])},
        {"name": "C 不建新计划直接推", "js": _push(params["push_c"], params["ids"]["c"])},
    ]


ON_ITS_WAY = ("queued", "sent", "already_pending")


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, chose8, plan_a, click_a, start_a, push_a, plan_bc, click_bc, start_bc, push_b, chose9, push_c = (
        by.get(k) or {} for k in ("新建页选 T.8 后开对话", "右栏显示 T.8", "A 建写入计划", "A 点写入", "A 按计划开始", "A 推送", "B、C 建写入计划",
                                  "B、C 点写入", "B、C 按计划开始", "B 推送（网页记的是 T.9）", "右栏换 T.9", "C 不建新计划直接推"))
    ids, t8, label8, label9 = params["ids"], params["t8"], params["label8"], params["label9"]
    t8_only = [(t8, "T.8")]  # 计划里的目标名是账套名（`operator.book_target`），右栏下拉另带位置（`target.option_name`）

    def targets(made):
        return [(item.get("account_set"), item.get("account_set_title")) for item in made.get("account_sets") or []]

    return [
        {"case": "造的数据：两本同期都覆盖当月、没记住哪本（右栏没有默认）；网页按日期没能定 A 写哪本（同期两本），B、C 记成了 T.9",
         "seen": {"default": params["default"], "labels": [label8, label9], "web": params["web"], "prepared": params["prepared"],
                  "hold": params["hold"]},
         "ok": params["default"] == "" and bool(label8) and bool(label9) and label8 != label9
         and (params["web"].get(params["docs"][0]) or {}).get("ledger_book") in (None, "")
         and "payload" in params["prepared"]},
        {"case": "新建页（还没建会话）选了公司就有账套胶囊：两本同期、没记住时未定；点开列出 T.8、T.9，选 T.8 后显示 T.8；"
                 "再说第一句建对话（月份是当月、CC 回了这一句），服务端读回本对话账套就是 T.8，没出提问卡", "seen": opened,
         "ok": bool(opened.get("picked")) and opened.get("chip_found") is True and opened.get("chip_before") not in (label8, label9)
         and {label8, label9} <= set(opened.get("chip_options") or []) and opened.get("chip_after") == label8
         and bool(opened.get("replied")) and opened.get("period") == CHAT_MONTH and opened.get("project") == params["company"]
         and opened.get("current") == t8 and opened.get("asked") == 0},
        {"case": "右栏账套一行显示新建页选的 T.8；点开列出 T.8、T.9，服务端读回仍是 T.8", "seen": chose8,
         "ok": chose8.get("found") is True and chose8.get("before") == label8 and {label8, label9} <= set(chose8.get("options") or [])
         and bool(chose8.get("changed")) and chose8.get("after") == label8 and chose8.get("current") == t8},
        {"case": "A、D 建写入计划：ready、写 A 1 张、目标 T.8，出「写入」；D 的日期在 T.8 会计期间之外（Express 会拒）跳过、原因"
                 "期间不符，不另选别的账套；不出选账套的提问卡", "seen": plan_a,
         "ok": plan_a.get("status") == "ready" and plan_a.get("count") == 1 and plan_a.get("documents") == [ids["a"]]
         and [(item.get("doc_id"), item.get("reason")) for item in plan_a.get("skipped") or []]
         == [(ids["d"], "account_period_mismatch")]
         and bool(plan_a.get("chip")) and plan_a.get("asked") == 0 and targets(plan_a) == t8_only},
        {"case": "点「写入」= 这句话照常发出，CC 回了这一句；仍不出提问卡", "seen": click_a,
         "ok": bool(click_a.get("bubble")) and bool(click_a.get("replied")) and click_a.get("label") == plan_a.get("label")
         and click_a.get("asked") == 0},
        {"case": "CC 按计划开始：started、A", "seen": start_a,
         "ok": start_a.get("status") == "started" and start_a.get("documents") == [ids["a"]]},
        {"case": "推 A：交出去了（在路上），不被账套闸 / 计划闸拦、不问", "seen": push_a,
         "ok": push_a.get("document") == ids["a"] and push_a.get("status") in ON_ITS_WAY and push_a.get("asked") == 0},
        {"case": "B、C 建写入计划（网页记的是 T.9）：ready、2 张、目标 T.8，不问", "seen": plan_bc,
         "ok": plan_bc.get("status") == "ready" and plan_bc.get("count") == 2
         and plan_bc.get("documents") == [ids["b"], ids["c"]] and plan_bc.get("asked") == 0 and targets(plan_bc) == t8_only
         and bool(plan_bc.get("chip"))},
        {"case": "再点「写入」、开始：started、B 与 C", "seen": {"click": click_bc, "start": start_bc},
         "ok": bool(click_bc.get("bubble")) and bool(click_bc.get("replied")) and click_bc.get("asked") == 0
         and start_bc.get("status") == "started" and start_bc.get("documents") == [ids["b"], ids["c"]]},
        {"case": "推 B：改投本对话账套，交出去了（在路上）", "seen": push_b,
         "ok": push_b.get("document") == ids["b"] and push_b.get("status") in ON_ITS_WAY and push_b.get("asked") == 0},
        {"case": "右栏改成 T.9：显示 T.9，服务端读回本对话账套是 T.9", "seen": chose9,
         "ok": chose9.get("found") is True and chose9.get("before") == label8 and bool(chose9.get("changed"))
         and chose9.get("after") == label9 and chose9.get("current") == params["t9"]},
        {"case": "不建新计划直接推 C：不推，原因 ledger_changed（复述的是 T.8，不能写进 T.9）", "seen": push_c,
         "ok": push_c.get("document") == ids["c"] and push_c.get("status") == "not_pushed"
         and push_c.get("reason") == "ledger_changed"},
    ]


def verify(lab, manifest, params, camera_code=None):
    a, b, c = params["docs"]
    d = params["outside"]
    got = lab.python(manifest["site"], f"""
sessions = frappe.get_all("Pearnly Assistant Session", filters={{"user": {USER!r}, "project": {params['company']!r}}},
                          fields=["session_id", "project_ledger", "period"], limit_page_length=5)
plans = frappe.get_all("Pearnly Assistant Write Plan", filters={{"session_id": ["in", [s.session_id for s in sessions] or ["-"]]}},
                       fields=["name", "status", "ledger", "started"], order_by="creation asc", limit_page_length=10)
docs = {{name: frappe.db.get_value("Purchase Document", name, ["ledger_state", "ledger_reason", "ledger_book", "ledger_job"], as_dict=True)
        for name in {[a, b, c, d]!r}}}
jobs = {{name: frappe.get_all("Pearnly CC Job", filters={{"source_doctype": "Purchase Document", "source_name": name}},
                             fields=["name", "status", "book_key"], limit_page_length=10) for name in {[a, b, c, d]!r}}}
t9 = frappe.db.count("Pearnly CC Job", {{"book_key": {params['t9']!r}}})
print(json.dumps({{"sessions": sessions, "plans": plans, "docs": docs, "jobs": jobs, "t9_jobs": t9}}, default=str))
""")
    sessions, plans, docs, jobs = got["sessions"], got["plans"], got["docs"], got["jobs"]
    t8, t9 = params["t8"], params["t9"]
    started = [p for p in plans if p["status"] == "started"]
    return [
        {"case": "库里这段对话的本对话账套是最后选的 T.9", "saved": sessions,
         "ok": len(sessions) == 1 and sessions[0]["project_ledger"] == t9},
        {"case": "两份写入计划都记着建计划时的 T.8、都已开始", "saved": plans,
         "ok": len(started) == 2 and all(p["ledger"] == t8 for p in started)},
        {"case": "A：写向 T.8（ledger_book = T.8，一件写向 T.8 的写账件）", "saved": {"doc": docs[a], "jobs": jobs[a]},
         "ok": (docs[a] or {}).get("ledger_book") == t8 and len(jobs[a]) == 1 and jobs[a][0]["book_key"] == t8
         and (docs[a] or {}).get("ledger_job") == jobs[a][0]["name"]},
        {"case": "B：网页记的 T.9 改投 T.8（ledger_book = T.8，一件写向 T.8 的写账件）", "saved": {"doc": docs[b], "jobs": jobs[b]},
         "ok": (docs[b] or {}).get("ledger_book") == t8 and len(jobs[b]) == 1 and jobs[b][0]["book_key"] == t8},
        {"case": "C：被拒后没动（没有写账件，ledger_book 仍是网页记的 T.9）", "saved": {"doc": docs[c], "jobs": jobs[c]},
         "ok": not jobs[c] and not (docs[c] or {}).get("ledger_job") and (docs[c] or {}).get("ledger_book") == t9},
        {"case": "D（期间外）：没有写账件", "saved": {"doc": docs[d], "jobs": jobs[d]}, "ok": not jobs[d] and not (docs[d] or {}).get("ledger_job")},
        {"case": "没有任何写向 T.9 的写账件", "saved": got["t9_jobs"], "ok": got["t9_jobs"] == 0},
    ]


def cleanup(lab, manifest, params):
    """成功、失败、超时都跑：删这家公司的对话（计划随之删）、作废三张单（撤掉没写的写账件，不留成别的流程「等着录」的单）。"""
    company = params.get("company") or "none"
    lab.python(manifest["site"], f"""
from pearnly_core.assistant import sessions
frappe.set_user({USER!r})
for sid in frappe.get_all("Pearnly Assistant Session", filters={{"user": {USER!r}, "project": {company!r}}},
                          pluck="session_id", limit_page_length=20):
    sessions.delete(sid)
frappe.db.commit()
print("{{}}")
""")
    void_documents(lab, manifest["site"], [*(params.get("docs") or []), params.get("outside")])
