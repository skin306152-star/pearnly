"""流程用例：ERP 排队中的单钉在一台此刻不在线的电脑上（领走后那台失联、收回时钉回它，`cc_desktop.jobs._recover`），
单据页、列表、CC 对话都说出在等哪台：「等待电脑 · 名称」，状态词照旧「录入中」（11-25 两台电脑实测：同公司另一台在线时
writers 报 ok，页面只写「录入中」，看不出在等那台）。

实验栈里没有真的 CC 桌面版：按 CC 桌面版的样子登记一台电脑（`cc_desktop.devices.register`），开一张采购单（正式入账函数），
它的写账件照「领走后那台失联、收回时钉回它」的样子落库（排队中、``route_device`` = 那台、原因结果不明），那台最后报到推到在线时限之前；
单据的原因按每分钟那一轮同一个函数写（`push._queued_note` → `push._set`，实验栈不跑定时任务）。
页面：单据页点「Express」看原因栏、回列表看 Express 一栏的悬浮说明；CC 对话按推送结论工具同一个函数（`cc_tools._outcome`）核。
cleanup：撤写账件、移除那台电脑、作废单据。
"""
from __future__ import annotations

import uuid

from desk_list import ACTION, IDLE
from erp_lib import void_documents

TITLE = "ERP 排队中的单钉在一台不在线的电脑上：单据页、列表、CC 对话写「等待电脑 · 名称」，状态词仍是录入中（11-25）"
USER = "purchase@erp.regression"


def prepare(lab, manifest):
    company, tag = manifest["company"], uuid.uuid4().hex
    out = lab.python(manifest["site"], f"""
from datetime import timedelta
from pearnly_core.cc_desktop import contracts as cc, devices, jobs
from pearnly_erp_portal.express import contracts, push
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.purchasing import entry, suppliers
frappe.set_user({USER!r})
companies.set_active_company({company!r})
device = devices.register(device_key="flow-wc-{tag}", device_name="FLOW-PC-{tag[:6].upper()}")["device"]
supplier = frappe.db.get_value("ERP Supplier", {{"supplier_name": "ผู้ขายบริการทดสอบ"}}) or \\
    suppliers.create_supplier(supplier_name="ผู้ขายบริการทดสอบ", tax_id="0105536080112")["name"]
frappe.db.commit()
doc = entry.post({{"company": {company!r}, "supplier": supplier, "doc_date": frappe.utils.today(), "currency": "THB", "fx_rate": 1,
                  "has_vat": 1, "price_mode": "exclusive", "doc_kind": "purchase_invoice", "update_stock": 0, "is_paid": 0,
                  "doc_no": "FLOW-WC-{tag[:6]}",
                  "lines": [{{"item_type": "service", "description": "ค่าขนส่ง", "qty": 1, "unit": "", "unit_price": 300, "vat_rate": 7}}]}},
                 request_id="flow-waiting-computer-{tag}")
frappe.db.commit()
name = doc["name"]
row = frappe.db.get_value("Purchase Document", name, ["ledger_job", "ledger_book", "owner"], as_dict=True)
book = row.ledger_book or frappe.get_all("Pearnly CC Book", pluck="book_key", limit_page_length=1)[0]
job = row.ledger_job or jobs.enqueue(source_doctype="Purchase Document", source_name=name, request_key="flow-wc:" + name,
                                     actor=row.owner, book_key=book, payload={{"direction": "purchase", "doctype": "RR"}},
                                     label=name)["job"]
# 照领走后那台失联、收回时钉回它的样子（`jobs._recover`）：排队中、钉在那台、结果不明；那台最后报到在在线时限之前
frappe.db.set_value("Pearnly CC Job", job, {{"status": "queued", "route_device": device, "reason": jobs.LOST, "attempt": 1,
                    "lease_device": None, "lease_expires_at": None, "writing_key": None}}, update_modified=False)
frappe.db.set_value("Pearnly CC Device", device, "last_seen_at",
                    devices.now() - timedelta(seconds=cc.ONLINE_SECONDS + 60), update_modified=False)
frappe.db.set_value("Purchase Document", name, {{"ledger_state": contracts.QUEUED, "ledger_job": job, "ledger_book": book,
                    "ledger_reason": ""}}, update_modified=False)
frappe.db.commit()
# 每分钟那一轮同一个函数写原因（实验栈不跑定时任务）
current = frappe.db.get_value("Purchase Document", name, ["name", "owner", "ledger_state", "ledger_reason", "ledger_job",
                                                          "ledger_book", "ledger_target"], as_dict=True)
note = push._queued_note(push.PURCHASE, current)
push._set(push.PURCHASE, name, {{"ledger_reason": note}})
frappe.db.commit()
label = frappe.db.get_value("Pearnly CC Device", device, "device_label")
print(json.dumps({{"doc": name, "job": job, "device": device, "label": label, "note": note}}, default=str))
""")
    return {**out, "company": company}


