#!/usr/bin/env python3
"""固定场景（部署前跑，真模型、要花钱）：代录里放一份 24 张同一买方的销售税票 PDF，客户只问一次（11-08 生产事故的同形重放）。

事故：Cowork 的 CC「代录」里放 24 页 PDF（24 张销售税票，卖方 ชินเซียร์ ไอซ์、买方都是 Big C 总部、每张 286.76），说「把这份 PDF 录进
Express」；Express 主档里同一个买方税号有三个客户、这家从没记过对应。旧代码：CC 去调直写推送被拒、说出内部词；点开始后 24 张各停在
「要选客户」、出 24 张一样的提问卡；任务卡列 24 行内部编号；提问卡标题写「อื่นๆ」。本场景在本机实验栈用真模型、真识别把这一遍
照用户的样子做一次（Cowork、ERP 各一遍，电脑宽度、手机 375 宽各一遍），断言（任何一条不过即不过）：

  ① 代录会话的工具（远端会话 ``agent.tools``，引擎 ``sessions.retrieve``）里没有 cowork_push_documents / erp_push_documents；
     CC 的回话里不出现 operator、wrong_mode、CWH-、64 位哈希这类内部词
  ② 开始卡里「客户」这一题只出现一次，标题不是「其他 / อื่นๆ / Other」
  ③ 答完开始后任务里 24 张都交出去（排队），没有一张停在「要选客户」，页面上不出现逐张提问卡（operator_choose）
  ④ 任务行与步骤里每张单写「单号 · 对方」，不出现 CWH- / P: / I: / S: / 哈希
  ⑤ 站点记住了这个买方的对应（Cowork ``Cowork Express Choice`` / ERP ``ERP Express Choice``，kind=party）

到交单排队为止：实验栈没有 CC 桌面版，真录进 Express 那一步验不了。

素材：`tests/fixtures/scenarios/party-once-24-sales.pdf`（`make_party_once_pdf.py` 合成，合成税号，不是真实客户文件）。每遍先清掉上一遍
留下的（Cowork 按连接标签整套删；ERP 留着公司，删这家的收单记录、单据、写账件、记住的选择、代录任务与会话），两个产品各建「这家
公司的 Express 账套 + 主档里三个同税号的 Big C 客户」、从没记过对应。电脑那遍界面说中文、说中文那句；手机那遍界面说泰文、说泰文那句。

花费（2026-11 价，Opus 5.5 对话 + Claude 识别）：每遍对话约 0.1–0.3 美元；识别 24 页每遍约 0.02（全走 Haiku）到 0.5（升 Opus）美元，
ERP 先按采购读、卖方是本公司再按销售读，最多两倍。四遍合计按 1–2 美元备。`--reuse` 让手机那遍不放 PDF、直接用电脑那遍识别好的单
（不再付识别费，只验开始卡、交单与页面）。

跑法（本机实验栈，例 pearnly-parity-lab；先把真引擎接上，见下）：

    PEARNLY_FLOWS_LAB=pearnly-parity-lab REGRESSION_LAB_PORT=18941 \\
      python3 scripts/scenarios/operator_party_once.py --product all --viewport all --shots <截图目录> --out <结果.json>

接真引擎（只在实验栈；密钥只放站点配置与容器环境，不进仓库、不打印）：

    python3 scripts/scenarios/operator_party_once.py --engine-on <密钥文件> --src <实验栈源码工作树>
    ...跑场景...
    python3 scripts/scenarios/operator_party_once.py --engine-off --src <实验栈源码工作树>

``--engine-on`` 用覆盖文件重起 bench：ANTHROPIC_BASE_URL 指真 Anthropic、带识别进程与派发进程、PEARNLY_ANTHROPIC_API_KEY；两个站点
``pearnly_assistant_api_key`` 换成真密钥、``pearnly_ocr_engine=claude``。``--engine-off`` 站点密钥换回假值、识别引擎配置去掉、
不带覆盖文件重起 bench（回到假智能体）。重起前先确认没有别的流程在用这个实验栈（``ps -axo command | grep flows/run.py``）。
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PDF = ROOT / "tests" / "fixtures" / "scenarios" / "party-once-24-sales.pdf"
# 复核 5 批之第 1 批（同一张票只写一次）：先在直写里放前 4 页的截图（同 11-08 岳父逐张截图直写），再在代录里放整份 PDF
SHOTS = [ROOT / "tests" / "fixtures" / "scenarios" / f"party-once-screenshot-{i:02d}.jpg" for i in range(1, 5)]
DIRECT_LINES = {"desktop": "把这 4 张录进 Express", "mobile": "บันทึก 4 ใบนี้เข้า Express"}
DIRECT_DOCNUM = "RR-DIRECT-{:02d}"
# 花费硬封顶（主会话 11-09：这次 2 美元）：每遍开跑前按最坏单价估这一遍，累计超过 --budget 就不开跑。单价取设计文档文末的实测折算
# 往高里算（识别升 Opus 约 0.02 美元 / 页，按 0.025；对话一轮 11-09 实测引擎账单 0.07–0.32 美元（每轮写约 5 万 token 缓存），按 0.35）
PAGE_MAX, TURN_MAX = 0.025, 0.35
PAGES = 24
FIRST_REF = "IV69/00751"
SELLER_TAX = "0105561077332"
SELLER = "บริษัท ชินเซียร์ ไอซ์ จำกัด"
BUYER_TAX = "0107536999991"
BUYER = "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)"
# Express 主档里同一个买方税号的三个客户（这家从没记过对应）；场景答第一个
CUSTOMERS = [{"code": "BIGC", "name": "BIG C", "tax_id": BUYER_TAX, "branch": "00000"},
             {"code": "BIGC-001", "name": "BIG C-001 (พลับพลาไชย)", "tax_id": BUYER_TAX, "branch": "00151"},
             {"code": "BIGC-002", "name": "BIG C-002 (ซอยประชุม)", "tax_id": BUYER_TAX, "branch": "00087"}]
PICK = CUSTOMERS[0]["code"]
LINES = {"desktop": ("zh", "把这份 PDF 录进 Express"), "mobile": ("th", "นำไฟล์ PDF บันทึกขายเชื่อเข้าบริษัทนี้")}
PUSH_TOOLS = ("cowork_push_documents", "erp_push_documents")
SITES = {"cowork": "cowork.regression.localhost", "erp": "erp.regression.localhost"}
USERS = {"cowork": "admin@cowork.regression", "erp": "admin@erp.regression"}
FAKE_KEY = "sk-ant-regression-lab-" + "0" * 24
# 回话 / 页面里不该出现的内部词：英文 operator 一词、拒绝码、单据内部编号、64 位哈希
LEAKS = [re.compile(r"\boperator\b", re.I), re.compile(r"wrong_mode"), re.compile(r"CWH-"), re.compile(r"\b[0-9a-f]{64}\b")]
INTERNAL_IDS = [re.compile(r"CWH-"), re.compile(r"(?<![A-Za-z])[PIS]:\S"), re.compile(r"\b[0-9a-f]{32,64}\b")]
OTHER_TITLES = ("其他", "อื่นๆ", "Other")
CUSTOMER_TITLES = ("客户", "ลูกค้า", "Customer")


def _flows():
    """流程用例框架（实验室容器、站点里跑 Python、建会话）：照 `scripts/flows/run.py` 的 ``Lab``。"""
    sys.path.insert(0, str(ROOT / "scripts" / "flows"))
    spec = importlib.util.spec_from_file_location("flows_run", ROOT / "scripts" / "flows" / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUN = _flows()
from cc_lib import JS  # noqa: E402  （_flows 已把 scripts/flows 放进路径）


# ───────────────────────────── 造数据（站点里跑，产品自己的函数）─────────────────────────────

COWORK_LABEL = "Scenario party-once Express"
COWORK_KEY = "partyonce"

COWORK_SETUP = f'''
from uuid import uuid4
from pearnly_cowork.access import scope
from pearnly_cowork.companies import service as companies
from pearnly_cowork.connections import pool, service as connections
from pearnly_core.express_operator import tasks
USER = {USERS["cowork"]!r}
frappe.set_user(USER)
def cleanup():
    """上一遍留下的：这条连接（标签）下的公司、单据、推送记录、锁、记住的选择；这位用户这家公司的代录任务与会话。"""
    linked = ["Cowork Companion Delivery", "Cowork Companion Request", "Cowork ERP Refresh", "Cowork ERP Snapshot",
              "Cowork Express Account Facts", "Cowork Express Account Set", "Cowork Express Agent Credential", "Cowork Express Choice",
              "Cowork Express Choice Change", "Cowork Express Reach", "Cowork Express Readback", "Cowork Folder Intake File",
              "Cowork Folder Intake Run", "Cowork Push Attempt", "Cowork Push Notification", "Cowork Push Writer Lock",
              "Cowork Push Record", "Cowork Tax Draft"]
    gone = {{"connections": 0, "documents": 0, "tasks": 0}}
    for c in frappe.get_all("Cowork ERP Connection", filters={{"label": {COWORK_LABEL!r}}}, pluck="name", limit_page_length=20):
        for company in frappe.get_all("Cowork Company", filters={{"seed_connection": c}}, pluck="name", limit_page_length=20):
            for task in frappe.get_all(tasks.DOCTYPE, filters={{"company": company}}, pluck="name", limit_page_length=0):
                frappe.db.delete("Pearnly Assistant Session", {{"operator_task": task}})
                frappe.db.delete(tasks.DOCUMENT, {{"parent": task}})
                frappe.db.delete(tasks.DOCTYPE, {{"name": task}})
                gone["tasks"] += 1
            companies.delete(company)
            frappe.db.delete("Cowork Company Choice", {{"company": company}})
        hist = frappe.get_all("Cowork OCR History", filters={{"push_connection": c}}, pluck="name", limit_page_length=0)
        if hist:
            frappe.db.delete("Cowork OCR Correction", {{"source_history": ["in", hist]}})
            frappe.db.delete("Cowork OCR History", {{"name": ["in", hist]}})
        records = frappe.get_all("Cowork Push Record", filters={{"connection": c}}, pluck="name", limit_page_length=0)
        if records:
            frappe.db.delete("Cowork Push Writer Lock", {{"holder": ["in", records]}})
        for dt in linked:
            frappe.db.delete(dt, {{"connection": c}})
        frappe.db.delete("Cowork ERP Connection", {{"name": c}})
        gone["connections"] += 1
        gone["documents"] += len(hist)
    frappe.db.delete("Cowork Push Writer Lock", {{"account_key": {COWORK_KEY!r}}})
    frappe.db.commit()
    return gone
'''

COWORK_PREPARE = COWORK_SETUP + f'''
gone = cleanup()
ws = scope.existing_workspace()
conf = {{"account_set": "PARTYONCE", "account_dir": "PARTYONCE", "express_root": "D:\\\\PARTYONCE", "fallback_acc": "53-01",
        "vat_input_acc": "11-61", "ap_acc": "21-01", "revenue_acc": "41-01", "vat_output_acc": "21-61", "ar_acc": "11-31"}}
conn = connections.configure(ws, "express", uuid4().hex, {COWORK_LABEL!r}, conf, users=[USER])
refresh = connections.request_refresh(conn["name"], uuid4().hex)
observation = {{"adapter": "express", "observed_at": "2026-10-01T00:00:00Z", "collector": {{"kind": "synthetic_lab"}},
    "account_sets": [{{"source_id": "PARTYONCE", "label": {SELLER!r}, "attributes": {{"root": "D:\\\\PARTYONCE", "account_dir": "PARTYONCE",
        "company": {SELLER!r}, "tax_id": {SELLER_TAX!r}, "branch": "00000", "period_start": "2026-01-01", "period_end": "2026-12-31",
        "writable": True, "mapping": {{k: conf[k] for k in ("fallback_acc", "vat_input_acc", "ap_acc", "revenue_acc", "vat_output_acc", "ar_acc")}}}}}}]}}
connections.install_snapshot(conn["name"], observation, refresh["request_id"], conn["revision"],
    {{"complete": True, "account_set": "PARTYONCE", "root_key": "D:\\\\PARTYONCE", "mappings": {{"accounts": [], "clients": []}},
     "supplier_profiles": {{}}, "bank_index": [], "reported_config": {{}}}})
doc = frappe.get_doc("Cowork ERP Connection", conn["name"])
pool.ensure_registered(doc, {COWORK_KEY!r}, root="D:\\\\PARTYONCE", now=frappe.utils.now_datetime())
# 这份账套的主档（同 CC 桌面版心跳报的账套资料）：三个同税号的 Big C 客户、科目表
# 这位用户的一台 CC 桌面版（合成，同 `cc_machine_operator_ai` 流程；没有电脑时 Cowork 不收单、不推送，「还没连接 CC 桌面版」）：
# 由它报这份账套（电脑密钥挂在这条连接上，上一遍的随连接一起清掉）
from pearnly_cowork.companion import credentials
machine = credentials.register("scenario-party-once-" + uuid4().hex[:6], express=True, device_key="scenario-party-once-" + uuid4().hex,
                               device_name="Scenario PC")
from pearnly_cowork.connections.member import Member
pool.apply_report(doc, Member(device=machine["device"], legacy=True), observation["account_sets"], frappe.utils.now_datetime())
pool.put_facts(doc, {COWORK_KEY!r}, {{"reported_customers": {CUSTOMERS!r}, "reported_suppliers": [],
    "reported_accounts": [{{"code": c, "name": c}} for c in ("11-31", "11-61", "21-01", "21-61", "41-01", "53-01")]}},
    device=machine["device"], routine=True, now=frappe.utils.now_datetime())
company = companies.create(conn["name"], "PARTYONCE")
frappe.db.commit()
print(json.dumps({{"gone": gone, "company": company["id"], "title": company["title"], "connection": conn["name"],
                  "choices": frappe.db.count("Cowork Express Choice", {{"connection": conn["name"], "kind": "party"}})}}))
'''

ERP_SETUP = f'''
from pearnly_core.cc_desktop import books
from pearnly_core.express_operator import tasks
from pearnly_erp_portal.express import facts, masters, push, target
from pearnly_erp_portal.masters import company as companies
USER = {USERS["erp"]!r}
frappe.set_user(USER)
DEVICE = "scenario-party-once"
def company_of():
    return frappe.db.get_value("ERP Company", {{"company_name": {SELLER!r}}})
def cleanup():
    """这家公司上一遍留下的：对话收单记录、它提交出的单据与写账件、记住的选择、代录任务与会话（公司本身留着）。"""
    co = company_of()
    gone = {{"intakes": 0, "documents": 0, "tasks": 0}}
    if not co:
        return gone
    for task in frappe.get_all(tasks.DOCTYPE, filters={{"company": co}}, pluck="name", limit_page_length=0):
        frappe.db.delete("Pearnly Assistant Session", {{"operator_task": task}})
        frappe.db.delete(tasks.DOCUMENT, {{"parent": task}})
        frappe.db.delete(tasks.DOCTYPE, {{"name": task}})
        gone["tasks"] += 1
    intakes = frappe.get_all("ERP LINE Intake", filters={{"company": co}}, pluck="name", limit_page_length=0)
    if intakes:
        frappe.db.delete("ERP LINE Intake", {{"name": ["in", intakes]}})
        gone["intakes"] = len(intakes)
    for doctype in (push.SALES, push.PURCHASE):
        names = frappe.get_all(doctype, filters={{"company": co}}, pluck="name", limit_page_length=0)
        for name in names:
            job = frappe.db.get_value(doctype, name, "ledger_job")
            if job:
                frappe.db.delete("Pearnly CC Job", {{"name": job}})
            for child in frappe.get_meta(doctype).get_table_fields():
                frappe.db.delete(child.options, {{"parent": name, "parenttype": doctype}})
            frappe.db.delete("ERP Stock Ledger Entry", {{"voucher_type": doctype, "voucher_no": name}}) \\
                if frappe.db.table_exists("ERP Stock Ledger Entry") else None
            frappe.db.delete(doctype, {{"name": name}})
        gone["documents"] += len(names)
    frappe.db.delete("ERP Express Choice", {{"company": co}})
    frappe.db.commit()
    return gone
'''

ERP_PREPARE = ERP_SETUP + f'''
gone = cleanup()
co = company_of()
if not co:
    co = companies.create_company(company_name={SELLER!r}, tax_id={SELLER_TAX!r}, branch="00000")["name"]
    frappe.db.commit()
companies.update_company(co, tax_id={SELLER_TAX!r}, branch="00000")
books.replace(DEVICE, [{{"path": "C:\\\\PearnlyExpress\\\\PARTYONCE", "company": {SELLER!r}, "tax_id": {SELLER_TAX!r}, "branch": "00000",
                        "writable": True, "period_start": "2026-01-01", "period_end": "2026-12-31", "uid": "PARTYONCE"}}],
              frappe.utils.now_datetime())
frappe.db.commit()
if not push.linked(co):
    companies.update_company(co, express_enabled=1)
    frappe.db.commit()
picked = target.pick(push._company(co), "2026-10-15")
key = picked["book"]["key"]
data = {{"version": 2, "customers": {CUSTOMERS!r}, "suppliers": [], "departments": [], "products": [],
        "facts": {{"accounts": [{{"code": c, "name": c, "type": c[0]}} for c in ("11-31", "11-61", "21-01", "21-61", "41-01", "53-01")],
                  "mapping": {{}}, "catalog": {{"isacc_candidates": []}}}}}}
facts.save(key, data)
masters.apply(co, "PARTYONCE", {{"customers": {CUSTOMERS!r}, "suppliers": [], "products": [], "stock": [], "warehouses": []}})
frappe.db.commit()
companies.set_active_company(co)
frappe.db.commit()
print(json.dumps({{"gone": gone, "company": co, "title": {SELLER!r}, "book": key, "linked": bool(push.linked(co)),
                  "choices": frappe.db.count("ERP Express Choice", {{"company": co, "kind": "party"}})}}))
'''


def prepare(lab, product, viewport, reuse=False):
    site, user = SITES[product], USERS[product]
    language = LINES[viewport][0]
    lab.python(site, f'frappe.db.set_value("User", {user!r}, "language", {language!r})\nfrappe.db.commit()\nprint("{{}}")')
    if reuse:
        return reset_for_reuse(lab, product)
    out = lab.python(site, COWORK_PREPARE if product == "cowork" else ERP_PREPARE)
    if out["choices"]:
        raise RuntimeError(f"{product}: the buyer already has a remembered customer: {out}")
    return out


def reset_for_reuse(lab, product):
    """手机那遍复用电脑那遍识别好的单（--reuse）：撤掉交单（推送记录 / 写账件）、记住的选择、代录任务与会话，单据回到等着录。"""
    if product == "cowork":
        body = COWORK_SETUP + f'''
c = frappe.db.get_value("Cowork ERP Connection", {{"label": {COWORK_LABEL!r}}})
company = frappe.db.get_value("Cowork Company", {{"seed_connection": c}})
for task in frappe.get_all(tasks.DOCTYPE, filters={{"company": company}}, pluck="name", limit_page_length=0):
    frappe.db.delete("Pearnly Assistant Session", {{"operator_task": task}})
    frappe.db.delete(tasks.DOCUMENT, {{"parent": task}})
    frappe.db.delete(tasks.DOCTYPE, {{"name": task}})
records = frappe.get_all("Cowork Push Record", filters={{"connection": c}}, pluck="name", limit_page_length=0)
if records:
    frappe.db.delete("Cowork Push Writer Lock", {{"holder": ["in", records]}})
for dt in ("Cowork Push Attempt", "Cowork Push Record", "Cowork Express Choice", "Cowork Express Choice Change", "Cowork Companion Delivery"):
    frappe.db.delete(dt, {{"connection": c}})
hist = frappe.get_all("Cowork OCR History", filters={{"push_connection": c}}, pluck="name", limit_page_length=0)
for name in hist:
    frappe.db.set_value("Cowork OCR History", name, {{"last_push_status": "", "last_push_reason": "", "last_push_record": ""}},
                        update_modified=False)
frappe.db.commit()
print(json.dumps({{"company": company, "title": frappe.db.get_value("Cowork Company", company, "title"), "documents": len(hist),
                  "connection": c, "choices": frappe.db.count("Cowork Express Choice", {{"connection": c, "kind": "party"}})}}))
'''
    else:
        body = ERP_SETUP + '''
co = company_of()
for task in frappe.get_all(tasks.DOCTYPE, filters={"company": co}, pluck="name", limit_page_length=0):
    frappe.db.delete("Pearnly Assistant Session", {"operator_task": task})
    frappe.db.delete(tasks.DOCUMENT, {"parent": task})
    frappe.db.delete(tasks.DOCTYPE, {"name": task})
frappe.db.delete("ERP Express Choice", {"company": co})
frappe.db.commit()
print(json.dumps({"company": co, "title": frappe.db.get_value("ERP Company", co, "company_name"),
                  "choices": frappe.db.count("ERP Express Choice", {"company": co, "kind": "party"})}))
'''
    return lab.python(SITES[product], body)


# ───────────────────────────── 页面步骤（无头 Chrome，`scripts/regression/ui_steps.mjs`）─────────────────────────────

def _texts_js():
    """页面上看得到的回话、任务行、提问卡（每步都带回，断言用）。"""
    return ("const replies=()=>qa('[data-role=\"assistant\"]').map(e=>e.innerText.trim()).filter(Boolean); "
            "const rowsText=()=>qa('[data-slot=\"cc-op-row\"]').map(e=>e.innerText.trim()); "
            "const chooseCards=()=>qa('[data-slot=\"cc-op-reason\"], [data-slot=\"cc-op-detail\"]').length; ")


def _attach_js(files):
    """把本地文件（[(文件名, 类型, base64)]）一次放进输入框（截下文件选择框，同用户点回形针选文件）。"""
    return f"""
