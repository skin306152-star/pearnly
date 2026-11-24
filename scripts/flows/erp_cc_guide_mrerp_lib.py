"""ERP 版 CC **直写**对话里的采购 / 销售引导在**接 MR.ERP 的公司**上（总表「采购 / 销售 · ERP 直写 · MR.ERP」）的共用片段：
`erp_cc_guide_mrerp_purchase.py`、`erp_cc_guide_mrerp_sales.py` 各调一次，只差业务。

11-24 线上缺陷：CC 对话放进来的收单记录在没接 Express 的公司上一律判 ``express.disconnected``，ERP 直写 · MR.ERP 写不进
（cc-guide-mrerp-direct 改成按「账本 + 对话方式」判）。这条守的是修好之后从页面点卡能走到提交、排上 MR.ERP 写账。

造数据（不连真 MR.ERP、不带凭据）：一条编造的 MR.ERP 连接（账套目录一本覆盖今天的年度账套 :data:`SET`，导入页菜单采购 / 赊销各两个
已关联日记账的、还没选过——推送会停在「先选导入菜单」，所以卡上要问）→ 网页同一个建公司函数建公司、接上这条连接与账套（插连接后先提交，
避开同一事务里 tabSeries 的 1020）→ 一件这家公司账本里的物料 → 本人从直写对话交进来的一张识别完成的收单记录（行认出这件物料、仓库定好），
同 `erp_cc_operator_intake.prepare` 的造法（那边的 Express 主档种子这里不用）。实验栈没有识别，用 documents 源的公开入口
``run.start(doc_ids=[I:…], at="asking")`` 开这次引导。

之后全在页面上点卡（点法同 `erp_cc_guide_direct_lib.steps`，不调接口、不经模型）：题卡上出 MR.ERP 导入菜单题（选项「代码 · 名称」），
逐题照推荐或第一项点 → 复核卡（这张票一行）→ 确定 → 结果卡。没有凭据，后台写账登录不了 MR.ERP，结果卡上这张不说已写入。
库里：引导运行 done、同意来自页面；一份直写写入计划；收单记录用网页同一个入账函数提交成这家公司的 ERP 单（来源 cc）、去向 MR.ERP；
卡上答的菜单记在连接上这份账套名下。
"""
from __future__ import annotations

import json
import time
import uuid

import erp_cc_guide_direct_lib as direct
from erp_lib import void_documents

USER = direct.USER
WORD = direct.WORD
DOCTYPES = direct.DOCTYPES
SET = "9:1"
BOOK_TAX, OWN_TAX = "0105561000038", "0105561000046"   # 编造（过校验位）
URL = "https://flow-mrerp.example.com"
KINDS = {"purchase": ("purchase", "purchase_expense"), "sales": ("sales_credit", "sales_cash")}

SEED = f'''
import base64, hashlib
from pearnly_core.assistant import sessions
from pearnly_erp_portal.integrations import intake, line_items
from pearnly_erp_portal.masters import company as companies

def _linked(*pairs):
    return [{{"idmenu": code, "label": label, "linked": True}} for code, label in pairs]

frappe.set_user("Administrator")
conn = frappe.get_doc({{"doctype": "ERP MRERP Connection", "system_url": {URL!r}, "enabled": 1,
    "catalog_json": json.dumps([{{"source_id": {SET!r}, "label": "FLOWBOOK", "attributes": {{
        "comidyear": {SET.split(":")[0]!r}, "seldb": "1", "company": "FLOW", "company_name": "บริษัท โฟลว์ จำกัด",
        "period_start": "2023-01-01", "period_end": "2027-12-31", "tax_id": {BOOK_TAX!r}}}}}]),
    "menu_options": json.dumps({{{SET!r}: {{"pages": {{
        "impaptran": _linked(("68", "ซื้อ"), ("454", "ซื้อ-ค่าใช้จ่าย")),
        "impartran": _linked(("119", "ขายเชื่อ"), ("120", "ขายเชื่อ-บริการ")),
        "imparse": _linked(("126", "ใบขายเงินสด"), ("127", "ใบขายเงินสด 2"))}},
        "observed_at": frappe.utils.now_datetime().isoformat(), "error": None, "error_at": None}}}}),
    }}).insert(ignore_permissions=True)
frappe.db.commit()
frappe.db.set_value("User", {USER!r}, "language", "zh")
frappe.set_user({USER!r})
co = companies.create_company(company_name="Flow MR " + TAG, tax_id={OWN_TAX!r}, payment_methods=["cash"])["name"]
frappe.db.commit()
print(json.dumps({{"company": co, "connection": conn.name}}))
'''

