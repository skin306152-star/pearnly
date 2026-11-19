"""流程用例：ERP 领单时判写账人（用户 11-17 定稿，两产品同一条）的管钱一面——两台电脑同时抢同一件只领到一次；旧件闸。

实验栈里没有真的 CC 桌面版：按 CC 桌面版的样子在站点里登记三台电脑（`cc_desktop.devices.register`）并报同一本账套
（`books.replace`）：办公室两台只有 A 登录过 CC、在线；销售员 B 自己一台、先关着。B 开两张销售单、按正式写入路径排写账件
（`push.send`）：第一张排在切换时刻（站点配置 ``pearnly_writer_rule_since``，存量站点迁移时由补丁写上）之前，第二张在之后。

1. 两件都归 B（写账人不在排队时换）。
2. 办公室两台各自连库、同时按领单同一个函数（`jobs._claim`）来领：只有一台领到、只领到切换之后那件（旧件不替、也不挡它），
   写账件记下替写的 A、``fence`` 只加一次，另一台什么都没领到、不报错。
3. 那件写完后，B 自己的电脑上线、放开旧件闸（删掉这项配置）：办公室那台不再替（本人的电脑在线用自己的），B 那台领到旧件、
   不记替写人。

没有页面步骤。cleanup：还原站点配置、撤写账件、作废单据、移除三台电脑与账套行、删主档种子。
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

from erp_lib import codes, company, void_documents, warehouses

TITLE = "ERP 领单时判写账人：两台电脑同时抢同一件只领到一次、记下替写人；切换前排的旧件不替也不挡新件；本人电脑在线时不替（11-17 定稿）"
A, B = "admin@erp.regression", "sales@erp.regression"
SINCE = "pearnly_writer_rule_since"


def _post(lab, manifest, co, tag, n, product, house):
    return lab.python(manifest["site"], f"""
from pearnly_erp_portal.express import push
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.sales import entry as sales
frappe.set_user({B!r})
companies.set_active_company({co!r})
made = sales.post({{"company": {co!r}, "doc_date": frappe.utils.today(), "has_vat": 1, "vat_rate": 7, "source": "manual",
                   "doc_kind": "receipt", "buyer_name": "flow writer {tag[:6]} {n}", "warehouse": {house!r}, "paid_amount": 107,
                   "payment_method": "cash", "payment_date": frappe.utils.today(),
                   "lines": [{{"product": {product!r}, "item_type": "goods", "description": "flow", "qty": 1, "unit": "ชิ้น",
                              "unit_price": 100, "vat_rate": 7, "vat_applicable": 1, "warehouse": {house!r}}}]}},
                  request_id="flow-writer-{tag}-{n}")
frappe.db.commit()
push.send(push.SALES, made["name"])
frappe.db.commit()
print(json.dumps({{"doc": made["name"], "job": frappe.db.get_value(push.SALES, made["name"], "ledger_job")}}))
""")


def prepare(lab, manifest):
    co, tag = company(manifest), uuid.uuid4().hex
    product = codes(manifest)["by_express"]["product"]
    house = next(row["name"] for row in warehouses(manifest) if row.get("express_code"))
    made = lab.python(manifest["site"], f"""
from datetime import timedelta
from pearnly_core.cc_desktop import books, devices
from pearnly_erp_portal.express import facts, push, target

row = push._company({co!r})
key = target.pick(row, frappe.utils.today())["book"]["key"]
book = frappe.get_all("Pearnly CC Book", filters={{"book_key": key}}, fields=["path", "company", "tax_id", "branch", "period_start",
                                                                            "period_end", "uid"], limit_page_length=1)[0]
raw = {{"path": book.path, "company": book.company, "tax_id": book.tax_id, "branch": book.branch, "writable": True,
        "period_start": str(book.period_start), "period_end": str(book.period_end), "uid": book.uid}}