if(!window.__fileHook){{window.__fileHook=1; const orig=HTMLInputElement.prototype.click;
  HTMLInputElement.prototype.click=function(){{ if(this.type==='file'&&window.__nextFiles){{const dt=new DataTransfer();
    for(const f of window.__nextFiles) dt.items.add(f); window.__nextFiles=null; this.files=dt.files;
    this.dispatchEvent(new Event('change',{{bubbles:true}})); return;}} return orig.apply(this,arguments);}};}}
window.__nextFiles={json.dumps(files)}.map(([n,t,b])=>{{const raw=atob(b); const a=new Uint8Array(raw.length);
  for(let k=0;k<raw.length;k++) a[k]=raw.charCodeAt(k); return new File([a], n, {{type:t}});}});
const add=qa('button.cc-attach').find(seen); if(!add) return {{error:'no attach button'}}; add.click();
"""


# 对话里弹出的提问卡（ask_user，直写）：选 Big C 总部那个客户（名字里没有 -00 的那个）、别的题照预选，一题题点下一题
ANSWER_ASK = f"""
const box=qa('[data-slot="cc-askq"][data-phase="ask"]').pop(); if(!box) return {{asked:false}};
const picked=[];
for(let k=0;k<30;k++){{
  const opts=[...box.querySelectorAll('[data-slot="cc-askq-option"]')];
  const mine=opts.find(o=>o.innerText.includes({json.dumps(CUSTOMERS[0]['name'])})&&!o.innerText.includes('-00'))||opts.find(o=>o.getAttribute('aria-checked')==='true')||opts[0];
  if(mine&&mine.getAttribute('aria-checked')!=='true') mine.click(); await sleep(200);
  picked.push((box.querySelector('[data-slot="cc-askq-question"]')?.innerText.trim()||'')+' => '+(mine?.innerText.trim()||''));
  const b=box.querySelector('[data-slot="cc-askq-next"]'); if(!b||b.disabled) return {{asked:true, error:'cannot answer', picked}};
  const last=!box.querySelector('[data-slot="cc-askq-pager"]')||box.querySelector('.cc-askq-nth')?.innerText.split('/').every((x,i,a)=>a[0]===a[1]);
  b.click(); await sleep(400); if(last) break;
}}
return {{asked:true, picked}};
"""


def direct_steps(params, viewport):
    """Cowork 先在直写里放前 4 页的截图、说一句（同岳父 11-08 逐张截图直写）：CC 识别、要选客户时弹提问卡（脚本选 Big C 总部）、
    推送。实验栈没有 CC 桌面版：推出去停在排队，`run_one` 随后用合成回执记成写成（带单号）。"""
    files = [(path.name, "image/jpeg", base64.b64encode(path.read_bytes()).decode()) for path in SHOTS]
    idle = "!q('[data-slot=\"cc-run-indicator\"]')"
    return [
        {"name": "直写放截图说一句", "file": "0-直写截图后CC回话", "shot": True, "js": JS + _texts_js() + "await toMode('chat'); "
         "await until(()=>q('[data-slot=\"cc-chin-company\"]')); "
         f"const picked=await pickCompany({json.dumps(params['title'])}); " + _attach_js(files)
         + "const shown=await until(()=>document.body.innerText.includes('party-once-screenshot-04'),80); "
         "const ready=await until(()=>{const b=q('button.aui-composer-send'); return b&&!b.disabled;},240); "
         f"const sent=await say({json.dumps(DIRECT_LINES[viewport])}); "
         f"const got=await until(()=>replies().length>0&&({idle}||q('[data-slot=\"cc-askq\"][data-phase=\"ask\"]')),220); "
         "return {picked, shown, ready, sent, got, replies: replies()};"},
        # 识别 4 张、要选客户、推送：一步一分钟内，有提问卡就答，没有就等这一轮结束
        *[{"name": f"直写答题{k}", "js": JS + _texts_js() + ANSWER_ASK.replace("return {asked:false};",
           f"{{ const done=await until(()=>{idle}&&!q('[data-slot=\"cc-askq\"][data-phase=\"ask\"]'),200); "
           "return {asked:false, done, replies: replies().slice(-3)}; }")} for k in range(1, 7)],
        {"name": "直写结束", "file": "0b-直写推送后", "shot": True, "js": JS + _texts_js()
         + f"const done=await until(()=>{idle},200); return {{done, replies: replies()}};"},
    ]


def steps(params, viewport, reuse=False):
    title, (_, line) = params["title"], LINES[viewport]
    pdf = "" if reuse else base64.b64encode(PDF.read_bytes()).decode()
    attach = "" if reuse else f"""
