"""流程用例：被电脑以「要人选一次」的码拦下的单，答过一次就不再问、写入放行（11-26 真机 PE2569-000021）。

11-26 testerp 副本：采购单上次被 CC 桌面版以 ``STOCK_ACC_GROUP_UNFIT`` 拦下；CC 对话里选了存货科目组（记下 DM），开始前确认卡片
照旧补回这道题、值为空，写入计划判「未定」，直写、代录都反复追问。修法（`express.api._unanswered`）：这张单停下之后答过、答的仍是
能选的值 → 不再出题，单据照「再写一次」重新准备一件（原来那件 ``replaced``，不清原因放行），由新的结果说话。

备数照 `cc_operator_stop_choice` 同一套：站点里临时放一份账套资料（两个合格的存货科目组 DM / P002，`express.facts`），网页同一个入账
函数建一张要新建库存品的采购单，直写交进写账队列，再把那件写账件标成电脑以 ``STOCK_ACC_GROUP_UNFIT`` 转人工（同电脑回执）。
直写：CC 的开始前确认卡片与 ``pearnly_precheck_answer`` 同一个共用入口（`write_precheck.api.card` / ``record``）答 DM，再判不再问、
写入计划放行，直写同一个出口（`cc_tools._route`）照「再写一次」重新准备，新的一件带 DM、原来那件 replaced。
代录：代录预检（`operator.precheck`）答前问、答后不问；页面打开代录会话，那张卡不再是提问卡。cleanup 原样还原账套资料、删记住的
选择与会话、建的单作废（没写的写账件随作废撤掉）。只用 new 数据集（接了 Express）。
"""
from __future__ import annotations

import uuid

from cc_lib import JS
from erp_lib import void_documents

TITLE = "被电脑以要人选的码拦下的单答过一次就不再问：直写与代录开始前确认不再出这道题、写入计划放行、照再写一次重新准备（带上答的组）"
USER = "admin@erp.regression"
REASON = "[STOCK_ACC_GROUP_UNFIT] ST01 stock account 14-01-03-00 is not an inventory account"


