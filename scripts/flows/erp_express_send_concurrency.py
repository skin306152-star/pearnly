"""流程用例：同一张单的两次写账准备交错（`express.push._send`，11-16 验收首跑：收银日汇总写成了，单据上的账套被盖空、已答过的
「是不是增值税登记户」又挂回来；按件数现算请求号时还能排出第二件、写两次账）。站点级：两条线程各自连库，按产品里真实的两个入口：

1. 「poll 与答题同时」：一张停在要人选（新供应商的对方档等）的采购单。线程甲照每分钟那一轮重准备（`push.send`），准备完故意
   慢 3 秒再落库（模拟主档大、准备久）；线程乙在这期间照 `express.api.answer` 记下答案、清停下原因、提交、再交（`push.send`）。
   准备途中照主档落地那样提交一次（事务换新，数据库的快照隔离不再拦晚写回）。修好后：线程甲写回前锁内再核，见账本栏已变就
   放弃，结果以答案为准——单据排上一件写账件、不再挂着要问的项（修前：线程甲晚落库，把单据盖回「停下、要问」，写账件却已排上）。
2. 不用问的单，两次交单交错（线程甲慢）：修好后只有一件写账件（修前：后到的按件数编出 #2，排出第二件）。

没有页面步骤；准备时放主档种子（同 `erp_cc_operator_intake`），cleanup 先删种子、撤写账件、作废单据。
"""
from __future__ import annotations

import uuid

from erp_lib import company, void_documents, warehouses

TITLE = "ERP 写账准备并发：poll 与答题同时以答案为准、不丢字段；两次交单交错只排一件写账件（11-16 管钱缺陷）"
ADMIN = "admin@erp.regression"


def prepare(lab, manifest):
    co, tag = company(manifest), uuid.uuid4().hex[:8]
    house = warehouses(manifest)[0]["name"]
    return {**lab.python(manifest["site"], f"""
from pearnly_erp_portal.express import facts, push, target
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
supplier = suppliers.create_supplier(supplier_name="flow concurrency {tag}")["name"]
def bought(n):
    made = purchase.post({{"company": {co!r}, "supplier": supplier, "doc_date": frappe.utils.today(), "currency": "THB", "fx_rate": 1,
                          "has_vat": 1, "price_mode": "exclusive", "doc_kind": "purchase_invoice", "update_stock": 0,
                          "doc_no": "CONC-{tag}-" + str(n), "printed_total": 107,
                          "lines": [{{"item_type": "service", "description": "flow concurrency", "qty": 1, "unit_price": 100,
                                     "vat_rate": 7}}]}}, request_id="flow-conc-{tag}-" + str(n))
    frappe.db.commit()
    return made["name"]
first, second = bought(1), bought(2)
print(json.dumps({{"key": key, "seeded": seeded, "first": first, "second": second, "supplier": supplier}}))
"""), "company": co, "tag": tag, "cases": []}


# 两条线程各自连库跑的那一段（站点里执行）：线程甲 slow=True，准备（`_prepare_body`）算完后睡 3 秒再交回
RACE = '''
import threading, time
from pearnly_erp_portal.express import api as express_api, choices, contracts, facts, push
site, sites = frappe.local.site, frappe.local.sites_path
real = push._prepare_body
slow = threading.local()
def delayed(*args, **kwargs):
    out = real(*args, **kwargs)
    if getattr(slow, "on", False):
        frappe.db.commit()  # 准备途中提交过一次（同主档落地「不带着事务等」，`express.masters`）：事务换新，旧快照不再护着
        time.sleep(3)
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
def poll(name):
    slow.on = True
    return push.send(push.PURCHASE, name)
def race(a, b):
    results = {}
    one = threading.Thread(target=run, args=(a, results, "a"))
    two = threading.Thread(target=run, args=(b, results, "b"))
    one.start(); time.sleep(0.8); two.start(); one.join(60); two.join(60)
    return results
def state(name):
    frappe.db.rollback()  # 结束本线程的旧快照（可重复读），读两条线程提交后的样子
    row = frappe.db.get_value(push.PURCHASE, name, ["ledger_state", "ledger_reason", "ledger_job", "ledger_book", "ledger_needed"],
                              as_dict=True)
    live = [j for j in frappe.get_all("Pearnly CC Job", filters={"source_doctype": push.PURCHASE, "source_name": name},
                                       fields=["name", "status", "request_key"]) if j.status != "cancelled"]
    return {"state": row.ledger_state, "reason": row.ledger_reason or "", "job": row.ledger_job or "",
            "book": row.ledger_book or "", "needed": row.ledger_needed or "", "live": [dict(j) for j in live]}
'''


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], "ADMIN = " + repr(ADMIN) + "\n" + RACE + f"""
frappe.set_user(ADMIN)
first, second = {params['first']!r}, {params['second']!r}
# 第一张：先按正式写入路径准备一次，停在要人选（新供应商的对方档等），取出要答的项与第一个可选值（同人在单据页点选）
push.send(push.PURCHASE, first)
frappe.db.commit()
held = state(first)
asked = json.loads(frappe.db.get_value(push.PURCHASE, first, "ledger_needed") or "[]")
data = facts.load({params['key']!r})
answers = []
for item in asked:
    rows = [r for r in express_api._options(item, data) if r.get("code")]
    if rows:
        answers.append((item["kind"], item["subject"], rows[0]["code"], rows[0].get("name") or ""))
def answer():  # 同 `express.api.answer`：记下、清停下原因、提交、再交
    for kind, subject, value, label in answers:
        choices.remember({params['company']!r}, kind, subject, value, label)
    push._set(push.PURCHASE, first, {{"ledger_reason": "", "ledger_needed": None}})
    frappe.db.commit()
    return push.send(push.PURCHASE, first)
one = race(lambda: poll(first), answer)
after_answer = state(first)
# 第二张：同一供应商，答案已记住，不用再问；两次交单交错（线程甲慢）
two = race(lambda: poll(second), lambda: push.send(push.PURCHASE, second))
after_double = state(second)
print(json.dumps({{"held": held, "answers": len(answers), "race_one": one, "after_answer": after_answer, "race_two": two,
                  "after_double": after_double}}, default=str))
""")
    held, after, double = got.get("held") or {}, got.get("after_answer") or {}, got.get("after_double") or {}
    return [
        {"case": "the first document stops on a choice before the race (the setup is real)",
         "saved": {"held": held, "answers": got.get("answers")},
         "ok": held.get("state") == "held" and str(held.get("reason") or "").startswith("choice_needed") and got.get("answers", 0) > 0},
        {"case": "poll and an answer at the same time: the answer wins, one write job, nothing asked any more",
         "saved": {"race": got.get("race_one"), "after": after},
         "ok": after.get("state") == "queued" and not after.get("needed") and len(after.get("live") or []) == 1
         and after.get("job") == (after.get("live") or [{}])[0].get("name") and bool(after.get("book"))},
        {"case": "two sends at once queue exactly one write job",
         "saved": {"race": got.get("race_two"), "after": double},
         "ok": len(double.get("live") or []) == 1 and double.get("job") == (double.get("live") or [{}])[0].get("name")},
    ]


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
for name in ({params['first']!r}, {params['second']!r}):
    for job in frappe.get_all("Pearnly CC Job", filters={{"source_name": name, "status": ["in", ["queued", "manual"]]}}, pluck="name"):
        jobs.cancel(job, "flow cleanup")
frappe.db.commit()
print("{{}}")
""")
    void_documents(lab, manifest["site"], [params["first"], params["second"]])