if(!window.__fileHook){{window.__fileHook=1; const orig=HTMLInputElement.prototype.click;
  HTMLInputElement.prototype.click=function(){{ if(this.type==='file'&&window.__nextFile){{const dt=new DataTransfer(); dt.items.add(window.__nextFile);
    window.__nextFile=null; this.files=dt.files; this.dispatchEvent(new Event('change',{{bubbles:true}})); return;}} return orig.apply(this,arguments);}};}}
const raw=atob({json.dumps(pdf)}); const bytes=new Uint8Array(raw.length); for(let k=0;k<raw.length;k++) bytes[k]=raw.charCodeAt(k);
window.__nextFile=new File([bytes], 'party-once-24-sales.pdf', {{type:'application/pdf'}});
const add=qa('button.cc-attach').find(seen); if(!add) return {{error:'no attach button'}}; add.click();
const shown=await until(()=>document.body.innerText.includes('party-once-24-sales'),80);
const ready=await until(()=>{{const b=q('button.aui-composer-send'); return b&&!b.disabled;}},240);
"""
    wait_card = (_texts_js() + "const card=()=>q('[data-slot=\"cc-op-before-ask\"] [data-slot=\"cc-askq-question\"]')||"
                 "q('[data-slot=\"cc-op-before\"] [data-slot=\"cc-op-start\"]'); const got=await until(card,200); "
                 "const upload=qa('[data-slot=\"cc-op-upload\"]').map(e=>e.innerText.trim()).join(' | '); "
                 "return {got, ask: !!q('[data-slot=\"cc-op-before-ask\"]'), upload, replies: replies()};")
    return [
        {"name": "切到代录选公司", "js": JS + "const switched=await toMode('operator'); await until(()=>q('[data-slot=\"cc-chin-company\"]')); "
         f"const picked=await pickCompany({json.dumps(title)}); "
         "return {switched, picked, mode: mode(), period: q('[data-slot=\"cc-period-chip\"]')?.innerText.trim()||''};"},
        {"name": "放PDF说一句", "file": "1-放PDF后CC回话", "shot": True, "js": JS + _texts_js() + (attach or "const shown=true, ready=true; ")
         + f"const sent=await say({json.dumps(line)}); if(!sent) return {{error:'could not send', shown, ready}}; "
         "const got=await until(()=>replies().length>0&&!q('[data-slot=\"cc-run-indicator\"]'),220); "
         "return {shown, ready, sent, got, replies: replies()};"},
        # 识别 24 页、开始卡出来要几分钟：分几步等（每步一次页面求值不超过一分钟）
        *[{"name": f"等开始卡{k}", "js": JS + "if(q('[data-slot=\"cc-op-before-ask\"] [data-slot=\"cc-askq-question\"]')||"
           "q('[data-slot=\"cc-op-before\"] [data-slot=\"cc-op-start\"]')) return {got:true, ask: !!q('[data-slot=\"cc-op-before-ask\"]')}; "
           + wait_card} for k in range(1, 13)],
        {"name": "开始卡问题", "file": "2-开始卡里的问题", "shot": True, "js": JS + _texts_js() + """
