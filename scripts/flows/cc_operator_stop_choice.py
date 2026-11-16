"""流程用例：代录 / 直写被电脑拦下后有路可走（11-16 真机 H5）。

H5：ERP 网页建采购单（新物料）交代录，CC 桌面版按账套里用得最多的存货科目组（ST01 = 办公设备）建档、被自己的判据拦下，回
``STOCK_ACC_GROUP_UNFIT``；对话里那张卡只有「重试」、不说为什么，用户无路可走。修法两段，这条流程各核一遍：

- S1 开始前就问：账套里已有库存品、这张单要新建库存品、合格的存货科目组两个以上、没选过 → 开始前确认卡片问一次「挂哪个组」
  （按在用的库存品数预选），记住后再准备，载荷带上选的组（`stock_acc_group.for_new_items`）。
- S2 拦下后有选项：电脑写的时候以「要人选一次」的码拦下（这里用 ``SUPPLIER_DUP_SUSPECTED``：同税号两个供应商档，只有电脑读到）
  → 开始前确认卡片 / 代录中途那张卡列出那一项与单据页同一份选项（`api.blocked_needed`），代录卡片带上产品那一句原因
  （`products.reason_text`）；页面打开代录会话，那张卡是提问卡（有选项），不再只有「重试」。

实验栈没有真电脑读账套：站点里临时放一份账套资料（`express.facts`，同 `erp_express_new_item`），cleanup 原样还原；建的单作废、
没写的写账件撤掉、记住的选择删掉。只用 new 数据集（接了 Express）。
"""
from __future__ import annotations

import uuid

from cc_lib import JS
from erp_lib import void_documents

TITLE = "代录 / 直写被电脑拦下后有路可走：开始前问存货科目组、拦下的码出原因与选项卡、选了带上再交"
READY = "document.readyState === 'complete' && !!document.querySelector('[data-mode=\"operator\"]')"
USER = "admin@erp.regression"
SUPPLIER_TAX = "0105561116001"  # 校验位正确（ERP 建供应商要核）


