"""流程用例：CC 本对话账套（A 线 11-12，R2-5 端到端）——右栏选了 T.8，对话里的写入只进 T.8：建计划不问账套、模型在推送里另填
同期的 T.9 被拒、不填照 T.8 写。

R2-5（生产只读查清）：公司有两本同期账套（T.8、T.9，会计期间 2025、次年段到 2026-12-31），对话 2026-10、右栏显示 T.8，票据日期
2026-06；模型按公司资料里的期间自己判，最后在 `cowork_push_documents` 里自己填了别的账套键，直写卡死。修法见
`pearnly_core.assistant.ledger` 与 `pearnly_cowork.assistant.write_scope`。

做法（实验室假智能体 `lab_agents.py`：原文带 ``[lab-tool]{…}[/lab-tool]`` 就照真实引擎调那个工具，回「[lab-agent] 工具名 <结果原文>」）：
1. 造数据（产品自己的函数，形状同 `cowork_operator_lib.py`，换成本流程自己的连接名、账套键与公司税号）：一条 Express 连接，目录里
   同一税号两本同期账套 T.8、T.9（可写），从目录建公司；连接的缺省账套是 T.9（空账套回落连接缺省就会写错本，A6）。一张识别好的
   进项票、没有推送目标（识别时模型没给，R2-5 的样子）、票据日期 2026-06-09；付款与科目在两本账套里都记住了（写账判定没有要问的，
   T.9 被拒只能是因为不是本对话账套）；录入人有一台登记过的电脑（合成密钥 + 一次心跳）够得到这两本。实验室没有识别进程
   （识别只排队，`cc_send_while_waiting.py`），所以用预造的已识别单，不在对话里上传识别。
2. 新建页（还没建会话）选这家（月份默认当月）：账套胶囊未定（两本同期、没记住），点开选 T.8（测试机1 11-12：第一句之前
   就要能选），再说第一句建对话 → 接口读回的本对话账套就是 T.8、不出提问卡；右栏「ชุดบัญชี」一行显示 T.8。
3. 说一句建写入计划（``pearnly_write_plan`` 带这张单）→ ready、1 张，出「写入」；对话里不出任何提问卡。点「写入」，再按计划开始
   （``pearnly_write_start``）→ started。
4. 照 R2-5 真实路径：``cowork_push_documents`` 带这张单并另填 T.9 的连接与账套键 → 被拒（``not_this_conversation_book``），回包
   ``allowed_account_sets`` 只列 T.8；再推一次不填账套 → 交出去了（在路上：录入人的电脑够得到 T.8，见造数据；没有真的 CC 桌面版来
   领，结论停在已排队），库里落的才是结论。
5. verify：会话 ``project_ledger`` = T.8；这张票的推送目标是 T.8（``push_connection`` / ``push_account_set``）、推送记录都在 T.8，
   没有任何写向 T.9 的推送记录或写者锁；计划记的账套是 T.8。cleanup 删会话（计划随之删）与造的数据。不连 Express、不调模型。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cc_lib import JS  # noqa: E402
from cowork_operator_lib import CLEANUP, LABEL, TAX  # noqa: E402

# 新对话默认的月份：当月（CC 11-16 起，原来是上个月）；实验室与本机同一个时钟
CHAT_MONTH = __import__("datetime").date.today().strftime("%Y-%m")
TITLE = "CC 本对话账套（R2-5）：新建页选 T.8 再发第一句、右栏显示 T.8，建计划不问账套、模型另填同期 T.9 被拒只给 T.8、不填照 T.8 写，库里只有 T.8"
READY = "document.readyState === 'complete' && !!document.querySelector('textarea[aria-label]')"
USER = "admin@cowork.regression"
CHIP = "[data-slot=\"cc-quick-replies\"] button"
MY_LABEL = "Flow conversation ledger Express"
MY_TAX = "0994000777031"
T8, T9 = "flowt8", "flowt9"  # 账套键（Express 目录 source_id 规范化后，`normalize_express_account_key`）


def _mine(snippet: str) -> str:
    """代录流程的清理片段换成本流程自己的连接名、公司税号；账套锁按本流程的两个账套键放。"""
    return (snippet.replace(LABEL, MY_LABEL).replace(TAX, MY_TAX)
            .replace('frappe.db.delete("Cowork Push Writer Lock", {"account_key": "flowop"})',
                     f'frappe.db.delete("Cowork Push Writer Lock", {{"account_key": ["in", [{T8!r}, {T9!r}]]}})'))


# 清理另加两样（`CLEANUP` 按连接删，这两样不挂在连接上）：没有推送目标的票（按买方税号）、心跳记下的电脑行（标签 = 连接名）
EXTRA_CLEANUP = f'''
_by_connection = cleanup
def cleanup():
    gone = _by_connection()
    left = frappe.get_all("Cowork OCR History", filters={{"buyer_tax": {MY_TAX!r}}}, pluck="name", limit_page_length=0)
    if left:
        frappe.db.delete("Cowork OCR Correction", {{"source_history": ["in", left]}})
        frappe.db.delete("Cowork Push Record", {{"history": ["in", left]}})
        frappe.db.delete("Cowork OCR History", {{"name": ["in", left]}})
    frappe.db.delete("Cowork CC Desktop Device", {{"label": {MY_LABEL!r}, "device_owner": {USER!r}}})
    frappe.db.commit()
    return {{**gone, "documents": gone["documents"] + len(left)}}
'''


# 站点里造数据（同 `cowork_operator_lib.BASE`，目录里两本同期账套）
SEED = f'''
from uuid import uuid4
from pearnly_cowork.access import scope
from pearnly_cowork.connections import pool, service as connections
from pearnly_cowork.history import storage
from pearnly_cowork.push.application import service as application
frappe.set_user({USER!r})
cleanup()
ws = scope.existing_workspace()
mapping = {{"fallback_acc": "A", "vat_input_acc": "V", "ap_acc": "P", "revenue_acc": "S", "vat_output_acc": "O", "ar_acc": "R"}}
# 连接的缺省账套是 T.9：对话里空账套回落连接缺省就会写错本（A6）
conf = {{"account_set": "FLOWT9", "account_dir": "FLOWT9", "express_root": "D:\\\\FLOWL", **mapping}}
conn = connections.configure(ws, "express", uuid4().hex, {MY_LABEL!r}, conf, users=[{USER!r}])
# 录入人自己的一台 CC 桌面版够得到这两本（签发电脑密钥、按心跳的真实入口报一次可达行与科目表；不要求在线，D4），
# 推送的确认才交得出去（`push.native.service.has_writer`），账本投影才把去向抄到票上（`push_state.mirror`）。
# 心跳之后再装目录快照：目录（名称、会计期间、次年段）以快照为准，心跳只管可达行
from pearnly_cowork.companion import credentials, heartbeat
machine = credentials.authenticate(credentials.issue(conn["name"], uuid4().hex)["token"])
def reached(source, label):
    return {{"code": source, "name": label, "path": source, "root": "D:\\\\FLOWL", "writable": True, "mapping": mapping,
            "company": "Flow Ledger Co", "tax_id": {MY_TAX!r}}}
heartbeat._receive(machine, {{"account_set": "FLOWT9", "express_root": "D:\\\\FLOWL",
    "account_sets": [reached("FLOWT8", "T.8"), reached("FLOWT9", "T.9")],
    "accounts": [{{"code": code}} for code in ("A", "V", "P", "S", "O", "R", "5300")],
    "catalog": {{"products": [], "customers": []}}, "max_payload_version": 1}})
refresh = connections.request_refresh(conn["name"], uuid4().hex)
def book(source, label):
    return {{"source_id": source, "label": label, "attributes": {{"root": "D:\\\\FLOWL", "account_dir": source,
        "company": "Flow Ledger Co", "tax_id": {MY_TAX!r}, "branch": "00000", "period_start": "2025-01-01",
        "period_end": "2025-12-31", "next_end": "2026-12-31", "writable": True, "mapping": mapping}}}}
observation = {{"adapter": "express", "observed_at": "2026-10-01T00:00:00Z", "collector": {{"kind": "synthetic_lab"}},
    "account_sets": [book("FLOWT8", "T.8"), book("FLOWT9", "T.9")]}}
connections.install_snapshot(conn["name"], observation, refresh["request_id"], connections.detail(conn["name"])["revision"],
    {{"complete": True, "account_set": "FLOWT9", "root_key": "D:\\\\FLOWL", "mappings": {{"accounts": [], "clients": []}},
     "supplier_profiles": {{}}, "bank_index": [], "reported_config": {{}}}})
row = frappe.get_doc("Cowork ERP Connection", conn["name"])
for key in ({T8!r}, {T9!r}):
    pool.ensure_registered(row, key, root="D:\\\\FLOWL", now=frappe.utils.now_datetime())
company = companies.create(conn["name"], "FLOWT8")
frappe.db.commit()
def recognized(number):
    fields = {{"direction": "purchase", "seller_name": "Flow seller", "seller_tax": "0107561000013", "buyer_tax": {MY_TAX!r},
              "buyer_name": "Flow Ledger Co", "subtotal": "100", "vat": "7", "total_amount": "107", "date": "2026-06-09",
              "document_type": "tax_invoice", "invoice_number": number}}
    pages = [{{"page_index": 1, "fields": fields}}]
    made = storage.create_recognized(ws, pages, uuid4().hex, source="manual", confidence="high", ai_raw=pages)
    # 不设推送目标（R2-5：识别时没给）；付款方式、费用科目两本账套都记住（同复核页选过一次），判定没有要问的
    for source in ("FLOWT8", "FLOWT9"):
        application.save_choices(made["name"], conn["name"], source, json.dumps([
            {{"kind": "payment", "subject": "purchase|0107561000013", "value": "credit", "label": "Flow seller"}},
            {{"kind": "account", "subject": "purchase|0107561000013", "value": "5300", "label": "Flow seller"}}]))
    frappe.db.commit()
    return made["name"]
'''


# 本对话账套编号（Cowork 自账套身份跨电脑起是与电脑无关的 book_key，`companies.identity.book_id`，不再是「连接|键」）：
# 从这家的账套目录现取，流程不自己拼
LEDGER_IDS = """
from pearnly_cowork.companies import service as company_service
ids = {item["key"]: item["identity"] for item in company_service.listed_books(company_service.get(company["id"]), None)}
"""


def prepare(lab, manifest):
    site = manifest["site"]
    lab.python(site, f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    ref = "FLOW-CL-" + uuid.uuid4().hex[:6].upper()
    out = lab.python(site, _mine(CLEANUP) + EXTRA_CLEANUP + SEED + LEDGER_IDS + f"""