for(let k=0;k<5;k++){const more=q('[data-slot="cc-op-before"] .cc-show-more'); if(!more) break; more.click(); await sleep(500);}
const written=qa('[data-slot="cc-op-before"] [data-slot="cc-op-written"]').map(e=>e.innerText.trim());
const box=q('[data-slot="cc-op-before-ask"]'); if(!box) return {total:0, questions:[], written, start: !!q('[data-slot="cc-op-start"]'), replies: replies()};
const nth=box.querySelector('.cc-askq-nth')?.innerText.trim()||'1/1'; const total=parseInt(nth.split('/').pop())||1;
const questions=[]; const next=()=>box.querySelector('[data-slot="cc-askq-pager"] button:last-child');
for(let k=0;k<total;k++){ questions.push({question: box.querySelector('[data-slot="cc-askq-question"]')?.innerText.trim()||'',
  options: [...box.querySelectorAll('[data-slot="cc-askq-option"]')].map(o=>o.innerText.trim())});
  if(k<total-1){ next()?.click(); await sleep(250);} }
for(let k=0;k<total-1;k++){ box.querySelector('[data-slot="cc-askq-pager"] button')?.click(); await sleep(150);}
await sleep(300); return {total, questions, written};"""},
        {"name": "答题开始", "js": JS + _texts_js() + f"""