seeded = facts.load(key) is None
if seeded:  # 实验栈里这本账套的主档没落地：放一份（同 `erp_office_actor`），cleanup 先删
    mapping = {{"fallback_acc": "51-03-00-00", "vat_input_acc": "11-06-01-00", "ap_acc": "21-01-01-00", "revenue_acc": "41-01-00-00",
               "vat_output_acc": "21-04-01-00", "ar_acc": "11-03-01-00"}}
    facts.save(key, {{"version": 2, "customers": [], "suppliers": [], "products": [{{"code": "EXP-0001", "name": "สินค้าจาก Express",
                     "kind": "stock", "unit": "ชิ้น", "prices": ["50"]}}], "warehouses": [{{"code": "01", "name": "คลัง 1"}}],
                     "settings": {{"mainloc": "01"}}, "stock": [], "departments": [],
                     "facts": {{"accounts": [{{"code": c, "name": c, "type": c[0]}} for c in mapping.values()],
                               "mapping": mapping, "catalog": {{"isacc_candidates": []}}}}}})
now = devices.now()  # 报到时刻的写法（在线按它判，`contracts.online`）
made = {{}}
for label, user, online in (("one", {A!r}, True), ("two", {A!r}, True), ("own", {B!r}, False)):
    frappe.set_user(user)
    device = devices.register(device_key="flow-wc-" + label + "-{tag}", device_name="flow writer " + label)["device"]
    books.replace(device, [raw], now)
    frappe.db.set_value("Pearnly CC Device", device, "last_seen_at", now if online else now - timedelta(days=1),
                        update_modified=False)
    made[label] = device
frappe.db.commit()
print(json.dumps({{"key": key, "seeded": seeded, **made}}))
""")
    first = _post(lab, manifest, co, tag, 1, product, house)
    time.sleep(1.2)
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # 迁移那一刻（补丁写的同一种写法）
    before = lab.site_config(manifest["site"], {SINCE: since})
    time.sleep(1.2)
    second = _post(lab, manifest, co, tag, 2, product, house)
    return {**made, "company": co, "tag": tag, "since": since, "before": before.get(SINCE), "old": first, "new": second,
            "cases": []}


RACE = '''
import threading, types
from pearnly_core.cc_desktop import devices, jobs
site, sites = frappe.local.site, frappe.local.sites_path
# 两台都算完排头、做完判定再一起去加锁领：同一刻抢同一件
gate = threading.Barrier(2, timeout=20)
real = jobs.route_contracts.decide
def decide(*args, **kwargs):
    out = real(*args, **kwargs)
    try:
        gate.wait()
    except threading.BrokenBarrierError:
        pass
    return out
jobs.route_contracts.decide = decide
def claim(device, results):
    frappe.init(site=site, sites_path=sites)
    frappe.connect()
    try:
        results[device] = len(jobs._claim(types.SimpleNamespace(device=device), {}, devices.now()))
        frappe.db.commit()
    except Exception as error:
        frappe.db.rollback()
        results[device] = "error: " + repr(error)[:300]
    finally:
        frappe.destroy()
def job(name):
    frappe.db.rollback()
    return frappe.db.get_value("Pearnly CC Job", name, ["status", "actor", "writer", "lease_device", "fence"], as_dict=True) or {}
'''


def verify(lab, manifest, params, camera_code=None):
    old, new = params["old"]["job"], params["new"]["job"]
    raced = lab.python(manifest["site"], RACE + f"""
queued = {{"old": job({old!r}), "new": job({new!r})}}
for device in ({params['one']!r}, {params['two']!r}):  # 准备到这里过了几分钟：办公室两台照常在报到（报到时刻同 `devices.now`）
    frappe.db.set_value("Pearnly CC Device", device, "last_seen_at", devices.now(), update_modified=False)
frappe.db.commit()
results = {{}}
threads = [threading.Thread(target=claim, args=(device, results)) for device in ({params['one']!r}, {params['two']!r})]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join(60)
after = {{"old": job({old!r}), "new": job({new!r})}}
# 那件照电脑写好的样子收尾（领走的电脑回执成功）：这本账套空出来
frappe.db.set_value("Pearnly CC Job", {new!r}, {{"status": "success", "writing_key": None}}, update_modified=False)
frappe.db.commit()
print(json.dumps({{"queued": queued, "results": results, "after": after}}, default=str))
""")
    lab.site_config(manifest["site"], {SINCE: None})  # 用户看过统计定了「旧件也写」：删掉这项即放开
    own = lab.python(manifest["site"], RACE + f"""
