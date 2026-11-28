"""流程用例：后台正在给一张单排写账件时，人作废了它（11-28 并发冲突 B ③，管钱底线：作废的单没有没撤销的写账件）。

关快照隔离后（11-26 生产起），作废请求开头的普通读建好了读视图，之后它等这张单的行锁；后台那次准备（`express.push._send`，
锁着单据行）排上写账件、记下 ``ledger_job``、提交后放锁，作废才拿到锁。修前作废用读视图里的旧副本 ``doc.save()``：后台写
``ledger_job`` 用的是不改修改时间的 ``set_value``，保存前比对拦不住，保存把它盖回空，撤件那一步（`express.push.void`）就撤不掉
刚排上的那件——单据已作废，写账件仍在队里，电脑领走就写进 Express。修后两层：作废入口先结束读视图、先锁后读
（`api_cancel_*`、`api_void_purchase`、`access.get_document`）；撤件按来源单据在写账件表里加锁撤掉全部没写的件（不依赖单据栏位）。

站点级，两条线程各自连库，按产品真实入口，三格：
- ``purchase``：页面「作废」`purchasing.api.api_cancel_purchase`；
- ``sales``：页面「作废」`sales.api.api_cancel_sale`；
- ``amend``：采购「修改」`purchasing.api.api_post_purchase(amended_from=原单)`（作废原单再建，作废在提交事务里，入口层管不到，
  靠撤件不变式）；
- ``stock``：入库单作废 `inventory.api.api_void_stock_entry`（仓库要已对上 Express 仓库码，new 数据集照主档同步对好）。
线程甲（后台）：`push.send`，准备完、排件之前停 3 秒（锁着单据行）；线程乙：开头普通读一次（同请求开头），0.8 秒后调上面的入口。
判：单据（修改格是原单）是已作废；这张单没有任何没撤销的写账件。每格 ``ROUNDS`` 遍、每遍一张新单。

没有页面步骤。准备时放主档种子（同 `erp_express_send_concurrency`），cleanup 先删种子、撤写账件、作废单据。
"""
from __future__ import annotations

import uuid

from erp_lib import company, void_documents

TITLE = "ERP 作废与写账同时到（11-28 管钱底线）：后台正在排写账件时作废（采购、销售、采购修改作废原单），作废的单没有没撤销的写账件"
ADMIN = "admin@erp.regression"
ROUNDS = 3
KINDS = ("purchase", "sales", "amend", "stock")
DOCTYPES = {"purchase": "Purchase Document", "amend": "Purchase Document", "sales": "ERP Sales Document", "stock": "ERP Stock Entry"}