const box=q('[data-slot="cc-op-before-ask"]');
if(!box){{  // 这家的客户已在直写里选过、记住了：开始卡没有要问的，直接点开始
  const b=q('[data-slot="cc-op-before"] [data-slot="cc-op-start"]'); if(!b) return {{error:'no question card and no start'}}; b.click();
  const settled=await until(()=>q('[data-slot="cc-op-before"] [data-slot="cc-ask-answer"]')||q('[data-slot="cc-ask-failure"]'),200);
  return {{picked:[], settled, answer: q('[data-slot="cc-op-before"] [data-slot="cc-ask-answer"]')?.innerText.trim()||'',
          failure: q('[data-slot="cc-ask-failure"]')?.innerText.trim()||''}};
}}
const picked=[];
for(let k=0;k<30;k++){{
  const opts=[...box.querySelectorAll('[data-slot="cc-askq-option"]')];
  const mine=opts.find(o=>o.innerText.includes({json.dumps(CUSTOMERS[0]['name'])})&&!o.innerText.includes('-00'))||opts.find(o=>o.getAttribute('aria-checked')==='true')||opts[0];
  if(mine&&mine.getAttribute('aria-checked')!=='true') mine.click(); await sleep(200);
  picked.push((box.querySelector('[data-slot="cc-askq-question"]')?.innerText.trim()||'')+' => '+(mine?.innerText.trim()||''));
  const b=box.querySelector('[data-slot="cc-askq-next"]'); if(!b||b.disabled) return {{error:'cannot answer', picked}};
  const last=!box.querySelector('[data-slot="cc-askq-pager"]')||box.querySelector('.cc-askq-nth')?.innerText.split('/').every((x,i,a)=>a[0]===a[1]);
  b.click(); await sleep(400); if(last) break;
}}
const settled=await until(()=>q('[data-slot="cc-op-before-ask"] [data-slot="cc-ask-answer"]')||q('[data-slot="cc-ask-failure"]'),200);
return {{picked, settled, answer: q('[data-slot="cc-op-before-ask"] [data-slot="cc-ask-answer"]')?.innerText.trim()||'',
        failure: q('[data-slot="cc-ask-failure"]')?.innerText.trim()||''}};"""},
        *[{"name": f"等交单{k}", "js": JS + _texts_js() + f"""
const enough=()=>{{ for(const b of qa('.cc-steps-summary[aria-expanded="false"]')) b.click(); return rowsText().length>={PAGES}; }};
const got=await until(enough,200); return {{got, rows: rowsText().length}};"""} for k in range(1, 5)],
        {"name": "交单后任务卡", "file": "3-交单后任务卡", "shot": True, "full": True, "js": JS + _texts_js() + """
