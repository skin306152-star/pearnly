"""流程用例：后台正在给一张采购单排写账件时，人点了「作废」（11-28 并发冲突 B ③，管钱底线）。

关快照隔离后（11-26 生产起），作废请求开头的普通读建好了读视图，之后它等这张单的行锁；后台那次准备（`express.push._send`，
锁着单据行）排上写账件、记下 ``ledger_job``、提交后放锁，作废才拿到锁。作废里撤写账件的那一步（`express.push.void`）修前是
普通读 ``ledger_job``：读到读视图里的「还没有件」，撤不掉刚排上的那件——单据已作废，写账件仍在队里，电脑领走就写进 Express。
修后加锁读，看得到刚排上的件并撤掉。

站点级，两条线程各自连库，按产品真实入口：
- 线程甲（后台）：`push.send`，准备完、排件之前停 3 秒（锁着单据行，模拟主档大、准备久）；
- 线程乙（作废）：开头先普通读一次（同请求开头），0.8 秒后调页面「作废」的白名单入口 `purchasing.api.api_cancel_purchase`。
判：单据是已作废；这张单没有任何没撤销的写账件；单据上不再指着写账件。重复 ``ROUNDS`` 遍，每遍一张新单。

没有页面步骤。准备时放主档种子（同 `erp_express_send_concurrency`），cleanup 先删种子、撤写账件、作废单据。
"""
from __future__ import annotations

import uuid

from erp_lib import company, void_documents

TITLE = "ERP 作废与写账同时到（11-28 管钱底线）：后台正在排写账件时点作废，作废后这张单没有没撤销的写账件、不会写进 Express"
ADMIN = "admin@erp.regression"
ROUNDS = 3


def prepare(lab, manifest):
    co, tag = company(manifest), uuid.uuid4().hex[:8]
    return {**lab.python(manifest["site"], f"""
from pearnly_erp_portal.express import choices, facts, push, target
from pearnly_erp_portal.express import api as express_api
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.purchasing import entry as purchase, suppliers
frappe.set_user({ADMIN!r})
companies.set_active_company({co!r})
key = target.pick(push._company({co!r}), frappe.utils.today())["book"]["key"]
seeded = facts.load(key) is None
if seeded:
    mapping = {{"fallback_acc": "51-03-00-00", "vat_input_acc": "11-06-01-00", "ap_acc": "21-01-01-00", "revenue_acc": "41-01-00-00",
               "vat_output_acc": "21-04-01-00", "ar_acc": "11-03-01-00"}}
    facts.save(key, {{"version": 2, "customers": [], "suppliers": [], "products": [], "warehouses": [{{"code": "01", "name": "W"}}],
                     "settings": {{"mainloc": "01"}}, "stock": [], "departments": [],
                     "facts": {{"accounts": [{{"code": c, "name": c, "type": c[0]}} for c in mapping.values()],
                               "mapping": mapping, "catalog": {{"isacc_candidates": []}}}}}})
supplier = suppliers.create_supplier(supplier_name="flow void race {tag}")["name"]
docs = []
for n in range({ROUNDS}):
    made = purchase.post({{"company": {co!r}, "supplier": supplier, "doc_date": frappe.utils.today(), "currency": "THB", "fx_rate": 1,
                          "has_vat": 1, "price_mode": "exclusive", "doc_kind": "purchase_invoice", "update_stock": 0,
                          "doc_no": "VOIDRACE-{tag}-" + str(n), "printed_total": 107,
                          "lines": [{{"item_type": "service", "description": "flow void race", "qty": 1, "unit_price": 100,
                                     "vat_rate": 7}}]}}, request_id="flow-voidrace-{tag}-" + str(n))
    frappe.db.commit()
    docs.append(made["name"])
# 先按正式写入路径准备一次：要人选的（新供应商的对方档等）照人点选记下答案，再把单据放回「排队中、还没有件」
from pearnly_core.cc_desktop import jobs
for name in docs:
    # 照正式路径准备一次（先放成「排队中、还没有件」，同提交时 `push.queue`），停在要人选的照人点选记下答案，再放回同样的状态
    push._set(push.PURCHASE, name, {{"ledger_state": "queued", "ledger_reason": "", "ledger_needed": None, "ledger_job": None}})
    frappe.db.commit()
    push.send(push.PURCHASE, name)
    frappe.db.commit()
    data = facts.load(key)
    for item in json.loads(frappe.db.get_value(push.PURCHASE, name, "ledger_needed") or "[]"):
        rows = [r for r in express_api._options(item, data) if r.get("code")]
        if rows:
            choices.remember({co!r}, item["kind"], item["subject"], rows[0]["code"], rows[0].get("name") or "")
    for job in frappe.get_all("Pearnly CC Job", filters={{"source_doctype": push.PURCHASE, "source_name": name,
                                                          "status": ["in", ["queued", "manual"]]}}, pluck="name"):
        jobs.cancel(job, "flow prepare")
    push._set(push.PURCHASE, name, {{"ledger_state": "queued", "ledger_reason": "", "ledger_needed": None, "ledger_job": None}})
    frappe.db.commit()
print(json.dumps({{"key": key, "seeded": seeded, "docs": docs, "supplier": supplier}}, default=str))
"""), "company": co, "tag": tag}