def prepare(lab, manifest):
    tag = uuid.uuid4().hex[:6]
    company = manifest["company"]
    warehouse = manifest["warehouses"][0]["name"]
    lab.keep_fields(manifest["site"], "ERP Company", company, ["express_sync_state"])
    return {**lab.python(manifest["site"], f"""
import os
import time
from pearnly_core.assistant import sessions
from pearnly_core.cc_desktop import jobs
from pearnly_core.express_operator import feed, tasks
from pearnly_erp_portal.express import choices, facts, operator, precheck, push, target
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.products import service as products
from pearnly_erp_portal.purchasing import entry, suppliers

frappe.set_user({USER!r})
company = {company!r}
companies.set_active_company(company)
key = target.pick(push._company(company), frappe.utils.nowdate())["book"]["key"]
path = facts._path(key)
backup = path + ".flow-{tag}"
if os.path.exists(path):
    os.replace(path, backup)
# 这份账套：两个供应商同税号（电脑才认得出疑似重复）；存货科目组两个合格（DM 在用 4 件、P002 在用 1 件）、一个不合格（ST01 办公设备）
facts.save(key, {{"version": 2, "customers": [], "departments": [], "products": [],
                 "suppliers": [{{"code": "SUP-A-{tag}", "name": "ผู้ขาย H5 {tag}", "tax_id": {SUPPLIER_TAX!r}, "branch": "00000"}},
                               {{"code": "SUP-B-{tag}", "name": "ผู้ขาย H5 {tag} (เก่า)", "tax_id": {SUPPLIER_TAX!r},
                                 "branch": "00000"}}],
                 "facts": {{"accounts": [{{"code": "11-04-01-00", "name": "วัตถุดิบคงเหลือ", "type": "1"}}], "mapping": {{}},
                           "catalog": {{"isacc_candidates": [
                               {{"code": "DM", "desc": "วัตถุดิบ", "inv_acc": "11-04-01-00", "fit": True, "used_by": 4}},
                               {{"code": "P002", "desc": "สินค้า", "inv_acc": "11-04-02-00", "fit": True, "used_by": 1}},
                               {{"code": "ST01", "desc": "อุปกรณ์สำนักงาน", "inv_acc": "14-01-03-00", "fit": False, "used_by": 2}}]}}}}}})
supplier = suppliers.create_supplier(supplier_name="ผู้ขาย H5 {tag}", tax_id={SUPPLIER_TAX!r})["name"]
product = products.create_product(product_name="D1116 H5 Item {tag}", unit="ชิ้น", unit_price=500, default_cost=500)["name"]
frappe.db.commit()
doc = entry.post({{"company": company, "supplier": supplier, "doc_date": frappe.utils.nowdate(), "currency": "THB", "fx_rate": 1,
                  "has_vat": 1, "price_mode": "exclusive", "doc_kind": "purchase_invoice", "update_stock": 1,
                  "doc_no": "H5-{tag}", "warehouse": {warehouse!r},
                  "lines": [{{"product": product, "item_type": "goods", "description": "D1116 H5 Item {tag}", "qty": 3,
                             "unit": "ชิ้น", "unit_price": 500, "vat_rate": 7, "warehouse": {warehouse!r}, "location": ""}}]}},
                 request_id="flow-h5-{tag}")
frappe.db.commit()
name = doc["name"]
out = {{"doc": name, "company": company, "book": key, "backup": backup, "path": path, "product": product}}

def kinds(found):
    return [(item["kind"], item.get("value") or "", [o.get("code") for o in item.get("options") or []]) for item in found["items"]]

# S1：新物料先答「新建」（H5 当时选的），再判 → 问存货科目组、预选在用最多的 DM；选 P002 后再准备，载荷带 P002
first = precheck.collect_document(push.PURCHASE, name)
out["s1_first"] = kinds(first)
for item in first["items"]:
    if item["kind"] == "item":
        choices.remember(company, "item", item["subject"], "__new__")
        out["item_subject"] = item["subject"]
frappe.db.commit()
second = precheck.collect_document(push.PURCHASE, name)
out["s1_group"] = kinds(second)
group = next((item for item in second["items"] if item["kind"] == choices.STOCK_GROUP), None)
if group:
    choices.remember(company, choices.STOCK_GROUP, group["subject"], "P002")
    out["group_subject"] = group["subject"]
frappe.db.commit()
ready = precheck.collect_document(push.PURCHASE, name)
out["s1_payload_group"] = (ready["result"].get("payload") or {{}}).get("stock_acccod")

# S2：交进写账队列，电脑以「同税号两个供应商档」拦下（同电脑回执：写账件转人工带码、单据记成停下）
push.send(push.PURCHASE, name)
frappe.db.commit()
job = frappe.db.get_value(push.PURCHASE, name, "ledger_job")
out["job"] = job
reason = "[SUPPLIER_DUP_SUSPECTED] two suppliers share tax id {SUPPLIER_TAX}"
if job:
    frappe.db.set_value("Pearnly CC Job", job, {{"status": "manual", "reason": reason}}, update_modified=False)
    push.job_changed({{"source_doctype": push.PURCHASE, "source_name": name, "status": "manual", "job": job, "reason": reason}})
    frappe.db.commit()
blocked = precheck.collect_document(push.PURCHASE, name)
out["s2_items"] = kinds(blocked)
# 代录会话里这张停下的单：卡片带原因那一句；代录预检列出要选的那一项
made = sessions.create(project=company, period=frappe.utils.nowdate()[:7], mode="operator")
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": made["id"]}}, "title", "{tag} H5")
task = frappe.get_doc(tasks.DOCTYPE, made["task"])
now = frappe.utils.now_datetime().replace(microsecond=0)
task.title, task.status, task.started_at, task.finished_at = "{tag} H5", "attention", now, now
task.append("documents", {{"doc_id": "P:" + name, "ref": name, "party": "ผู้ขาย H5 {tag}", "doc_group": "purchase",
                           "state": "not_done", "reason": reason, "queued_at": now, "done_at": now}})
task.flags.ignore_permissions = True
task.save()
frappe.db.commit()
card = next((e for e in sessions.events(made["id"])["events"] if e.get("name") == feed.CHOOSE), None)
out["card_reason_text"] = ((card or {{}}).get("input") or {{}}).get("reason_text", "")
checked = operator.precheck(company, ["P:" + name])["documents"][0]
out["operator_items"] = [item["kind"] for item in (checked.get("precheck") or {{}}).get("items") or []]
out["session"], out["task"] = made["id"], made["task"]
print(json.dumps(out, default=str))
"""), "tag": tag}