def cleanup(lab, manifest, params):
    lab.python(manifest["site"], f"""
from frappe.utils import now_datetime
from pearnly_core.cc_desktop import books
frappe.db.set_value("Pearnly CC Job", {params['job']!r}, {{"status": "cancelled", "writing_key": None, "route_device": None,
                    "reason": "flow cleanup"}}, update_modified=False)
books.replace({params['device']!r}, [], now_datetime())
frappe.db.set_value("Pearnly CC Device", {params['device']!r}, {{"retired_at": now_datetime(), "token_digest": None}},
                    update_modified=False)
frappe.db.commit()
print("{{}}")
""")
    void_documents(lab, manifest["site"], [params["doc"]])


def steps(params, camera_code=None):
    return [
        {"name": "Express 弹窗", "js": IDLE + ACTION + "await idle();"
         "for(let k=0;k<80&&!document.querySelector('.page-head .page-title');k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "const via=await action('Express'); if(!via) return {button:false};"
         "for(let k=0;k<80&&!(window.cur_dialog&&cur_dialog.$wrapper.is(':visible'));k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "const d=cur_dialog; const v=n=>d&&d.fields_dict&&d.fields_dict[n]?String(d.get_value(n)||''):null;"
         "const out={button:true, state:v('state'), reason:v('reason'), want_reason:__('Waiting for computer')+' · '+"+repr(params['label'])+","
         "want_state:__('Entering')}; d.hide(); await idle(); return out;"},
        {"name": "列表悬浮", "js": IDLE + "await idle(); frappe.set_route('pearnly-purchase'); await idle();"
         f"const want={params['label']!r};"
         "let cell=null; for(let k=0;k<120&&!cell;k++){ await new Promise(r=>setTimeout(r,250));"
         "cell=[...document.querySelectorAll('span.ellipsis[title]')].find(s=>(s.getAttribute('title')||'').endsWith(' · '+want)); }"
         "return {title:cell?cell.getAttribute('title'):null, text:cell?cell.textContent.trim():null,"
         "want_title:__('Waiting for computer')+' · '+want, want_text:__('Entering')};"},
    ]


def judge(params, results, camera_code=None):
    seen = {r.get("step"): r.get("value") or {} for r in results}
    dialog, listed = seen.get("Express 弹窗") or {}, seen.get("列表悬浮") or {}
    return [
        {"case": "the document's Express dialog says which computer it waits for, the status word stays entering",
         "seen": dialog,
         "ok": dialog.get("button") is True and bool(dialog.get("want_reason"))
         and dialog.get("reason") == dialog.get("want_reason") and dialog.get("state") == dialog.get("want_state")},
        {"case": "the list's Express cell shows entering and its hover names the computer",
         "seen": listed, "ok": bool(listed.get("title")) and listed.get("title") == listed.get("want_title")
         and listed.get("text") == listed.get("want_text")},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
from pearnly_erp_portal.express import cc_tools
frappe.set_user({USER!r})
frappe.local.lang = "zh"
out = cc_tools._outcome(cc_tools.KINDS["purchase"], {params['doc']!r})
doc = frappe.db.get_value("Purchase Document", {params['doc']!r}, ["ledger_state", "ledger_reason"], as_dict=True)
print(json.dumps({{"outcome": out, "doc": doc}}, default=str))
""")
    out, doc = got.get("outcome") or {}, got.get("doc") or {}
    return [
        {"case": "the reason the minute round writes is the waiting computer with its connection-page name",
         "saved": {"note": params.get("note"), "doc": doc},
         "ok": doc.get("ledger_state") == "queued" and doc.get("ledger_reason") == f"express.waiting_computer:{params['label']}"},
        {"case": "the CC conversation's push outcome is queued and says the same sentence",
         "saved": out,
         "ok": out.get("status") == "queued" and out.get("waiting_computer") == params["label"]
         and out.get("note") == f"等待电脑 · {params['label']}" and out.get("cc_desktop_online") is False},
    ]