for(let k=0;k<10;k++){ const b=qa('.cc-steps-summary[aria-expanded="false"]')[0]; if(!b) break; b.click(); await sleep(200);}
await sleep(1500);
return {rows: rowsText(), choose: chooseCards(), asks: qa('[data-slot="cc-askq"]').length + qa('[data-slot="cc-op-before-ask"]').length,
        replies: replies(), summaries: qa('.cc-steps-summary').map(e=>e.innerText.trim()), page: document.body.innerText.slice(0, 20000)};"""},
    ]


# ───────────────────────────── 断言 ─────────────────────────────

def leaks(text):
    return sorted({m.group(0) for pattern in LEAKS for m in pattern.finditer(text or "")})


def internal_ids(text):
    return sorted({m.group(0) for pattern in INTERNAL_IDS for m in pattern.finditer(text or "")})


def judge(product, params, results):
    by = {r.get("step"): r.get("value") or {} for r in results}
    said, card, answered, done = (by.get(k) or {} for k in ("放PDF说一句", "开始卡问题", "答题开始", "交单后任务卡"))
    questions = card.get("questions") or []
    customer = [q for q in questions if any(w in q["question"] for w in CUSTOMER_TITLES)]
    direct = params.get("direct") or {}
    pushed = direct.get("pushed") or []
    written = card.get("written") or []
    other = [q for q in questions if any(q["question"].startswith(w) for w in OTHER_TITLES)]
    rows = done.get("rows") or []
    replies = "\n".join(done.get("replies") or said.get("replies") or [])
    shape = re.compile(r"^IV69/\d{5} · .+")
    return [
        {"case": "① CC 的回话里没有内部词（operator、wrong_mode、CWH-、哈希）", "seen": {"replies": (done.get("replies") or said.get("replies") or [])[:6],
         "leaks": leaks(replies)}, "ok": bool(said.get("got")) and not leaks(replies)},
        {"case": "② 开始卡里「客户」这一题只出现一次（Cowork 在直写里已选过、记住了：不再问），没有标题是「其他」的题",
         "seen": {"questions": questions, "direct": bool(direct)}, "ok": len(customer) == (0 if direct else 1) and not other},
        *([{"case": "⑥ 直写里 4 张截图识别、选了客户、推出去（同岳父逐张截图直写）", "seen": {"pushed": pushed},
            "ok": len(pushed) == len(SHOTS)},
           {"case": "⑦ 代录开始卡标出直写已写进 Express 的 4 张（已入账 · 单号），默认不交", "seen": {"written": written},
            "ok": len(written) == len(SHOTS) and all(DIRECT_DOCNUM.format(i) in " ".join(written) for i in range(1, len(SHOTS) + 1))}]
          if direct else []),
        {"case": "③ 答完开始：卡片定下、没有报错；页面上没有逐张提问卡", "seen": {**answered, "choose_cards": done.get("choose"),
         "question_cards": done.get("asks")}, "ok": bool(answered.get("settled")) and not answered.get("failure") and not done.get("choose")},
        {"case": f"④ 任务行 {PAGES} 行，每行「单号 · 对方」，没有内部编号", "seen": {"rows": rows[:30], "ids": internal_ids("\n".join(rows))},
         "ok": len(rows) == PAGES and all(shape.match(r.split("\n")[0]) for r in rows) and not internal_ids("\n".join(rows))},
        {"case": "页面上任何地方都不出现内部编号与内部词", "seen": {"ids": internal_ids(done.get("page") or ""), "leaks": leaks(done.get("page") or "")},
         "ok": not internal_ids(done.get("page") or "") and not leaks(done.get("page") or "")},
    ]


def verify(lab, product, params):
    site = SITES[product]
    common = f'''
from pearnly_core.assistant import engine
from pearnly_core.express_operator import tasks
frappe.set_user({USERS[product]!r})
task = frappe.get_all(tasks.DOCTYPE, filters={{"company": {params["company"]!r}}}, fields=["name", "status", "session_id"],
                      order_by="creation desc", limit_page_length=1)
task = task[0] if task else {{}}
rows = frappe.get_all(tasks.DOCUMENT, filters={{"parent": task.get("name") or "-"}}, fields=["doc_id", "state", "reason", "ref", "party"],
                      limit_page_length=0)
session = frappe.db.get_value("Pearnly Assistant Session", {{"operator_task": task.get("name") or "-"}}, ["session_id", "remote_ids"],
                              as_dict=True) or {{}}
sid = session.get("session_id") or ""
remote = ([r for r in json.loads(session.get("remote_ids") or "[]") if r] or [""])[-1]  # 引擎那边的会话（本站编号不是它）
tools_on, tools_error = [], ""
try:
    found = engine.adapter().api.beta.sessions.retrieve(remote)
    tools_on = sorted({{getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None) or getattr(t, "type", "")
                       for t in (found.agent.tools or [])}})
except Exception as error:
    tools_error = str(error)[:300]
'''
    if product == "cowork":
        body = common + f'''
c = {params["connection"]!r}
choices = frappe.get_all("Cowork Express Choice", filters={{"connection": c, "kind": "party"}}, fields=["subject", "value"], limit_page_length=10)
hist = frappe.get_all("Cowork OCR History", filters={{"push_connection": c}}, fields=["name", "last_push_status", "last_push_reason"],
                      limit_page_length=0)
print(json.dumps({{"task": task, "rows": rows, "sid": sid, "tools": tools_on, "tools_error": tools_error, "choices": choices,
                  "documents": [dict(h) for h in hist]}}, default=str))
'''
    else:
        body = common + f'''
co = {params["company"]!r}
from pearnly_erp_portal.express import push
choices = frappe.get_all("ERP Express Choice", filters={{"company": co, "kind": "party"}}, fields=["subject", "value"], limit_page_length=10)
docs = frappe.get_all(push.SALES, filters={{"company": co}}, fields=["name", "doc_no", "status", "ledger_state", "ledger_reason"], limit_page_length=0) \\
    if "doc_no" in [f.fieldname for f in frappe.get_meta(push.SALES).fields] else \\
    frappe.get_all(push.SALES, filters={{"company": co}}, fields=["name", "status", "ledger_state", "ledger_reason"], limit_page_length=0)
print(json.dumps({{"task": task, "rows": rows, "sid": sid, "tools": tools_on, "tools_error": tools_error, "choices": choices,
                  "documents": [dict(d) for d in docs]}}, default=str))
'''
    found = lab.python(site, body)
    rows = found["rows"]
    held = [r for r in rows if "choice" in str(r.get("reason") or "") or r.get("state") in ("choose", "held", "attention")]
    sent = [r for r in rows if r.get("state") in ("queued", "sent", "running", "saved")]
    skipped = [r for r in rows if r.get("state") == "skipped"]
    want_skipped = len(SHOTS) if (params.get("direct") or {}).get("pushed") else 0
    return [
        {"case": "① 代录会话的远端工具里没有直写推送（cowork_push_documents / erp_push_documents）",
         "saved": {"sid": found["sid"], "tools": found["tools"], "error": found["tools_error"]},
         "ok": bool(found["tools"]) and not any(t in found["tools"] for t in PUSH_TOOLS)},
        {"case": f"③ 任务里 {PAGES} 张都交出去（排队；直写已写进 Express 的跳过），没有一张停在要选客户",
         "saved": {"task": found["task"], "states": sorted({r.get('state') for r in rows}), "skipped": len(skipped),
                   "held": held[:3], "documents": len(found["documents"]), "sample": found["documents"][:3]},
         "ok": len(rows) == PAGES and len(sent) == PAGES - want_skipped and len(skipped) == want_skipped and not held},
        {"case": "④ 站点任务行都有单号与对方", "saved": {"missing": [r for r in rows if not r.get("ref") or not r.get("party")][:3]},
         "ok": len(rows) == PAGES and all(r.get("ref") and r.get("party") for r in rows)},
        {"case": f"⑤ 站点记住了这个买方的对应（选的是 {PICK}）", "saved": found["choices"],
         "ok": any(ch.get("value") == PICK and BUYER_TAX in str(ch.get("subject") or "") for ch in found["choices"])},
    ]


# ───────────────────────────── 运行 ─────────────────────────────

def drive(site, sid, steps_, mobile, shots, timeout=1800):
    job = {"base": f"http://{site}:{RUN.PORT}", "cookie_domain": site, "sid": sid, "start": "/assistant", "mobile": mobile,
           "width": 375, "video": "", "steps": steps_, "ready": "document.readyState === 'complete' && !!document.querySelector('[data-mode=\"operator\"]')",
           **({"shots": shots} if shots else {})}
    env = dict(os.environ, SMOKE_CHROME_RULES=f"MAP {site} 127.0.0.1")
    holder = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)  # 带整份 PDF 与截图，超过命令行长度上限：写文件交 @路径
    holder.write(json.dumps(job))
    holder.close()
    proc = subprocess.Popen(["node", str(ROOT / "scripts/regression/ui_steps.mjs"), "@" + holder.name], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, env=env, start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise RuntimeError(f"page steps timed out after {timeout}s")
    finally:
        os.unlink(holder.name)
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


def engine_ready(lab, product):
    site = SITES[product]
    return lab.python(site, '''
import os
key = str(frappe.conf.get("pearnly_assistant_api_key") or "")
print(json.dumps({"real_key": bool(key) and not key.startswith("sk-ant-regression-lab-"),
                  "base_url": os.environ.get("ANTHROPIC_BASE_URL", ""), "ocr_key": bool(os.environ.get("PEARNLY_ANTHROPIC_API_KEY", "").strip()),
                  "ocr_engine": frappe.conf.get("pearnly_ocr_engine") or ""}))
''')


def mark_direct_written(lab) -> dict:
    """直写推出去的那几张（实验栈没有 CC 桌面版，停在排队）用合成回执记成写成、带单号（同岳父那 4 张已写进真账）。"""
    return lab.python(SITES["cowork"], COWORK_SETUP + f'''
c = frappe.db.get_value("Cowork ERP Connection", {{"label": {COWORK_LABEL!r}}})
rows = frappe.get_all("Cowork OCR History", filters={{"push_connection": c, "last_push_status": ["in", ["pending", "success"]]}},
                      fields=["name", "invoice_no", "last_push_record", "last_push_status"], order_by="invoice_no asc", limit_page_length=20)
for i, row in enumerate(rows, start=1):
    frappe.db.set_value("Cowork Push Record", row.last_push_record, {{"status": "success", "external_docnum": {DIRECT_DOCNUM!r}.format(i)}},
                        update_modified=False)
    frappe.db.set_value("Cowork OCR History", row.name, "last_push_status", "success", update_modified=False)
frappe.db.commit()
print(json.dumps({{"pushed": [dict(r) for r in rows]}}, default=str))
''')


def estimate(product, viewport, reuse) -> float:
    """这一遍按最坏单价估多少美元（硬封顶用）：识别页数 × PAGE_MAX + 对话轮数 × TURN_MAX。"""
    pages = 0 if reuse else PAGES + (len(SHOTS) if product == "cowork" else 0)
    turns = 2 + (4 if product == "cowork" and not reuse else 0)  # 代录一句 + 答题后；直写那一句、要选客户来回几轮
    return round(pages * PAGE_MAX + turns * TURN_MAX, 2)


def run_one(lab, product, viewport, shots, reuse):
    ready = engine_ready(lab, product)
    if not (ready["real_key"] and "api.anthropic.com" in ready["base_url"] and ready["ocr_key"] and ready["ocr_engine"] == "claude"):
        raise SystemExit(f"{product}: 实验栈没接真引擎 / 真识别（{ready}），先跑 --engine-on")
    started = time.monotonic()
    params = prepare(lab, product, viewport, reuse)
    site, user = SITES[product], USERS[product]
    sid = lab.session(site, user)
    folder = ""
    if shots:
        folder = os.path.join(shots, f"{product}-{viewport}")
        os.makedirs(folder, exist_ok=True)
    direct = product == "cowork" and not reuse
    try:
        before = drive(site, sid, direct_steps(params, viewport), viewport == "mobile", folder) if direct else []
        params["direct"] = mark_direct_written(lab) if direct else {}
        results = before + drive(site, sid, steps(params, viewport, reuse), viewport == "mobile", folder)
    finally:
        lab.drop(site, user, sid)
    if folder:  # 截图文件名带产品、宽度、步骤
        for shot in Path(folder).glob("*.png"):
            shot.rename(Path(shots) / f"{product}-{viewport}-{shot.name}")
        os.rmdir(folder)
    page_errors = [e for r in results for e in (r.get("page_errors") or []) + ([r["error"]] if r.get("error") else [])
                   + ([f"{r.get('step')}: {r['value']['error']}"] if isinstance(r.get("value"), dict) and r["value"].get("error") else [])]
    checks = judge(product, params, results) + verify(lab, product, params)
    checks.append({"case": "页面没有报错、步骤没有出错", "seen": page_errors, "ok": not page_errors})
    return {"product": product, "viewport": viewport, "reuse": reuse, "ok": all(c["ok"] for c in checks),
            "seconds": round(time.monotonic() - started), "company": params.get("company"), "checks": checks,
            "steps": [{"step": r.get("step"), "value": r.get("value")} for r in results]}


# ───────────────────────────── 接 / 断真引擎（只在实验栈）─────────────────────────────

OVERRIDE = """services:
  bench:
    env_file: [{env}]
    environment:
      ANTHROPIC_BASE_URL: https://api.anthropic.com
      FRAPPE_BACKGROUND_WORKERS_NOFORK: "1"
      PEARNLY_OCR_THREADS: "1"
    command: [bash, -lc, "for p in 8000 8001 8002; do bench serve --port $$p --noreload & done; bench worker --queue short,default,long & (cd sites && ../env/bin/python -m pearnly_core.execution.worker --queue ocr --num-workers 1) & (cd sites && ../env/bin/python -m pearnly_core.execution.dispatch) & wait"]