def cleanup(lab, manifest, params):
    """账套资料原样还原、记住的选择删掉、建的单作废（没写的写账件随作废撤掉）、代录会话连任务删掉。成功、失败、超时都跑。"""
    lab.python(manifest["site"], f"""
import os
from pearnly_core.assistant import sessions
from pearnly_erp_portal.express import choices
frappe.set_user({USER!r})
path, backup = {params.get("path")!r}, {params.get("backup")!r}
if path and os.path.exists(path):
    os.remove(path)
if backup and os.path.exists(backup):
    os.replace(backup, path)
for kind, subject in (("item", {params.get("item_subject")!r}), (choices.STOCK_GROUP, {params.get("group_subject")!r})):
    if subject:
        found = choices._name({params.get("company")!r}, kind, subject)
        if frappe.db.exists(choices.CHOICE, found):
            frappe.delete_doc(choices.CHOICE, found, ignore_permissions=True, force=True)
if {params.get("session")!r}:
    try:
        sessions.delete({params.get("session")!r})
    except Exception:
        pass
frappe.db.commit()
print("{{}}")
""")
    if params.get("doc"):
        void_documents(lab, manifest["site"], [params["doc"]])


def steps(params, camera_code=None):
    return [{"name": "打开代录会话", "js": JS +
             "const card=await until(()=>q('[data-slot=\"cc-askq-question\"]')||q('[data-slot=\"cc-op-choose\"]'),160); await sleep(500); "
             "return {card: !!card, question: q('[data-slot=\"cc-askq-question\"]')?.innerText.trim()||'', "
             "choices: qa('[data-slot=\"cc-askq-choice\"], .cc-askq-opt').map(e=>e.innerText.trim()).slice(0,10), "
             "retryOnly: !!q('[data-slot=\"cc-op-retry\"]') && !q('[data-slot=\"cc-askq-question\"]'), "
             "reason: q('[data-slot=\"cc-op-reason\"]')?.innerText.trim()||''};"}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") for r in results if r.get("step") == "打开代录会话"), None) or {}
    return [{"case": "代录会话里停下的那张是提问卡（有可选项），不再只有「重试」", "seen": seen,
             "ok": bool(seen.get("card")) and bool(seen.get("question")) and not seen.get("retryOnly")}]


def verify(lab, manifest, params, camera_code=None):
    group = next((row for row in params.get("s1_group") or [] if row[0] == "stock_group"), None)
    return [
        {"case": "S1：新物料先问新建还是对应已有", "seen": params.get("s1_first"),
         "ok": any(row[0] == "item" for row in params.get("s1_first") or [])},
        {"case": "S1：账套已有库存品、两个合格组、没选过 → 开始前问挂哪个组，只列合格的，按在用数预选 DM", "seen": group,
         "ok": bool(group) and group[1] == "DM" and sorted(group[2]) == ["DM", "P002"]},
        {"case": "S1：选了 P002 后再准备，载荷带 stock_acccod = P002（不再交给 CC 桌面版按众数选）",
         "seen": params.get("s1_payload_group"), "ok": params.get("s1_payload_group") == "P002"},
        {"case": "S2：电脑以同税号两个供应商档拦下 → 卡片列出选对方档那一项、选项是这两个档",
         "seen": params.get("s2_items"),
         "ok": any(row[0] == "party" and {"SUP-A-" + params["tag"], "SUP-B-" + params["tag"]} <= set(row[2])
                   for row in params.get("s2_items") or [])},
        {"case": "S2：代录预检同样列出这一项（中途那张卡照它出题）", "seen": params.get("operator_items"),
         "ok": "party" in (params.get("operator_items") or [])},
        {"case": "S2：代录卡片带上产品那一句原因（不是空、不是原因码）", "seen": params.get("card_reason_text"),
         "ok": bool(params.get("card_reason_text")) and "SUPPLIER_DUP_SUSPECTED" not in params.get("card_reason_text", "")},
    ]