RACE = '''
import threading, time
from pearnly_erp_portal.express import push
from pearnly_erp_portal.purchasing import api as purchase_api
site, sites = frappe.local.site, frappe.local.sites_path
real = push._prepare_body
slow = threading.local()
def delayed(*args, **kwargs):
    out = real(*args, **kwargs)
    if getattr(slow, "on", False):
        time.sleep(3)  # 锁着单据行、还没排件（不提交：行锁一直在）
    return out
push._prepare_body = delayed
def run(fn, results, label):
    frappe.init(site=site, sites_path=sites)
    frappe.connect()
    frappe.set_user(ADMIN)
    try:
        results[label] = fn()
        frappe.db.commit()
    except Exception as error:
        frappe.db.rollback()
        results[label] = {"error": repr(error)[:300]}
    finally:
        frappe.destroy()
def sending(name):
    slow.on = True
    return push.send(push.PURCHASE, name)
def voiding(name):
    frappe.db.sql("select count(*) from `tabPurchase Document`")  # 请求开头的普通读：读视图从这里起
    time.sleep(0.8)
    return purchase_api.api_cancel_purchase(name, "flow void race")  # 页面「作废」的同一个白名单入口
def race(name):
    results = {}
    one = threading.Thread(target=run, args=(lambda: sending(name), results, "send"))
    two = threading.Thread(target=run, args=(lambda: voiding(name), results, "void"))
    one.start(); two.start(); one.join(60); two.join(60)
    return results
def state(name):
    frappe.db.rollback()
    row = frappe.db.get_value(push.PURCHASE, name, ["status", "ledger_state", "ledger_job"], as_dict=True)
    live = [dict(j) for j in frappe.get_all("Pearnly CC Job", filters={"source_doctype": push.PURCHASE, "source_name": name},
                                             fields=["name", "status", "request_key"]) if j.status != "cancelled"]
    return {"status": row.status, "ledger_state": row.ledger_state or "", "job": row.ledger_job or "", "live": live}
'''


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], "ADMIN = " + repr(ADMIN) + "\n" + RACE + f"""
frappe.set_user(ADMIN)
out = []
for name in {params['docs']!r}:
    before = state(name)
    raced = race(name)
    out.append({{"name": name, "before": before, "race": raced, "after": state(name)}})
print(json.dumps(out, default=str))
""")
    cases = []
    for row in got:
        before, after = row.get("before") or {}, row.get("after") or {}
        cases.append({"case": f"{row['name']}: queued with no write job before the race (the setup is real)",
                      "saved": before, "ok": before.get("ledger_state") == "queued" and not before.get("job")
                      and not before.get("live")})
        cases.append({"case": f"{row['name']}: voided while the job was being queued leaves no live write job",
                      "saved": {"race": row.get("race"), "after": after},
                      "ok": after.get("status") == "void" and not after.get("live") and not after.get("job")})
    return cases


def steps(params, camera_code=None):
    return []


def judge(params, results, camera_code=None):
    return []


def cleanup(lab, manifest, params):
    lab.python(manifest["site"], f"""
import os
from pearnly_core.cc_desktop import jobs
from pearnly_erp_portal.express import facts
if {bool(params.get('seeded'))!r}:
    path = facts._path({params['key']!r})
    if os.path.exists(path):
        os.remove(path)
for name in {params.get('docs') or []!r}:
    for job in frappe.get_all("Pearnly CC Job", filters={{"source_name": name, "status": ["in", ["queued", "manual"]]}}, pluck="name"):
        jobs.cancel(job, "flow cleanup")
frappe.db.commit()
print("{{}}")
""")
    void_documents(lab, manifest["site"], params.get("docs") or [])