SEED_REST = f'''
import base64, hashlib
from pearnly_core.assistant import sessions
from pearnly_erp_portal.integrations import intake, line_items
frappe.set_user({USER!r})
prod = frappe.get_doc({{"doctype": "ERP Product", "product_name": "FLOW ITEM " + TAG, "base_unit": "ชิ้น", "is_active": 1,
                       "express_company": co, "express_code": "FL" + TAG.upper(), "origin": "mrerp", "ledger": "mrerp"}})
prod.flags.express_sync = True
prod.insert(ignore_permissions=True)
frappe.db.commit()
made = sessions.create(project=co, period=frappe.utils.today()[:7], mode="chat")
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": made["id"]}}, "title", TAG)
pixel = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
file = frappe.get_doc({{"doctype": "File", "file_name": TAG + ".png", "is_private": 1, "content": pixel}}).insert()
day = frappe.utils.today()
lines = [{{"item_type": "goods", "description": "ของ " + TAG, "qty": 2, "unit_price": 100, "vat_rate": 7}}]
if DIRECTION == "purchase":
    result = {{"doc_no": TAG.upper() + "-1", "doc_date": day, "doc_kind": "purchase_invoice", "has_vat": 1,
              "supplier_name": "flow mr supplier " + TAG, "lines": lines, "printed": {{"total": "214.00"}}}}
else:
    result = {{"doc_no": TAG.upper() + "-1", "doc_date": day, "doc_kind": "receipt", "has_vat": 1, "vat_rate": 7,
              "buyer_name": "flow mr buyer " + TAG, "party_type": "company", "lines": lines, "printed": {{"total": "214.00"}}}}
doc = frappe.get_doc({{"doctype": intake.DOCTYPE, "status": intake.READY, "direction": DIRECTION, "owner": {USER!r},
                      "sent_by": {USER!r}, "source": intake.SOURCE_CC, "session_id": made["id"], "request_key": "flow-" + TAG,
                      "language": "zh", "media_type": "image", "company": co, "party": "@doc", "is_paid": 1,
                      "payment_method": "cash", "source_file": file.file_url, "source_file_name": file.name,
                      "file_hash": hashlib.sha256(pixel).hexdigest(), "received_at": frappe.utils.now(),
                      "result": json.dumps(result, ensure_ascii=False)}}).insert(ignore_permissions=True)
value = line_items.prepare(doc, result)
value["lines"] = [{{**line, "item_type": "goods", "product": prod.name, "new": False, "decided": True}} for line in value["lines"]]
value["warehouse"] = value.get("warehouse") or frappe.get_all("ERP Warehouse", filters={{"company": co, "is_active": 1}},
                                                              pluck="name", limit_page_length=1)[0]
frappe.db.set_value(intake.DOCTYPE, doc.name, "line_choices", json.dumps(value))
frappe.db.commit()
'''


