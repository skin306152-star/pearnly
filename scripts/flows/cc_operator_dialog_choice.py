"""流程用例：代录在 Express 中途遇到配方外的业务选择框，带回对话让人选（CC 桌面版 1.12.23 ③④，A5 服务端一半）。

电脑把框、单据窗口和 Express 原样留着，回执原因 ``dialog_choice`` 带 ``meta.operator.dialog``（指纹、原文、按钮）。站点这一侧：

- 判定：这张单在 ERP 的写账前置判定里多一项 ``operator_dialog``（问框的原文，选项是框上的按钮），代录预检列进 ``needed``；
- 页面：打开代录会话，那张卡是提问卡，选项就是框上的按钮（不是只有「重试」）；
- 答了：按公司 + 指纹记住（下回代录载荷带 ``operator.dialog_answers``），并给留着框的那台电脑排一条「点这个钮」（领单回复带下去）。

实验栈没有真电脑：账套资料临时放一份（同 ``cc.operator.stop-choice``），电脑的回执由脚本按 CC 桌面版的形状写进写账件；cleanup 原样
还原、记住的选择删掉、建的单作废。只用 new 数据集。
"""
from __future__ import annotations

import uuid

from cc_lib import JS
from erp_lib import void_documents

TITLE = "代录中途配方外的业务选择框带回对话：判定出一题（选项是框上的按钮）、页面出提问卡、答了按公司记住并叫电脑去点"
USER = "admin@erp.regression"
SUPPLIER_TAX = "0105561116001"
DIGEST = "a1b2c3d4e5f60718"
BOX_TEXT = "เลือกวิธีบันทึกรายการนี้"
BUTTONS = ["แบบ ก", "แบบ ข"]
DEVICE = "PC-FLOW-DIALOG"