name = recognized({ref!r})
doc = frappe.db.get_value("Cowork OCR History", name, ["push_connection", "push_account_set"], as_dict=True)
print(json.dumps({{"company": company["id"], "title": company["title"], "doc": name, "connection": conn["name"],
                  "target_before": doc, "t8": ids.get({T8!r}, ""), "t9": ids.get({T9!r}, "")}}))
""")
    tag = "cl" + uuid.uuid4().hex[:6]
    conn = out["connection"]
    plan = {"name": "pearnly_write_plan", "input": {"documents": [out["doc"]]}}
    other = {"name": "cowork_push_documents", "input": {"documents": [out["doc"]], "connection": conn, "account_set_key": "FLOWT9"}}
    plain = {"name": "cowork_push_documents", "input": {"documents": [out["doc"]]}}
    return {**out, "ref": ref, "tag": tag, "cases": [],
            "open_line": f"{tag} 这家这个月的票",
            "plan_line": f"{tag} 把这张录进 Express [lab-tool]{json.dumps(plan)}[/lab-tool]",
            "other_line": f"{tag} [lab-tool]{json.dumps(other)}[/lab-tool]",
            "plain_line": f"{tag} [lab-tool]{json.dumps(plain)}[/lab-tool]"}


# 从假智能体回的「[lab-agent] 工具名 <工具结果原文>」里取出那段 JSON（气泡里结果之后可能跟着步骤行的字）：按括号配对截出第一段再解析
RESULT = (
    "const resultOf=(bubble,tool)=>{const text=bubble?bubble.innerText:''; const at=text.indexOf(tool+' {'); if(at<0) return null; "
    "const s=text.slice(text.indexOf('{',at)); let depth=0, str=false, esc=false; "
    "for(let i=0;i<s.length;i++){const c=s[i]; if(str){if(esc) esc=false; else if(c==='\\\\') esc=true; else if(c==='\"') str=false; continue;} "
    "if(c==='\"') str=true; else if(c==='{') depth++; else if(c==='}'){depth--; if(depth===0){try{return JSON.parse(s.slice(0,i+1));}catch(e){return {unparsed: s.slice(0,400)};}}}} "
    "return {unparsed: s.slice(0,400)};};"
    "const replies=()=>qa('[data-role=\"assistant\"]');"
    # 这一句（带工具名与本流程标签的那条用户消息）之后的那条回复里出现工具结果
    "const toolTurn=async(line,tool)=>{const before=replies().filter(e=>e.innerText.includes(tool+' {')).length; const sent=await say(line); "
    "const mine=()=>replies().filter(e=>e.innerText.includes(tool+' {'))[before]; "
    "await until(()=>!!mine()&&resultOf(mine(),tool)&&!resultOf(mine(),tool).unparsed,480); "
    "await until(()=>!q('[data-slot=\"cc-run-indicator\"]'),80); return {sent, result: resultOf(mine(),tool)};};"
    "const asked=()=>qa('[data-slot=\"cc-askq-question\"]').length;"
)

# 打开右栏：电脑点顶栏开关；手机点右上「…」→ 公司名（右栏盖满屏），同 `cowork_pending_operator.py`
OPEN_PANEL = (
    "const ledgerRow=()=>q('[data-slot=\"cc-company-ledger\"]'); const panel=()=>q('[data-slot=\"cc-session-panel\"]');"
    "if(!panel()){const t=q('[data-slot=\"cc-panel-toggle\"][aria-pressed=\"false\"]'); "
    "if(t) t.click(); else {const m=qa('.cc-mpill button')[1]; if(m) press(m); await until(()=>q('.cc-menu-item'),20); q('.cc-menu-item')?.click();}} "
    "await until(panel,120); "
    "const fold=q('[data-card=\"company\"] .cc-panel-card-toggle[aria-expanded=\"false\"]'); fold?.click(); "
    "await until(ledgerRow,120); "
    "const shown=()=>(ledgerRow()?.querySelector('.cc-panel-extra')?.innerText||'').trim();"
)
CLOSE_PANEL = "q('.cc-panel[data-mode=\"phone\"] .cc-panel-top .cc-icon-btn')?.click(); await sleep(600);"


def new_page_ledger(label: str) -> str:
    """新建页（还没建会话）选了公司后，在账套胶囊里选 ``label``（电脑在输入框下巴、手机在问候下面，测试机1 11-12：第一句之前就能选）。
    要先 pickCompany；回 chip_found / chip_before（服务端默认，空 = 未定时胶囊写「ชุดบัญชี」本身）/ chip_options / chip_after。"""
    return ("const chipEl=()=>q('[data-slot=\"cc-ledger-chip\"]'); const chipText=()=>(chipEl()?.innerText||'').trim(); "
            "const chip_found=!!(await until(chipEl,120)); const chip_before=chip_found?chipEl().getAttribute('title')||'':''; "
            "let chip_options=[], chip_after=''; "
            "if(chip_found){press(chipEl()); await until(()=>qa('.cc-menu-item').length>0,40); "
            "chip_options=qa('.cc-menu-item').map(e=>e.innerText.trim()); "
            f"qa('.cc-menu-item').find(e=>e.innerText.trim()==={json.dumps(label)})?.click(); "
            f"await until(()=>chipText()==={json.dumps(label)},60); await sleep(300); chip_after=chipText();}} ")


def steps(params, camera_code=None):
    sid = "new URLSearchParams(location.search).get('c')||''"
    return [
        {"name": "新建页选 T.8 后开对话", "js": JS + RESULT + "const switched=await toMode('chat'); await until(()=>q('[data-slot=\"cc-chin-company\"]')); "
         f"const picked=await pickCompany({json.dumps(params['title'])}); " + new_page_ledger("T.8") +
         f"const sent=await say({json.dumps(params['open_line'])}); "
         f"const replied=await until(()=>replies().some(e=>e.innerText.includes('[lab-agent]')&&e.innerText.includes({json.dumps(params['tag'])})),240); "
         "await until(()=>!q('[data-slot=\"cc-run-indicator\"]'),80); "
         f"await until(()=>!!({sid}),80); const session={sid}; "
         "const row=(await listed('chat')).find(s=>s.id===session)||{}; "
         "const read=session?((await call('pearnly_core.assistant.api.ledgers',{session_id:session}))||{}):{}; "
         "return {switched, picked, chip_found, chip_before, chip_options, chip_after, sent, replied, session, "
         "period: row.period||'', project: row.project||'', current: read.current||'', asked: asked()};"},
        {"name": "右栏显示 T.8", "js": JS + OPEN_PANEL + f"const session={sid}; "
         "const before=shown(); const row=ledgerRow(); if(!row) return {found:false, before}; press(row); "
         "await until(()=>qa('.cc-menu-item').length>0,40); const options=qa('.cc-menu-item').map(e=>e.innerText.trim()); "
         "const item=qa('.cc-menu-item').find(e=>e.innerText.trim()==='T.8'); item?.click(); "
         "const changed=await until(()=>shown()==='T.8',80); await sleep(800); "
         "const read=session?((await call('pearnly_core.assistant.api.ledgers',{session_id:session}))||{}):{}; "
         "const after=shown(); " + CLOSE_PANEL +
         "return {found:true, before, options, picked: !!item, changed, after, current: read.current||'', "
         "listed: (read.options||[]).map(o=>o.label)};"},
        {"name": "说一句建写入计划", "js": JS + RESULT + f"const turn=await toolTurn({json.dumps(params['plan_line'])},'pearnly_write_plan'); "
         "const r=turn.result||{}; window.__plan=r.plan||''; "
         f"const chip=await until(()=>!!q({json.dumps(CHIP)}),80); await sleep(800); "
         f"return {{sent: turn.sent, status: r.status||'', plan: r.plan||'', count: r.count, documents: r.documents||[], "
         f"skipped: r.skipped||[], account_sets: r.account_sets||[], chip, label: q({json.dumps(CHIP)})?.innerText.trim()||'', asked: asked(), "
         "raw: r.unparsed||''};"},
        {"name": "点写入", "js": JS + RESULT + f"const b=q({json.dumps(CHIP)}); if(!b) return {{clicked:false}}; const label=b.innerText.trim(); "
         "const users=()=>qa('[data-role=\"user\"]').map(e=>e.innerText.trim()); const before=users().length; b.click(); "
         "const bubble=await until(()=>users().length>before&&users().at(-1).includes(label),60); "
         "const replied=await until(()=>replies().some(e=>e.innerText.includes('[lab-agent] '+label)),240); "
         "await until(()=>!q('[data-slot=\"cc-run-indicator\"]'),80); "
         "return {clicked:true, label, bubble, replied, asked: asked()};"},
        # 实验室的模型不会自己开始：照 CC 听到「写入」后的做法，用原文带 [lab-tool] 让假智能体调 pearnly_write_start
        {"name": "按计划开始", "js": JS + RESULT + "const plan=window.__plan||''; if(!plan) return {started:false, reason:'no plan'}; "
         f"const line={json.dumps(params['tag'] + ' [lab-tool]')}+JSON.stringify({{name:'pearnly_write_start', input:{{plan}}}})+'[/lab-tool]'; "
         "const turn=await toolTurn(line,'pearnly_write_start'); const r=turn.result||{}; "
         f"await until(()=>!q({json.dumps(CHIP)}),60); "
         "return {sent: turn.sent, status: r.status||'', count: r.count, documents: r.documents||[], asked: asked(), raw: r.unparsed||''};"},
        # R2-5 真实路径：模型在推送工具里自己填了同期的另一本
        {"name": "另填 T.9 推送", "js": JS + RESULT + f"const turn=await toolTurn({json.dumps(params['other_line'])},'cowork_push_documents'); "
         "const r=((turn.result||{}).results||[])[0]||{}; "
         "return {sent: turn.sent, status: r.status||'', reason: r.reason||'', allowed: (r.allowed_account_sets||[]).map(a=>({connection:a.connection, key:a.key, label:a.label})), "
         "asked: asked(), raw: (turn.result||{}).unparsed||''};"},
        {"name": "不填账套推送", "js": JS + RESULT + f"const turn=await toolTurn({json.dumps(params['plain_line'])},'cowork_push_documents'); "
         "const r=((turn.result||{}).results||[])[0]||{}; "
         "return {sent: turn.sent, status: r.status||'', reason: r.reason||'', asked: asked(), raw: (turn.result||{}).unparsed||''};"},
    ]


REFUSED = ("not_this_conversation_book", "not_this_company_book", "ledger_needed", "not_started", "not_in_plan", "ledger_changed")


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, chose, made, clicked, started, other, plain = (by.get(k) or {} for k in (
        "新建页选 T.8 后开对话", "右栏显示 T.8", "说一句建写入计划", "点写入", "按计划开始", "另填 T.9 推送", "不填账套推送"))
    t8_only = [{"connection": params["connection"], "key": T8, "label": "T.8"}]
    return [
        {"case": "新建页（还没建会话）选了公司就有账套胶囊：两本同期、没记住时未定；点开列出 T.8、T.9，选 T.8 后显示 T.8；"
                 "再说第一句建对话（月份是当月、CC 回了这一句），服务端读回本对话账套就是 T.8，没出提问卡", "seen": opened,
         "ok": bool(opened.get("picked")) and opened.get("chip_found") is True and opened.get("chip_before") not in ("T.8", "T.9")
         and {"T.8", "T.9"} <= set(opened.get("chip_options") or []) and opened.get("chip_after") == "T.8"
         and bool(opened.get("replied")) and opened.get("period") == CHAT_MONTH and opened.get("project") == params["company"]
         and opened.get("current") == params["t8"] and opened.get("asked") == 0},
        {"case": "右栏账套一行显示新建页选的 T.8；点开列出 T.8、T.9，服务端读回仍是 T.8", "seen": chose,
         "ok": chose.get("found") is True and chose.get("before") == "T.8" and {"T.8", "T.9"} <= set(chose.get("options") or [])
         and bool(chose.get("changed")) and chose.get("after") == "T.8" and chose.get("current") == params["t8"]},
        {"case": "说一句建写入计划：这张单要写（ready、1 张，目标 T.8），出「写入」；不出选账套的提问卡", "seen": made,
         "ok": made.get("status") == "ready" and made.get("count") == 1 and made.get("documents") == [params["doc"]]
         and bool(made.get("chip")) and made.get("asked") == 0
         and [(item.get("account_set"), item.get("account_set_title")) for item in made.get("account_sets") or []]
         == [(T8, "T.8")]},
        {"case": "点「写入」= 这句话照常发出，CC 回了这一句；仍不出提问卡", "seen": clicked,
         "ok": bool(clicked.get("bubble")) and bool(clicked.get("replied")) and clicked.get("label") == made.get("label")
         and clicked.get("asked") == 0},
        {"case": "CC 按计划开始：started、这一张", "seen": started,
         "ok": started.get("status") == "started" and started.get("documents") == [params["doc"]]},
        {"case": "R2-5：模型在推送里另填同期的 T.9 → 被拒（不是本对话账套），回包只给 T.8", "seen": other,
         "ok": other.get("status") == "failed" and other.get("reason") == "not_this_conversation_book"
         and other.get("allowed") == t8_only},
        {"case": "不填账套再推：不再被账套闸 / 计划闸拦，交出去了（在路上）", "seen": plain,
         "ok": plain.get("status") in ("sent", "queued") and plain.get("reason") not in REFUSED and plain.get("asked") == 0},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
sessions = frappe.get_all("Pearnly Assistant Session", filters={{"user": {USER!r}, "project": {params['company']!r}}},
                          fields=["session_id", "project_ledger", "period"], limit_page_length=5)
plans = frappe.get_all("Pearnly Assistant Write Plan", filters={{"session_id": ["in", [s.session_id for s in sessions] or ["-"]]}},
                       fields=["name", "status", "ledger"], limit_page_length=5)
doc = frappe.db.get_value("Cowork OCR History", {params['doc']!r}, ["push_connection", "push_account_set", "last_push_status",
                          "last_push_reason"], as_dict=True) or {{}}
records = frappe.get_all("Cowork Push Record", filters={{"history": {params['doc']!r}}},
                         fields=["name", "status", "connection", "account_key"], limit_page_length=10)
t9 = {{"records": frappe.db.count("Cowork Push Record", {{"connection": {params['connection']!r}, "account_key": {T9!r}}}),
      "locks": frappe.db.count("Cowork Push Writer Lock", {{"account_key": {T9!r}}})}}
print(json.dumps({{"sessions": sessions, "plans": plans, "doc": doc, "records": records, "t9": t9}}, default=str))
""")
    sessions, plans, doc, records = got["sessions"], got["plans"], got["doc"], got["records"]
    target = (doc.get("push_connection"), str(doc.get("push_account_set") or "").lower())
    return [
        {"case": "库里这段对话的本对话账套是 T.8", "saved": sessions,
         "ok": len(sessions) == 1 and sessions[0]["project_ledger"] == params["t8"]},
        {"case": "写入计划记的账套是 T.8、已开始", "saved": plans,
         "ok": len(plans) == 1 and plans[0]["ledger"] == params["t8"] and plans[0]["status"] == "started"},
        {"case": "这张票的推送目标是 T.8（识别时没有目标，推的那一刻落到本对话账套）", "saved": {"before": params["target_before"], "now": doc},
         "ok": not (params["target_before"] or {}).get("push_account_set") and target == (params["connection"], T8)},
        {"case": "推送记录都在 T.8（一条、在路上），没有任何写向 T.9 的推送记录或写者锁", "saved": {"records": records, "t9": got["t9"]},
         "ok": len(records) == 1 and records[0]["status"] == "pending"
         and all(r["connection"] == params["connection"] and r["account_key"] == T8 for r in records)
         and got["t9"] == {"records": 0, "locks": 0}},
    ]


def cleanup(lab, manifest, params):
    """成功、失败、超时都跑：删这家公司的对话（计划随之删）、清掉造的连接、公司、单据与推送记录。"""
    company = params.get("company") or "none"
    lab.python(manifest["site"], _mine(CLEANUP) + EXTRA_CLEANUP + f"""
from pearnly_core.assistant import sessions
frappe.set_user({USER!r})
for sid in frappe.get_all("Pearnly Assistant Session", filters={{"user": {USER!r}, "project": {company!r}}},
                          pluck="session_id", limit_page_length=20):
    sessions.delete(sid)
gone = cleanup()
frappe.db.commit()
print(json.dumps({{"gone": gone, "left": leftovers()}}))
""")