def prepare(lab, manifest):
    co, tag = company(manifest), uuid.uuid4().hex[:8]
    return {**lab.python(manifest["site"], f"""
from pearnly_erp_portal.express import choices, facts, push, target
from pearnly_erp_portal.express import api as express_api
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.purchasing import entry as purchase, suppliers
from pearnly_erp_portal.sales import entry as sales
from pearnly_erp_portal.inventory import documents as stock_docs
from pearnly_erp_portal.products import service as products
from pearnly_core.cc_desktop import jobs
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
def bought(n):
    return purchase.post({{"company": {co!r}, "supplier": supplier, "doc_date": frappe.utils.today(), "currency": "THB", "fx_rate": 1,
                          "has_vat": 1, "price_mode": "exclusive", "doc_kind": "purchase_invoice", "update_stock": 0,
                          "doc_no": "VOIDRACE-{tag}-" + str(n), "printed_total": 107,
                          "lines": [{{"item_type": "service", "description": "flow void race", "qty": 1, "unit_price": 100,
                                     "vat_rate": 7}}]}}, request_id="flow-voidrace-{tag}-" + str(n))["name"]
def sold(n):
    return sales.post({{"company": {co!r}, "doc_date": frappe.utils.today(), "doc_kind": "receipt", "has_vat": 0, "vat_rate": 0,
                       "buyer_name": "flow void race {tag}",
                       "lines": [{{"item_type": "service", "description": "flow void race", "qty": 1, "unit": "งาน",
                                  "unit_price": 100}}]}}, request_id="flow-voidrace-sale-{tag}-" + str(n))["name"]
house = (frappe.get_all("ERP Warehouse", filters={{"company": {co!r}, "is_active": 1, "express_code": ["is", "set"]}},
                        pluck="name", order_by="express_code asc", limit=1) or [None])[0]
item = products.create_product(product_name="flow void race {tag}", unit="ชิ้น")["name"]
def received(n):
    return stock_docs.create_entry(direction="in", posting_date=frappe.utils.nowdate(), warehouse=house, reason="flow void race",
                                   submit=True, lines=[{{"product": item, "qty": 1, "unit_cost": 10, "warehouse": house}}])["name"]
docs = {{}}
for kind in {list(KINDS)!r}:
    docs[kind] = []
    if kind == "stock" and not house:
        continue  # 这家公司没有对上 Express 仓库码的仓：库存这一格跳过（判定里记成没测到）
    for n in range({ROUNDS}):
        docs[kind].append(sold(n) if kind == "sales" else received(n) if kind == "stock" else bought(kind + str(n)))
        frappe.db.commit()
# 提交时 `push.queue` 把单标成「排队中」并排了一次提交后的后台准备（`push.send`，短队列）。等它们每张都真跑过（单不再是
# 「排队中、没有件」）再往下：实验室慢时这批后台准备会落在下面复位成「要人选」之后，读到答案已记住就照常排上件，竞争开始前
# 单上就有件了（11-28 列车合并头 new / 桌面：采购几格成立、后面几种不成立，件的请求号挤在同一秒）。固定等几秒保证不了
import time
def _settled(doctype, name):
    frappe.db.rollback()  # 新事务：看后台任务刚提交的状态
    row = frappe.db.get_value(doctype, name, ["ledger_state", "ledger_job"], as_dict=True) or {{}}
    return row.get("ledger_state") != "queued" or bool(row.get("ledger_job"))
deadline = time.time() + 180
waiting = [({DOCTYPES!r}[kind], name) for kind, names in docs.items() for name in names]
while waiting and time.time() < deadline:
    waiting = [pair for pair in waiting if not _settled(*pair)]
    if waiting:
        time.sleep(1)
if waiting:
    raise SystemExit("post-submit background send did not run within 180s: " + json.dumps(waiting))
# 起始状态：停在「要人选」（答案下面记住，同人在单据页答过、等后台再排）。不用「排队中」：每分钟的写账巡检（`push.poll`）会
# 自己把排队中的单排上件；停在要人选的它不碰。前置全程也不把单放成「排队中、没有件」：巡检若在那时列到它们，逐张交的时候
# 单已复位成要人选，准备照样认记住的答案、排上件，竞争开始前单上就有件了（11-28 列车合并头 new / 桌面，件的请求号挤在同一秒）
START = {{"ledger_state": "held", "ledger_reason": "choice_needed:account", "ledger_needed": None, "ledger_job": None}}
# 照正式路径准备一次（`push.send` 对停下的单同样准备），停在要人选的照人点选记下答案，再放回起始状态
for kind, names in docs.items():
    doctype = {DOCTYPES!r}[kind]
    for name in names:
        push._set(doctype, name, START)
        frappe.db.commit()
        push.send(doctype, name)
        frappe.db.commit()
        data = facts.load(key)
        for item in json.loads(frappe.db.get_value(doctype, name, "ledger_needed") or "[]"):
            rows = [r for r in express_api._options(item, data) if r.get("code")]
            if rows:
                choices.remember({co!r}, item["kind"], item["subject"], rows[0]["code"], rows[0].get("name") or "")
        for job in frappe.get_all("Pearnly CC Job", filters={{"source_doctype": doctype, "source_name": name,
                                                              "status": ["in", ["queued", "manual"]]}}, pluck="name"):
            jobs.cancel(job, "flow prepare")
        push._set(doctype, name, START)
        frappe.db.commit()
# 复位后要稳住：在途的巡检 / 后台准备若又排出件，撤掉、再复位，直到连续 3 秒没有单再有件（最多 120 秒）
def _live(doctype, name):
    return frappe.get_all("Pearnly CC Job", filters={{"source_doctype": doctype, "source_name": name,
                                                       "status": ["in", ["queued", "manual"]]}}, pluck="name")
pairs = [({DOCTYPES!r}[kind], name) for kind, names in docs.items() for name in names]
deadline, quiet = time.time() + 120, 0
while time.time() < deadline and quiet < 3:
    time.sleep(1)
    frappe.db.rollback()
    moved = [(doctype, name) for doctype, name in pairs
             if _live(doctype, name) or frappe.db.get_value(doctype, name, "ledger_state") != "held"]
    for doctype, name in moved:
        for job in _live(doctype, name):
            jobs.cancel(job, "flow prepare")
        push._set(doctype, name, START)
        frappe.db.commit()
    quiet = 0 if moved else quiet + 1
if quiet < 3:
    raise SystemExit("documents kept getting write jobs after the reset for 120s")
print(json.dumps({{"key": key, "seeded": seeded, "docs": docs, "supplier": supplier, "stock_house": house}}, default=str))
"""), "company": co, "tag": tag}