def prepare(lab, manifest):
    tag = uuid.uuid4().hex[:6]
    company = manifest["company"]
    warehouse = manifest["warehouses"][0]["name"]
    lab.keep_fields(manifest["site"], "ERP Company", company, ["express_sync_state"])
    return {**lab.python(manifest["site"], f"""
import json as _json
import os
from pearnly_core.assistant import sessions
from pearnly_core.cc_desktop import operator_controls
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
facts.save(key, {{"version": 2, "customers": [], "departments": [],
                 # 存货表读到了（一件无关的库存品）：新物料按品名认不到，开始前问「新建 / 对应已有」
                 "products": [{{"code": "OTHER-{tag}", "name": "สินค้าอื่น {tag}", "kind": "stock"}}],
                 "suppliers": [{{"code": "SUP-A-{tag}", "name": "ผู้ขาย H5 {tag}", "tax_id": {SUPPLIER_TAX!r}, "branch": "00000"}},
                               {{"code": "SUP-B-{tag}", "name": "ผู้ขาย H5 {tag} (เก่า)", "tax_id": "", "branch": ""}}],
                 # 科目表与默认科目对照（同 ERP 单测的一套）：采购单要采购 / 进项税 / 应付科目才准备得出来
                 "facts": {{"accounts": [{{"code": code, "name": code, "type": code[0]}} for code in (
                               "11-01-01-00", "11-02-01-00", "11-04-01-00", "11-04-02-00", "52-08-00-00", "41-01-00-00",
                               "11-03-01-00", "21-04-01-00", "51-03-00-00", "21-01-01-00", "11-06-01-00")],
                           "mapping": {{"revenue_acc": "41-01-00-00", "ar_acc": "11-03-01-00", "vat_output_acc": "21-04-01-00",
                                       "fallback_acc": "51-03-00-00", "ap_acc": "21-01-01-00", "vat_input_acc": "11-06-01-00"}},
                           "catalog": {{"isacc_candidates": [
                               {{"code": "DM", "desc": "วัตถุดิบ", "inv_acc": "11-04-01-00", "fit": True, "used_by": 4}},
                               {{"code": "P002", "desc": "สินค้า", "inv_acc": "11-04-02-00", "fit": True, "used_by": 1}},
                               {{"code": "ST01", "desc": "อุปกรณ์สำนักงาน", "inv_acc": "14-01-03-00", "fit": False, "used_by": 2}}]}}}}}})
supplier = suppliers.create_supplier(supplier_name="ผู้ขาย H5 {tag}", tax_id={SUPPLIER_TAX!r})["name"]
product = products.create_product(product_name="D1117 Box Item {tag}", unit="ชิ้น", unit_price=500, default_cost=500)["name"]
frappe.db.commit()
doc = entry.post({{"company": company, "supplier": supplier, "doc_date": frappe.utils.nowdate(), "currency": "THB", "fx_rate": 1,
                  "has_vat": 1, "price_mode": "exclusive", "doc_kind": "purchase_invoice", "update_stock": 1,
                  "doc_no": "BOX-{tag}", "warehouse": {warehouse!r},
                  "lines": [{{"product": product, "item_type": "goods", "description": "D1117 Box Item {tag}", "qty": 2,
                             "unit": "ชิ้น", "unit_price": 500, "vat_rate": 7, "warehouse": {warehouse!r}, "location": ""}}]}},
                 request_id="flow-box-{tag}")
frappe.db.commit()
name = doc["name"]
out = {{"doc": name, "company": company, "book": key, "backup": backup, "path": path, "product": product}}
# 这张单别的都先定下（新物料「新建」、存货组 DM），只剩电脑中途碰到的那个框
first = precheck.collect_document(push.PURCHASE, name)
for item in first["items"]:
    if item["kind"] == "item":
        choices.remember(company, "item", item["subject"], "__new__")
        out["item_subject"] = item["subject"]
frappe.db.commit()
for item in precheck.collect_document(push.PURCHASE, name)["items"]:
    if item["kind"] == choices.STOCK_GROUP:
        choices.remember(company, choices.STOCK_GROUP, item["subject"], "DM")
        out["group_subject"] = item["subject"]
frappe.db.commit()
push.send(push.PURCHASE, name, operator={{"id": "flow-{tag}", "index": 1, "total": 1}})
frappe.db.commit()
job = frappe.db.get_value(push.PURCHASE, name, "ledger_job")
out["job"] = job
reason = "[OPERATOR_DIALOG_CHOICE] business choice box: ExpressI {BOX_TEXT}"
meta = {{"operator": {{"batch": "flow-{tag}", "doc": "P:" + name, "reason": "dialog_choice",
                      "held": {{"computer": "PC-FLOW"}},
                      "dialog": {{"digest": {DIGEST!r}, "title": "ExpressI", "text": {BOX_TEXT!r}, "buttons": {BUTTONS!r},
                                 "doc_type": "RR", "stage": "saving"}}}}}}
if job:
    frappe.db.set_value("Pearnly CC Job", job, {{"status": "manual", "reason": reason, "written_by_device": {DEVICE!r},
                                                 "meta_json": _json.dumps(meta, ensure_ascii=False)}}, update_modified=False)
    push.job_changed({{"source_doctype": push.PURCHASE, "source_name": name, "status": "manual", "job": job, "reason": reason}})
    frappe.db.commit()
stop = precheck.dialog_stop(push.PURCHASE, name)
out["stop"] = {{k: stop.get(k) for k in ("digest", "buttons", "device", "batch", "doc")}}
checked = operator.precheck(company, ["P:" + name])["documents"][0]
out["operator_needed"] = [(n["kind"], [o.get("code") for o in n.get("options") or []]) for n in checked.get("needed") or []]
made = sessions.create(project=company, period=frappe.utils.nowdate()[:7], mode="operator")
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": made["id"]}}, "title", "{tag} BOX")
task = frappe.get_doc(tasks.DOCTYPE, made["task"])
now = frappe.utils.now_datetime().replace(microsecond=0)
task.title, task.status, task.started_at, task.finished_at = "{tag} BOX", "attention", now, now
task.append("documents", {{"doc_id": "P:" + name, "ref": name, "party": "ผู้ขาย H5 {tag}", "doc_group": "purchase",
                           "state": "not_done", "reason": reason, "queued_at": now, "done_at": now}})
task.flags.ignore_permissions = True
task.save()
frappe.db.commit()
card = next((e for e in sessions.events(made["id"])["events"] if e.get("name") == feed.CHOOSE), None)
out["card_reason"] = ((card or {{}}).get("input") or {{}}).get("reason", "")
out["session"], out["task"] = made["id"], made["task"]
print(json.dumps(out, default=str))
"""), "tag": tag}