def prepare(lab, manifest, business):
    tag = "gmr" + uuid.uuid4().hex[:6]
    site = manifest["site"]
    try:
        made = lab.python(site, f"TAG, DIRECTION = {tag!r}, {business!r}\n" + SEED)
        # 公司接上这条 MR.ERP 连接与账套：经 lab.set_fields 记原值再改（格结束时统一还原，test_flows_run CompanyFields）
        lab.set_fields(site, "ERP Company", made["company"], {
            "mrerp_enabled": 1, "express_enabled": 0, "mrerp_connection": made["connection"], "mrerp_account_set": SET,
            "mrerp_company": "tax:" + BOOK_TAX, "mrerp_linked_at": _five_minutes_ago(lab, site)})
        out = lab.python(site, f"TAG, DIRECTION, co, conn_name = {tag!r}, {business!r}, {made['company']!r}, {made['connection']!r}\n"
                         + SEED_REST + """
from pearnly_core.assistant.guide import run
try:
    found = run.start(made["id"], DIRECTION, doc_ids=["I:" + doc.name], at="asking")
    frappe.db.commit()
    started = {"run": found["run"], "status": found["status"], "stage": found.get("stage") or ""}
except Exception as error:
    frappe.db.rollback()
    started = {"run": "", "error": repr(error)[:300]}
print(json.dumps({"company": co, "connection": conn_name, "product": prod.name, "session": made["id"], "intake": doc.name,
                  "started": started}, default=str))
""")
    except Exception:
        cleanup(lab, manifest, {"tag": tag, "business": business})
        raise
    return {**out, "tag": tag, "business": business, "direction": business, "doc_id": "I:" + out["intake"],
            "run": (out.get("started") or {}).get("run") or "", "cases": []}


def _five_minutes_ago(lab, site) -> str:
    return lab.python(site, 'print(json.dumps({"at": str(frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-5))}))')["at"]


steps = direct.steps


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    asked, review, done = (by.get(k) or {} for k in ("题卡", "复核", "确定"))
    business, doc = params["business"], params["doc_id"]
    rows = done.get("rows") or []
    return [
        {"case": f"直写对话里开出来的是{WORD[business]}引导卡、不是代录卡；题卡问到这份 MR.ERP 账套的导入菜单，逐题点完没有卡住"
                 "（修好前这张票判「Express 未连接」开不出可写的卡）",
         "seen": {"started": params.get("started"), **asked},
         "ok": asked.get("business") == business and not asked.get("operator") and not asked.get("stuck")
         and any(" · " in (item.get("picked") or "") and item["picked"].split(" · ")[0].isdigit() for item in asked.get("asked") or [])},
        {"case": "复核卡：只有这一张票、不挡写入，确定那一下能点", "seen": review,
         "ok": bool(review.get("review")) and [d.get("doc") for d in review.get("docs") or []] == [doc]
         and not any(d.get("blocking") for d in review.get("docs") or []) and bool(review.get("enabled"))},
        {"case": "点确定：结果卡列这一张（没有凭据登录不了 MR.ERP，不说已写入）", "seen": done,
         "ok": bool(done.get("clicked")) and done.get("status") == "done" and [r.get("doc") for r in rows] == [doc]
         and all(r.get("status") not in ("entered", "withdrawn", "") for r in rows)},
    ]


