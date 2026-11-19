"""流程用例：ERP 写账人与收款一个口径（用户 11-16「按建议」，`express.push.actor_of` → `office_actor`）——只用手机的销售员 B 开的
销售单，交给办公室那台只有 A 登录过 CC、够得到这本账套的电脑写；ERP 单据上仍记 B；单据页与代录对话不再说「未连接电脑」（B60 前叫「CC 桌面版未连接」）；
另一家公司的电脑（够不到这本账套）不领。

实验栈里没有真的 CC 桌面版：按 CC 桌面版的样子在站点里登记两台电脑（`cc_desktop.devices.register`，登录人分别是 A 与 C）并报账套
（`books.replace`：A 那台报这家公司的账套，C 那台只报另一家公司的），标成在线。B 开单（正式入账函数）后按正式写入路径排写账件
（`push.send`），再按电脑领单同一个排头函数（`jobs._heads`）看哪台领得到；最后照电脑写好的样子回执（`push.job_changed`）。
单据页的原因（`push._queued_note`，每分钟那一轮写到单据上）与代录对话那句（`operator.computer_state`）按产生它们的函数核——
实验栈不跑定时任务。没有页面步骤。cleanup：删主档种子、撤写账件、作废单据、移除两台电脑与它们的账套行。
"""
from __future__ import annotations

import uuid

from erp_lib import codes, company, void_documents, warehouses

TITLE = "ERP 写账人同收款：只用手机的销售员的单交给办公室电脑写、ERP 记原发单人、不再显示未连接；别家公司的电脑不领（11-16）"
A, B, C = "admin@erp.regression", "sales@erp.regression", "purchase@erp.regression"


def prepare(lab, manifest):
    co, tag = company(manifest), uuid.uuid4().hex
    product = codes(manifest)["by_express"]["product"]
    house = next(row["name"] for row in warehouses(manifest) if row.get("express_code"))
    return {**lab.python(manifest["site"], f"""
from frappe.utils import now_datetime
from pearnly_core.cc_desktop import books, devices
from pearnly_erp_portal.express import facts, push, target
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.sales import entry as sales

row = push._company({co!r})
key = target.pick(row, frappe.utils.today())["book"]["key"]
book = frappe.get_all("Pearnly CC Book", filters={{"book_key": key}}, fields=["path", "company", "tax_id", "branch", "period_start",
                                                                            "period_end", "uid"], limit_page_length=1)[0]
raw = {{"path": book.path, "company": book.company, "tax_id": book.tax_id, "branch": book.branch, "writable": True,
        "period_start": str(book.period_start), "period_end": str(book.period_end), "uid": book.uid}}
# 实验栈里这本账套的主档没落地（写账准备停在「主档读取中」）：放一份（同 `erp_cc_operator_intake`），cleanup 先删
seeded = facts.load(key) is None
if seeded:
    mapping = {{"fallback_acc": "51-03-00-00", "vat_input_acc": "11-06-01-00", "ap_acc": "21-01-01-00", "revenue_acc": "41-01-00-00",
               "vat_output_acc": "21-04-01-00", "ar_acc": "11-03-01-00"}}
    facts.save(key, {{"version": 2, "customers": [], "suppliers": [], "products": [{{"code": "EXP-0001", "name": "สินค้าจาก Express",
                     "kind": "stock", "unit": "ชิ้น", "prices": ["50"]}}], "warehouses": [{{"code": "01", "name": "คลัง 1"}}],
                     "settings": {{"mainloc": "01"}}, "stock": [], "departments": [],
                     "facts": {{"accounts": [{{"code": c, "name": c, "type": c[0]}} for c in mapping.values()],
                               "mapping": mapping, "catalog": {{"isacc_candidates": []}}}}}})
now = now_datetime()
try:
    frappe.set_user({A!r})
    office = devices.register(device_key="flow-oa-a-{tag}", device_name="flow office A")["device"]
    books.replace(office, [raw], now)
    frappe.set_user({C!r})
    other = devices.register(device_key="flow-oa-c-{tag}", device_name="flow other company")["device"]
    books.replace(other, [{{**raw, "path": "C:\\\\FlowOther\\\\{tag[:8]}", "tax_id": "0105556012345", "company": "flow other co",
                           "uid": "OTHER-{tag[:8]}"}}], now)
    for device in (office, other):
        frappe.db.set_value("Pearnly CC Device", device, "last_seen_at", now, update_modified=False)  # 在线
    frappe.db.commit()
    frappe.set_user({B!r})
    companies.set_active_company({co!r})
    made = sales.post({{"company": {co!r}, "doc_date": frappe.utils.today(), "has_vat": 1, "vat_rate": 7, "source": "manual",
                       "doc_kind": "receipt", "buyer_name": "flow office actor {tag[:6]}", "warehouse": {house!r}, "paid_amount": 107,
                       "payment_method": "cash", "payment_date": frappe.utils.today(),
                       "lines": [{{"product": {product!r}, "item_type": "goods", "description": "flow", "qty": 1, "unit": "ชิ้น",
                                  "unit_price": 100, "vat_rate": 7, "vat_applicable": 1, "warehouse": {house!r}}}]}},
                      request_id="flow-office-actor-{tag}")
    frappe.db.commit()
    push.send(push.SALES, made["name"])  # 正式写入路径排写账件（提交后后台那一轮同一个函数）
    frappe.db.commit()
except Exception:
    if seeded:  # 准备中途出错不留主档种子给同一站点的下一格（11-16 第一次登记电脑被拒时留下过）
        import os
        os.path.exists(facts._path(key)) and os.remove(facts._path(key))
    raise
print(json.dumps({{"doc": made["name"], "key": key, "seeded": seeded, "office": office, "other": other}}))
"""), "company": co, "tag": tag, "cases": []}