def prepare(lab, manifest):
    tag = uuid.uuid4().hex[:6]
    company = manifest["company"]
    warehouse = manifest["warehouses"][0]["name"]
    lab.keep_fields(manifest["site"], "ERP Company", company, ["express_sync_state"])
    return {**lab.python(manifest["site"], f"""
import os
from pearnly_core.assistant import sessions
from pearnly_core.cc_desktop import jobs
from pearnly_core.express_operator import tasks
from pearnly_core.write_precheck import api as write_precheck, contracts as card_rules
from pearnly_erp_portal.express import api, cc_tools, choices, facts, operator, precheck, push, target
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
# 同 cc_operator_stop_choice：存货科目组两个合格（DM 4 件、P002 1 件）、一个不合格（ST01）；总数 6 → 能证明挂得最多的 DM 合格，
# 开始前不问组、照旧交电脑（电脑那头按自己的众数挂了 ST01、被自己的判据拦下 = 真机那一种）
facts.save(key, {{"version": 2, "customers": [], "departments": [],
                 "products": [{{"code": "OTHER-{tag}", "name": "สินค้าอื่น {tag}", "kind": "stock"}}],
                 "suppliers": [{{"code": "SUP-A-{tag}", "name": "ผู้ขาย BQ {tag}", "tax_id": "0105561116001", "branch": "00000"}}],
                 "facts": {{"accounts": [{{"code": code, "name": code, "type": code[0]}} for code in (
                               "11-01-01-00", "11-02-01-00", "11-04-01-00", "11-04-02-00", "52-08-00-00", "41-01-00-00",
                               "11-03-01-00", "21-04-01-00", "51-03-00-00", "21-01-01-00", "11-06-01-00")],
                           "mapping": {{"revenue_acc": "41-01-00-00", "ar_acc": "11-03-01-00", "vat_output_acc": "21-04-01-00",
                                       "fallback_acc": "51-03-00-00", "ap_acc": "21-01-01-00", "vat_input_acc": "11-06-01-00"}},
                           "catalog": {{"isacc_candidates": [
                               {{"code": "DM", "desc": "วัตถุดิบ", "inv_acc": "11-04-01-00", "fit": True, "used_by": 4}},
                               {{"code": "P002", "desc": "สินค้า", "inv_acc": "11-04-02-00", "fit": True, "used_by": 1}},
                               {{"code": "ST01", "desc": "อุปกรณ์สำนักงาน", "inv_acc": "14-01-03-00", "fit": False, "used_by": 1}}],
                                      "fingerprint": {{"stock_master_count": 6}}}}}}}})
supplier = suppliers.create_supplier(supplier_name="ผู้ขาย BQ {tag}", tax_id="0105561116001")["name"]
product = products.create_product(product_name="BQ Item {tag}", unit="ชิ้น", unit_price=500, default_cost=500)["name"]
frappe.db.commit()
def kinds(found):
    return [(item["kind"], item.get("value") or "", [o.get("code") for o in item.get("options") or []]) for item in found["items"]]

out = {{"company": company, "book": key, "backup": backup, "path": path, "docs": []}}

def blocked_document(suffix, operator_run):
    # 建一张要新建库存品的采购单，交进写账队列（直写或代录），电脑以存货科目组不合格拦下（同电脑回执：写账件转人工带码）
    doc = entry.post({{"company": company, "supplier": supplier, "doc_date": frappe.utils.nowdate(), "currency": "THB", "fx_rate": 1,
                      "has_vat": 1, "price_mode": "exclusive", "doc_kind": "purchase_invoice", "update_stock": 1,
                      "doc_no": "BQ-{tag}-" + suffix, "warehouse": {warehouse!r},
                      "lines": [{{"product": product, "item_type": "goods", "description": "BQ Item {tag}", "qty": 2,
                                 "unit": "ชิ้น", "unit_price": 500, "vat_rate": 7, "warehouse": {warehouse!r}, "location": ""}}]}},
                     request_id="flow-bq-{tag}-" + suffix)
    frappe.db.commit()
    name = doc["name"]
    out["docs"].append(name)
    # 新物料先答「新建」（同真机那张单当时的选择），开始前就没有要问的了
    for item in precheck.collect_document(push.PURCHASE, name)["items"]:
        if item["kind"] == "item":
            choices.remember(company, "item", item["subject"], "__new__")
            out["item_subject"] = item["subject"]
    frappe.db.commit()
    out["ready_items_" + suffix] = kinds(precheck.collect_document(push.PURCHASE, name))
    if operator_run:
        push.send(push.PURCHASE, name, operator={{"id": "flow-{tag}", "index": 1, "total": 1}})
    else:
        push.send(push.PURCHASE, name)
    frappe.db.commit()
    job = frappe.db.get_value(push.PURCHASE, name, "ledger_job")
    if job:
        frappe.db.set_value("Pearnly CC Job", job, {{"status": "manual", "reason": {REASON!r}}})
        push.job_changed({{"source_doctype": push.PURCHASE, "source_name": name, "status": "manual", "job": job, "reason": {REASON!r}}})
        frappe.db.commit()
    return name, job

direct, old = blocked_document("D", False)   # 直写交的
op_name, op_job = blocked_document("O", True)  # 代录交的
doc_id, op_id = "P:" + direct, "P:" + op_name
out.update(doc=direct, doc_id=doc_id, op_doc=op_name, op_id=op_id, old_job=old, op_job=op_job)
frappe.db.rollback()  # 换一份新快照：停下的时刻（写账件 modified）先于下面的作答

def operator_kinds(one):
    return [item["kind"] for item in (operator.precheck(company, [one])["documents"][0].get("precheck") or {{}}).get("items") or []]

# 答之前：直写的开始前确认卡片问存货科目组（不带预选值）、这张单留下；代录交的那张代录预检同样问；单据上不能「再写一次」
before = write_precheck.card(company, [doc_id])
group = next((item for item in card_rules.items_of(before) if item.get("kind") == choices.STOCK_GROUP), None)
out["before"] = {{"group": bool(group), "value": (group or {{}}).get("value") or "", "needs_card": before.get("needs_card"),
                 "held": card_rules.held_back(before, [])}}
out["before_operator"] = operator_kinds(op_id)
row = cc_tools._row(cc_tools.KINDS["purchase"], direct, write=True)
view = api._view(push.PURCHASE, row)
out["before_view"] = {{"needed": [item["kind"] for item in view["needed"]], "can_retry": view["can_retry"]}}

# 作答：CC 的 pearnly_precheck_answer 同一个共用入口（不带 resume：对话里写入只走计划）；记在公司 × 账套上，两张同一题
if group:
    answered = write_precheck.record(company, [doc_id], [{{"key": group["key"], "value": "DM"}}])
    out["answered_errors"] = answered.get("errors") or []
    out["group_subject"] = group.get("subject") or ""
frappe.db.commit()
stored = frappe.get_all(choices.CHOICE, filters={{"company": company, "kind": choices.STOCK_GROUP}}, fields=["subject", "value"])
out["stored"] = stored
out["group_subject"] = out.get("group_subject") or next((r["subject"] for r in stored if r["value"] == "DM"), "")

# 答之后：再判不再问、写入计划放行；代录预检同样不问；单据上能「再写一次」
after = write_precheck.card(company, [doc_id])
out["after"] = {{"kinds": [item.get("kind") for item in card_rules.items_of(after)], "needs_card": after.get("needs_card"),
                "held": card_rules.held_back(after, []), "writable": card_rules.writable(after, [])}}
out["after_operator"] = operator_kinds(op_id)
row = cc_tools._row(cc_tools.KINDS["purchase"], direct, write=True)
view = api._view(push.PURCHASE, row)
out["after_view"] = {{"needed": [item["kind"] for item in view["needed"]], "can_retry": view["can_retry"]}}

# 代录会话里代录交的那张停下的单（同 cc_operator_stop_choice 的造法）：页面那张卡不再是提问卡
made = sessions.create(project=company, period=frappe.utils.nowdate()[:7], mode="operator")
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": made["id"]}}, "title", "{tag} BQ")
task = frappe.get_doc(tasks.DOCTYPE, made["task"])
now = frappe.utils.now_datetime().replace(microsecond=0)
task.title, task.status, task.started_at, task.finished_at = "{tag} BQ", "attention", now, now
task.append("documents", {{"doc_id": op_id, "ref": op_name, "party": "ผู้ขาย BQ {tag}", "doc_group": "purchase",
                           "state": "not_done", "reason": {REASON!r}, "queued_at": now, "done_at": now}})
task.flags.ignore_permissions = True
task.save()
frappe.db.commit()
out["session"], out["task"] = made["id"], made["task"]

# 直写出口（写入计划同意后 `push_documents` 逐张调的那一个）：交出去、原来那件 replaced、新的一件带上答的组
out["route"] = cc_tools._route(cc_tools.KINDS["purchase"], direct, row)
frappe.db.commit()
new = frappe.db.get_value(push.PURCHASE, direct, ["ledger_job", "ledger_state"], as_dict=True)
out["new"] = {{"job": new.ledger_job, "state": new.ledger_state}}
out["old_after"] = frappe.db.get_value("Pearnly CC Job", old, ["status", "reason"], as_dict=True) if old else None
payload = frappe.db.get_value("Pearnly CC Job", new.ledger_job, "payload") if new.ledger_job else ""
out["new_group"] = (json.loads(payload or "{{}}").get("payload") or json.loads(payload or "{{}}")).get("stock_acccod") if payload else None
print(json.dumps(out, default=str))
"""), "tag": tag}