"""


def compose(src, *extra, env=None):
    files = ["-f", str(Path(src) / "infrastructure/compose/regression-lab.yml"), *extra]
    return subprocess.run(["docker", "compose", "-p", RUN.LAB, *files, "up", "-d", "--wait", "bench"], cwd=src, env=env,
                          capture_output=True, text=True, timeout=900)


def lab_env():
    # 实验栈建时的端口（REGRESSION_LAB_PORT / _COWORK / _DMS）原样带上，重起不换口
    env = dict(os.environ)
    env.setdefault("REGRESSION_LAB_PORT", str(RUN.PORT))
    for suffix, offset in (("_COWORK", 1), ("_DMS", 2)):
        env.setdefault("REGRESSION_LAB_PORT" + suffix, str(RUN.PORT + offset))
    return env


# Cowork 识别准入（`ocr.execution.settings.snapshot`）要写明识别配置与一套 Vertex 连接（合成值，只过准入；真读票按
# ``pearnly_ocr_engine=claude`` 交 Claude，同 `scripts/cowork-execution-lab-verify.py`）。实验栈默认都没有
COWORK_OCR = (("pearnly_cowork_ocr_environment", '{"OCR_LLM_BACKEND": "vertex"}'), ("pearnly_cowork_ocr_policy", '{"mode": "economy"}'),
              ("pearnly_ocr_credentials", '"/synthetic/credential-reference.json"'), ("pearnly_ocr_backend", '"vertex"'),
              ("pearnly_ocr_project", '"synthetic-lab"'), ("pearnly_ocr_location", '"asia-southeast1"'))


def set_sites(lab, key, engine):
    site = SITES["cowork"]  # Cowork 的识别要站点上写明识别配置（`ocr.execution.settings.snapshot`），实验栈默认没有
    for name, value in COWORK_OCR:
        lab.bench(*(["bench", "--site", site, "set-config", "--parse", name, value] if engine else
                    ["bench", "--site", site, "remove-from-config", name]), timeout=300)
    for site in SITES.values():
        args = ["bench", "--site", site, "set-config", "pearnly_assistant_api_key", key]
        result = lab.bench(*args, timeout=300)
        if result.returncode:
            raise SystemExit(f"{site}: set-config failed")
        if engine:
            lab.bench("bench", "--site", site, "set-config", "pearnly_ocr_engine", engine, timeout=300)
        else:
            lab.bench("bench", "--site", site, "remove-from-config", "pearnly_ocr_engine", timeout=300)


def engine_on(lab, src, key_file):
    key = Path(key_file).read_text().strip()
    if not key.startswith("sk-ant-"):
        raise SystemExit("密钥文件内容不像 Anthropic 密钥")
    with tempfile.TemporaryDirectory() as folder:
        env_file = Path(folder) / "real.env"
        env_file.write_text(f"PEARNLY_ANTHROPIC_API_KEY={key}\n")
        env_file.chmod(0o600)
        override = Path(folder) / "real.override.yml"
        override.write_text(OVERRIDE.format(env=env_file))
        result = compose(src, "-f", str(override), env=lab_env())
        if result.returncode:
            raise SystemExit(f"重起 bench 失败：{result.stderr[-600:]}")
    set_sites(lab, key, "claude")
    RUN.Lab.restart(lab)
    print("真引擎已接上（站点密钥、识别引擎 claude、识别进程）")


def engine_off(lab, src):
    set_sites(lab, FAKE_KEY, "")
    result = compose(src, env=lab_env())
    if result.returncode:
        raise SystemExit(f"重起 bench 失败：{result.stderr[-600:]}")
    print("已回到假智能体（站点密钥换回假值、识别引擎配置去掉、bench 不带覆盖文件）")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--product", choices=["cowork", "erp", "all"], default="all")
    parser.add_argument("--viewport", choices=["desktop", "mobile", "all"], default="all")
    parser.add_argument("--reuse", action="store_true", help="手机那遍不放 PDF，用电脑那遍识别好的单（不再付识别费）")
    parser.add_argument("--shots", default="", help="截图目录（文件名带产品、宽度、步骤）")
    parser.add_argument("--out", default="")
    parser.add_argument("--engine-on", metavar="密钥文件")
    parser.add_argument("--engine-off", action="store_true")
    parser.add_argument("--src", default="", help="实验栈挂的源码工作树（--engine-on / --engine-off 用它的 compose 文件重起 bench）")
    parser.add_argument("--budget", type=float, default=2.0, help="这次场景最多花多少美元（按最坏单价估，超了不开下一遍）")
    parser.add_argument("--spent", default="", help="记累计估价的文件（几次运行共用一个上限；不给就只算这一次）")
    args = parser.parse_args(argv)
    lab = RUN.Lab()
    if args.engine_on or args.engine_off:
        if not args.src:
            parser.error("--engine-on / --engine-off 要 --src")
        engine_on(lab, args.src, args.engine_on) if args.engine_on else engine_off(lab, args.src)
        return 0
    products = ["cowork", "erp"] if args.product == "all" else [args.product]
    viewports = ["desktop", "mobile"] if args.viewport == "all" else [args.viewport]
    results = []
    for product in products:
        for viewport in viewports:
            reuse = args.reuse and viewport == "mobile" and "desktop" in viewports
            cost = estimate(product, viewport, reuse)
            spent = float(Path(args.spent).read_text() or 0) if args.spent and Path(args.spent).exists() else sum(
                r.get("estimate", 0) for r in results)
            if spent + cost > args.budget:
                print(f"停下：这一遍最多约 {cost} 美元，已估 {spent}，会超过上限 {args.budget}（{product} / {viewport} 没跑）", flush=True)
                results.append({"product": product, "viewport": viewport, "ok": False, "error": "over budget", "checks": []})
                break
            if args.spent:
                Path(args.spent).write_text(str(round(spent + cost, 2)))
            try:
                result = run_one(lab, product, viewport, args.shots, reuse)
            except SystemExit:
                raise
            except Exception as error:  # noqa: BLE001 - 一遍出错记不通过，其余照跑
                result = {"product": product, "viewport": viewport, "ok": False, "error": str(error)[:800], "checks": []}
            result["estimate"] = cost
            results.append(result)
            bad = [c for c in result["checks"] if not c["ok"]]
            print(f"{'通过' if result['ok'] else '不通过'} {product} / {viewport} {result.get('seconds', '')}s {result.get('error', '')} "
                  f"{json.dumps(bad, ensure_ascii=False)[:1500] if bad else ''}", flush=True)
    ok = all(r["ok"] for r in results)
    if args.out:
        Path(args.out).write_text(json.dumps({"ok": ok, "results": results}, ensure_ascii=False, indent=1, default=str))
    print("场景：" + ("通过" if ok else "不通过"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