def cleanup(lab, manifest, params):
    lab.python(manifest["site"], f"""
import os
from frappe.utils import now_datetime
from pearnly_core.cc_desktop import books
from pearnly_erp_portal.express import facts
if {bool(params.get('seeded'))!r}:
    path = facts._path({params['key']!r})
    if os.path.exists(path):
        os.remove(path)
for device in ({params['office']!r}, {params['other']!r}):
    books.replace(device, [], now_datetime())
    frappe.db.set_value("Pearnly CC Device", device, {{"retired_at": now_datetime(), "token_digest": None}}, update_modified=False)
frappe.db.commit()
print("{{}}")
""")
    void_documents(lab, manifest["site"], [params["doc"]], doctype="ERP Sales Document")


def steps(params, camera_code=None):
    return []  # 没有页面：领单、回执与原因都在 verify 里按正式函数核


def judge(params, results, camera_code=None):
    return []


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
from pearnly_core.cc_desktop import devices, jobs
from pearnly_erp_portal.express import operator, push
doc = frappe.db.get_value(push.SALES, {params['doc']!r}, ["owner", "ledger_state", "ledger_job", "ledger_book", "ledger_reason"],
                          as_dict=True)
job = frappe.db.get_value("Pearnly CC Job", doc.ledger_job, ["name", "actor", "book_key", "status", "request_key"], as_dict=True) \\
    if doc.ledger_job else None
peers = devices.peers()
heads = {{name: [row.name for row in jobs._heads([{params['key']!r}], name, peers[name])] for name in ({params['office']!r}, {params['other']!r})}}
reach = {{name: sorted(jobs.books.writable(name) or []) for name in ({params['office']!r}, {params['other']!r})}}
note = push._queued_note(push.SALES, frappe._dict({{**doc, "name": {params['doc']!r}}}))
frappe.set_user({B!r})
words = operator.computer_state({params['company']!r}, {B!r})
frappe.set_user("Administrator")
written = {{}}
if job:
    push.job_changed({{"source_doctype": push.SALES, "source_name": {params['doc']!r}, "status": "success", "job": job.name,
                      "docnum": "SR-FLOW-{params['tag'][:6]}", "request_key": job.request_key}})
    frappe.db.commit()
    written = frappe.db.get_value(push.SALES, {params['doc']!r}, ["owner", "ledger_state", "ledger_docnum"], as_dict=True)
print(json.dumps({{"doc": doc, "job": job, "heads": heads, "reach": reach, "note": note, "words": words, "written": written}},
                 default=str))
""")
    job, doc, written = got.get("job") or {}, got.get("doc") or {}, got.get("written") or {}
    office, other = params["office"], params["other"]
    return [
        {"case": "the phone-only seller's document is queued for the office member whose computer reaches the book",
         "saved": {"owner": doc.get("owner"), "actor": job.get("actor"), "state": doc.get("ledger_state")},
         "ok": doc.get("owner") == B and job.get("actor") == A and job.get("status") == "queued"},
        {"case": "the office computer can take it; the other company's computer cannot (it does not reach this book)",
         "saved": {"heads": got.get("heads"), "reach": got.get("reach")},
         "ok": job.get("name") in (got["heads"].get(office) or []) and not got["heads"].get(other)
         and params["key"] not in (got["reach"].get(other) or [])},
        {"case": "the document page reason and the conversation no longer say CC Desktop not connected",
         "saved": {"note": got.get("note"), "words": got.get("words")},
         "ok": got.get("note") != "express.cc_desktop_not_connected" and got.get("words") != "express.cc_desktop_not_connected"},
        {"case": "written by that computer: the ERP document is written and still the seller's",
         "saved": written, "ok": written.get("ledger_state") == "written" and written.get("owner") == B},
    ]