frappe.db.set_value("Pearnly CC Device", {params['own']!r}, "last_seen_at", devices.now(), update_modified=False)  # B 的电脑上线
frappe.db.commit()
office = len(jobs._claim(types.SimpleNamespace(device={params['one']!r}), {{}}, devices.now()))
frappe.db.commit()
mine = len(jobs._claim(types.SimpleNamespace(device={params['own']!r}), {{}}, devices.now()))
frappe.db.commit()
# B 那台领到的那件（并行跑时同一本账套里别的流程也可能给 B 排了件，领到的不一定是旧件；看它是不是 B 自己的、没记替写人）
taken = frappe.get_all("Pearnly CC Job", filters={{"lease_device": {params['own']!r}, "status": "leased"}},
                       fields=["name", "actor", "writer"], limit_page_length=5)
print(json.dumps({{"office": office, "own": mine, "old": job({old!r}), "taken": [dict(row) for row in taken]}}, default=str))
""")
    queued, results, after = raced.get("queued") or {}, raced.get("results") or {}, raced.get("after") or {}
    taken = [device for device, count in results.items() if count == 1]
    return [
        {"case": "both documents are queued as the seller's own (who writes is decided when a computer claims)",
         "saved": queued, "ok": all((queued.get(k) or {}).get("actor") == B and (queued.get(k) or {}).get("status") == "queued"
                                    for k in ("old", "new"))},
        {"case": "two office computers claiming at the same moment: exactly one takes it, once, standing in for B as A",
         "saved": {"results": results, "after": after},
         "ok": len(taken) == 1 and all(count in (0, 1) for count in results.values())
         and (after.get("new") or {}).get("status") == "leased" and (after.get("new") or {}).get("lease_device") == taken[0]
         and (after.get("new") or {}).get("writer") == A and (after.get("new") or {}).get("actor") == B
         and int((after.get("new") or {}).get("fence") or 0) == 1},
        {"case": "the job queued before the switch is not stood in for and does not block the newer one",
         "saved": after.get("old"), "ok": (after.get("old") or {}).get("status") == "queued" and not (after.get("old") or {}).get("writer")},
        {"case": "with the submitter's own computer online the office computer does not stand in; hers takes it as her own",
         "saved": own, "ok": own.get("office") == 0 and own.get("own") == 1 and len(own.get("taken") or []) == 1
         and (own.get("taken") or [{}])[0].get("actor") == B and not (own.get("taken") or [{}])[0].get("writer")},
    ]


def steps(params, camera_code=None):
    return []


def judge(params, results, camera_code=None):
    return []


def cleanup(lab, manifest, params):
    lab.site_config(manifest["site"], {SINCE: params.get("before")})
    lab.python(manifest["site"], f"""
import os
from frappe.utils import now_datetime
from pearnly_core.cc_desktop import books, jobs
from pearnly_erp_portal.express import facts
if {bool(params.get('seeded'))!r}:
    path = facts._path({params.get('key', '')!r})
    if os.path.exists(path):
        os.remove(path)
for name in {[params[k]["job"] for k in ("old", "new") if params.get(k, {}).get("job")]!r} + frappe.get_all(
        "Pearnly CC Job", filters={{"lease_device": {params.get('own', '')!r}, "status": "leased"}}, pluck="name"):
    if frappe.db.get_value("Pearnly CC Job", name, "status") in ("queued", "leased"):
        frappe.db.set_value("Pearnly CC Job", name, {{"status": "cancelled", "writing_key": None, "reason": "flow cleanup"}},
                            update_modified=False)
for device in {[params[k] for k in ("one", "two", "own") if params.get(k)]!r}:
    books.replace(device, [], now_datetime())
    frappe.db.set_value("Pearnly CC Device", device, {{"retired_at": now_datetime(), "token_digest": None}}, update_modified=False)
frappe.db.commit()
print("{{}}")
""")
    docs = [params[k]["doc"] for k in ("old", "new") if params.get(k, {}).get("doc")]
    if docs:
        void_documents(lab, manifest["site"], docs, doctype="ERP Sales Document")