def cleanup(lab, manifest, params):
    """账套资料原样还原、记住的选择删掉、信箱清掉、建的单作废、代录会话连任务删掉。成功、失败、超时都跑。"""
    lab.python(manifest["site"], f"""
import os
from pearnly_core.assistant import sessions
from pearnly_core.cc_desktop import operator_controls
from pearnly_erp_portal.express import choices
frappe.set_user({USER!r})
path, backup = {params.get("path")!r}, {params.get("backup")!r}
if path and os.path.exists(path):
    os.remove(path)
if backup and os.path.exists(backup):
    os.replace(backup, path)
company = {params.get("company")!r}
for kind, subject in (("item", {params.get("item_subject")!r}), (choices.STOCK_GROUP, {params.get("group_subject")!r}),
                      (choices.OPERATOR_DIALOG, {DIGEST!r})):
    if subject and company:
        found = choices._name(company, kind, subject)
        if frappe.db.exists(choices.CHOICE, found):
            frappe.delete_doc(choices.CHOICE, found, ignore_permissions=True, force=True)
operator_controls.take({DEVICE!r}, "99.0.0")
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
             "const card=await until(()=>q('[data-slot=\"cc-askq-question\"]')||q('[data-slot=\"cc-op-choose\"]'),160); "
             "await until(()=>q('[data-slot=\"cc-askq-question\"]'),120); await sleep(500); "
             "return {card: !!card, question: q('[data-slot=\"cc-askq-question\"]')?.innerText.trim()||'', "
             "choices: qa('[data-slot=\"cc-askq-option\"]').map(e=>e.innerText.trim()).slice(0,10), "
             "retryOnly: !!q('[data-slot=\"cc-op-retry\"]') && !q('[data-slot=\"cc-askq-question\"]')};"}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") for r in results if r.get("step") == "打开代录会话"), None) or {}
    choices_seen = " ".join(seen.get("choices") or [])
    return [{"case": "代录会话里停在框上的那张是提问卡，问的是框的原文，选项是框上的按钮", "seen": seen,
             "ok": bool(seen.get("question")) and BOX_TEXT in seen.get("question", "") and all(b in choices_seen for b in BUTTONS)
             and not seen.get("retryOnly")}]


def verify(lab, manifest, params, camera_code=None):
    answered = lab.python(manifest["site"], f"""
from pearnly_core.cc_desktop import operator_controls
from pearnly_erp_portal.express import choices, precheck, push
frappe.set_user({USER!r})
company, name = {params.get("company")!r}, {params.get("doc")!r}
saved = precheck.remember(company, {{"P:" + name: [{{"kind": "operator_dialog", "subject": {DIGEST!r}, "value": "แบบ ข", "label": "x"}}]}})
frappe.db.commit()
old = operator_controls.take({DEVICE!r}, "1.12.22")
new = operator_controls.take({DEVICE!r}, "1.12.23")
print(json.dumps({{"saved": saved, "remembered": choices.operator_dialogs(company), "old_cc": old, "controls": new}}, default=str))
""")
    stop = params.get("stop") or {}
    return [
        {"case": "回执里的框被读出来：指纹、按钮、留着框的电脑与批次", "seen": stop,
         "ok": stop.get("digest") == DIGEST and stop.get("buttons") == BUTTONS and stop.get("device") == DEVICE
         and stop.get("doc") == "P:" + str(params.get("doc"))},
        {"case": "代录预检把这个框列进 needed，选项是框上的按钮", "seen": params.get("operator_needed"),
         "ok": ["operator_dialog", BUTTONS] in [list(row) for row in params.get("operator_needed") or []]},
        {"case": "代录卡片的原因是 dialog_choice", "seen": params.get("card_reason"),
         "ok": "DIALOG_CHOICE" in str(params.get("card_reason") or "").upper()},
        {"case": "答了：按公司 + 指纹记住；只给 1.12.23 起的电脑排一条「点这个钮」", "seen": answered,
         "ok": (answered.get("remembered") or {}).get(DIGEST) == "แบบ ข" and answered.get("old_cc") == []
         and [(c.get("action"), c.get("digest"), c.get("button")) for c in answered.get("controls") or []]
         == [("answer", DIGEST, "แบบ ข")]},
    ]