RACE = '''
import threading, time
from pearnly_erp_portal.express import push
from pearnly_erp_portal.purchasing import api as purchase_api
from pearnly_erp_portal.sales import api as sales_api
from pearnly_erp_portal.inventory import api as stock_api
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
def sending(doctype, name):
    slow.on = True
    return push.send(doctype, name)
def amend(name):
    doc = frappe.get_doc(push.PURCHASE, name)
    payload = {"company": doc.company, "supplier": doc.supplier, "doc_date": str(doc.doc_date), "currency": "THB", "fx_rate": 1,
               "has_vat": 1, "price_mode": "exclusive", "doc_kind": doc.doc_kind, "update_stock": 0,
               "doc_no": doc.doc_no + "-A", "printed_total": 107,
               "lines": [{"item_type": "service", "description": "flow void race amended", "qty": 1, "unit_price": 100,
                          "vat_rate": 7}]}
    return purchase_api.api_post_purchase(json.dumps(payload), "flow-voidrace-amend-" + name, amended_from=name)
def voiding(kind, name):
    frappe.db.sql("select count(*) from `tabPurchase Document`")  # 请求开头的普通读：读视图从这里起
    time.sleep(0.8)
    if kind == "sales":
        return sales_api.api_cancel_sale(name, "flow void race")
    if kind == "amend":
        return amend(name)
    if kind == "stock":
        return stock_api.api_void_stock_entry(name=name)
    return purchase_api.api_cancel_purchase(name, "flow void race")  # 页面「作废」的同一个白名单入口
def race(kind, doctype, name):
    results = {}
    one = threading.Thread(target=run, args=(lambda: sending(doctype, name), results, "send"))
    two = threading.Thread(target=run, args=(lambda: voiding(kind, name), results, "void"))
    one.start(); two.start(); one.join(60); two.join(60)
    return results
def state(doctype, name):
    frappe.db.rollback()
    row = frappe.db.get_value(doctype, name, ["status", "ledger_state", "ledger_job"], as_dict=True)
    live = [dict(j) for j in frappe.get_all("Pearnly CC Job", filters={"source_doctype": doctype, "source_name": name},
                                             fields=["name", "status", "request_key"]) if j.status != "cancelled"]
    return {"status": row.status, "ledger_state": row.ledger_state or "", "job": row.ledger_job or "", "live": live}
'''


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], "ADMIN = " + repr(ADMIN) + "\n" + RACE + f"""
frappe.set_user(ADMIN)
out = []
for kind, names in {params['docs']!r}.items():
    doctype = {DOCTYPES!r}[kind]
    for name in names:
        before = state(doctype, name)
        raced = race(kind, doctype, name)
        out.append({{"kind": kind, "name": name, "before": before, "race": raced, "after": state(doctype, name)}})
print(json.dumps(out, default=str))
""")
    cases = [{"case": "stock: a warehouse mapped to an Express warehouse exists (else the stock cell is not run)",
              "saved": {"house": params.get("stock_house")}, "ok": bool(params.get("stock_house"))}]
    for row in got:
        before, after = row.get("before") or {}, row.get("after") or {}
        sent = (row.get("race") or {}).get("send") or {}
        cases.append({"case": f"{row['kind']} {row['name']}: waiting on an answered choice with no write job before, and the background send queued one "
                              "(the race is real)",
                      "saved": {"before": before, "send": sent},
                      "ok": before.get("ledger_state") == "held" and not before.get("job") and not before.get("live")
                      and sent.get("status") in ("sent", "already_queued")})
        cases.append({"case": f"{row['kind']} {row['name']}: voided while the job was being queued leaves no live write job",
                      "saved": {"race": row.get("race"), "after": after},
                      "ok": after.get("status") == "void" and not after.get("live")})
    return cases


def steps(params, camera_code=None):
    return []


def judge(params, results, camera_code=None):
    return []


def cleanup(lab, manifest, params):
    docs = params.get("docs") or {}
    lab.python(manifest["site"], f"""
import os
from pearnly_core.cc_desktop import jobs
from pearnly_erp_portal.express import facts
if {bool(params.get('seeded'))!r}:
    path = facts._path({params.get('key')!r})
    if os.path.exists(path):
        os.remove(path)
names = {[name for names in docs.values() for name in names]!r}
names += frappe.get_all("Purchase Document", filters={{"corrected_from": ["in", names or [""]]}}, pluck="name")
for name in names:
    for job in frappe.get_all("Pearnly CC Job", filters={{"source_name": name, "status": ["in", ["queued", "manual"]]}}, pluck="name"):
        jobs.cancel(job, "flow cleanup")
frappe.db.commit()
print("{{}}")
""")
    void_documents(lab, manifest["site"], (docs.get("purchase") or []) + (docs.get("amend") or []))
    void_documents(lab, manifest["site"], docs.get("sales") or [], doctype="ERP Sales Document")
    if docs.get("stock"):
        lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import documents
for name in {docs['stock']!r}:
    if frappe.db.get_value("ERP Stock Entry", name, "status") == "submitted":
        documents.void_entry(name)
print("{{}}")
""")