def cleanup(lab, manifest, params):
    """账套资料原样还原、记住的选择删掉、代录会话连任务删掉、建的单作废（没写的写账件随作废撤掉）。成功、失败、超时都跑。"""
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
    if params.get("docs"):
        void_documents(lab, manifest["site"], params["docs"])


def steps(params, camera_code=None):
    return [{"name": "打开代录会话", "js": JS +
             "const card=await until(()=>q('[data-slot=\"cc-askq-question\"]')||q('[data-slot=\"cc-op-choose\"]'),160); "
             # 答之前这张卡在预检回包后换成提问卡（cc.operator.stop-choice 等了约 30 秒）：同样等足，没换才算不再问
             "await until(()=>q('[data-slot=\"cc-askq-question\"]'),120); await sleep(500); "
             "return {card: !!card, question: q('[data-slot=\"cc-askq-question\"]')?.innerText.trim()||'', "
             "retry: !!q('[data-slot=\"cc-op-retry\"]'), reason: q('[data-slot=\"cc-op-reason\"]')?.innerText.trim()||''};"}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") for r in results if r.get("step") == "打开代录会话"), None) or {}
    return [{"case": "代录会话里代录交的那张停下的单，答过存货科目组后那张卡不再是提问卡",
             "seen": seen, "ok": bool(seen.get("card")) and not seen.get("question")}]


def verify(lab, manifest, params, camera_code=None):
    before, after = params.get("before") or {}, params.get("after") or {}
    doc_id = params.get("doc_id")
    return [
        {"case": "备数：新物料答了「新建」后开始前不再要问（电脑那头才拦下，服务端开始前判不出）",
         "seen": [params.get("ready_items_D"), params.get("ready_items_O")],
         "ok": params.get("ready_items_D") == [] and params.get("ready_items_O") == []},
        {"case": "答之前：直写那张开始前确认卡片问存货科目组、值为空（不带预选）、这张单留下；代录交的那张代录预检同样问；单据上不能「再写一次」",
         "seen": {"before": before, "operator": params.get("before_operator"), "view": params.get("before_view")},
         "ok": bool(before.get("group")) and not before.get("value") and doc_id in (before.get("held") or [])
               and "stock_group" in (params.get("before_operator") or [])
               and (params.get("before_view") or {}).get("can_retry") is False},
        {"case": "作答（pearnly_precheck_answer 同一个共用入口）记下 DM，没有报错", "seen": {"errors": params.get("answered_errors"),
                                                                                "stored": params.get("stored")},
         "ok": not params.get("answered_errors") and any(r.get("value") == "DM" for r in params.get("stored") or [])},
        {"case": "答之后：直写那张开始前确认不再出这道题、写入计划放行；代录交的那张代录预检也不再问",
         "seen": {"after": after, "operator": params.get("after_operator")},
         "ok": "stock_group" not in (after.get("kinds") or []) and doc_id not in (after.get("held") or [])
               and doc_id in (after.get("writable") or []) and "stock_group" not in (params.get("after_operator") or [])},
        {"case": "答之后单据上能「再写一次」、不再列这一项", "seen": params.get("after_view"),
         "ok": (params.get("after_view") or {}).get("needed") == [] and (params.get("after_view") or {}).get("can_retry") is True},
        {"case": "直写出口照再写一次重新准备一件：交出去、原来那件被替换（不是清原因放行）、新的一件带 DM",
         "seen": {"route": params.get("route"), "new": params.get("new"), "old": params.get("old_after"),
                  "group": params.get("new_group"), "old_job": params.get("old_job")},
         "ok": params.get("route") == "sent" and (params.get("new") or {}).get("job") not in (None, "", params.get("old_job"))
               and (params.get("old_after") or {}).get("status") == "cancelled" and params.get("new_group") == "DM"},
    ]