def verify(lab, manifest, params, camera_code=None):
    doctype = DOCTYPES[params["business"]]
    got = lab.python(manifest["site"], f"""
plans = frappe.get_all("Pearnly Assistant Write Plan", filters={{"session_id": {params['session']!r}}},
                       fields=["name", "consent", "status", "mode"], order_by="creation asc", limit_page_length=10)
guide = frappe.db.get_value("Pearnly Assistant Guide Run", {params['run']!r}, ["status", "consent", "business"], as_dict=True) or {{}}
record = frappe.db.get_value("ERP LINE Intake", {params['intake']!r}, ["status", "purchase", "sales_document"], as_dict=True) or {{}}
made = record.get("purchase") if {params['business']!r} == "purchase" else record.get("sales_document")
doc = frappe.db.get_value({doctype!r}, made, ["source", "company", "status", "ledger_state", "ledger_target"], as_dict=True) \\
    if made else None
menus = json.loads(frappe.db.get_value("ERP Company", {params['company']!r}, "mrerp_menus") or "{{}}")
print(json.dumps({{"plans": plans, "guide": guide, "record": record, "made": made, "doc": doc, "menus": menus}}, default=str))
""")
    plans, guide, doc = got.get("plans") or [], got.get("guide") or {}, got.get("doc") or {}
    consents = [str(plan.get("consent") or "") for plan in plans]
    return [
        {"case": "引导运行办完（done），同意是页面上这一下（guide:<运行>:<版本>）", "seen": guide,
         "ok": guide.get("status") == "done" and guide.get("business") == params["business"]
         and str(guide.get("consent") or "").startswith(f"guide:{params['run']}:")},
        {"case": "这段会话只有一份写入计划，是直写，同意与引导相同", "seen": plans,
         "ok": len(plans) == 1 and consents[0] == str(guide.get("consent") or "") and plans[0].get("mode") != "operator"},
        {"case": "收单记录用网页同一个入账函数提交成这家公司的 ERP 单（来源 cc、已提交），去向 MR.ERP", "seen": got,
         "ok": (got.get("record") or {}).get("status") == "confirmed" and bool(got.get("made"))
         and doc.get("source") == "cc" and doc.get("company") == params["company"] and doc.get("status") == "submitted"
         and doc.get("ledger_target") == "mrerp"},
        {"case": "卡上答的导入菜单记在这家公司这份账套名下（同网页记法，下一张不再问）", "seen": got.get("menus"),
         "ok": any(kind in ((got.get("menus") or {}).get(SET) or {}) for kind in KINDS[params["business"]])},
    ]


def cleanup(lab, manifest, params, attempts=4):
    """作废提交出的单（后台写账任务可能正锁着，等一下再来，同 `erp_cc_guide_direct_lib.cleanup`），再删会话、收单记录、物料、公司与连接。"""
    tag, business, intake = params.get("tag") or "", params.get("business") or "purchase", params.get("intake") or ""
    for attempt in range(attempts):
        try:
            made = lab.python(manifest["site"], f"""
frappe.set_user("Administrator")
names = [{intake!r}] if {intake!r} else frappe.get_all("ERP LINE Intake", filters={{"request_key": "flow-" + {tag!r}}}, pluck="name",
                                                   limit_page_length=5)
rows = [frappe.db.get_value("ERP LINE Intake", name, ["purchase", "sales_document"], as_dict=True) or {{}} for name in names]
made = [row.get("purchase") if {business!r} == "purchase" else row.get("sales_document") for row in rows]
print(json.dumps({{"made": next((m for m in made if m), None), "names": names}}))
""")
            if made.get("made"):
                void_documents(lab, manifest["site"], [made["made"]], doctype=DOCTYPES[business])
            break
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(15)
    lab.python(manifest["site"], f"""
from pearnly_core.assistant import sessions
frappe.set_user({USER!r})
for sid in frappe.get_all("Pearnly Assistant Session", filters={{"user": {USER!r}, "title": {tag!r}}}, pluck="session_id",
                          limit_page_length=5):
    sessions.delete(sid)
frappe.set_user("Administrator")
for name in frappe.get_all("ERP LINE Intake", filters={{"request_key": "flow-" + {tag!r}}}, pluck="name", limit_page_length=5):
    frappe.delete_doc("ERP LINE Intake", name, ignore_permissions=True, force=True)
cos = frappe.get_all("ERP Company", filters={{"company_name": "Flow MR " + {tag!r}}}, pluck="name", limit_page_length=5)
conns = [c for c in frappe.get_all("ERP Company", filters={{"name": ["in", cos or [""]]}}, pluck="mrerp_connection") if c]
frappe.db.delete("ERP Product", {{"express_company": ["in", cos or [""]]}})
for doctype in ("ERP Warehouse", "ERP POS Terminal"):
    frappe.db.delete(doctype, {{"company": ["in", cos or [""]]}})
frappe.db.delete("ERP Company", {{"name": ["in", cos or [""]]}})
frappe.db.delete("ERP MRERP Connection", {{"name": ["in", conns or [""]], "system_url": {URL!r}}})
frappe.db.commit()
print("{{}}")
""")
