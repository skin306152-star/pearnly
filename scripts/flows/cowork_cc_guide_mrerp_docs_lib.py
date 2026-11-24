"""Cowork 版 CC 采购 / 销售引导在**接 MR.ERP 的公司**的直写对话里（总表「采购 / 销售 · Cowork 直写 · MR.ERP」）的共用片段：
`cowork_cc_guide_mrerp_purchase.py`、`cowork_cc_guide_mrerp_sales.py` 各调一次，只差业务。

造数据：`cowork_mrerp_lib.SEED`（编造的 MR.ERP 连接与账套目录、从目录建的公司，不带登录凭据）→ 用产品自己的
``connections.observe_mrerp_menus``（推送与读菜单任务存读到的导入页选项那一处）给这份账套记下每个导入页两个已关联日记账的菜单
——这正是 11-24 线上缺陷的条件：多个已关联菜单、还没选过，推送停在「先选导入菜单」，判定卡原来不出题。再造一张本业务识别完成的税票
（推送目标是这家公司的 MR.ERP 连接与账套），用 documents 源的公开入口 ``run.start(doc_ids=[单据], at="asking")`` 在这家、本月、
这份账套的直写对话里开这次引导（实验栈没有识别进程，同 `cowork_cc_guide_docs_lib`）。

之后全在页面上点卡（步骤同 `cowork_cc_guide_docs_lib.steps`，不调接口、不经模型）：题卡上出导入菜单题（选项「代码 · 名称」），逐题点第一项
→ 复核卡 →「立即录入」→ 结果卡。没有凭据，推送的后台任务登录不了 MR.ERP，结果卡上这张是「已交出 / 未完成」而不是已写入。
库里：引导运行 done、同意是页面上这一下；卡上答的菜单记在这份账套名下（``mrerp_menus``，同网页弹窗记法）；这张票推向这份账套、有推送记录。
"""
from __future__ import annotations

import json
import uuid

import cowork_cc_guide_docs_lib as docs
import cowork_mrerp_lib as mr

USER = mr.USER
WORD = {"purchase": "采购", "sales": "销售"}
OTHER_TAX = "0105561000046"   # 编造（过校验位）
# 本业务票面方向会用到的导入页（`pearnly_core.mrerp.menus.KINDS_FOR_DIRECTION` 对应的页）
PAGES = {"purchase": ("impaptran",), "sales": ("impartran", "imparse")}
KINDS = {"purchase": ("purchase", "purchase_expense"), "sales": ("sales_credit", "sales_cash")}

MENUS = f'''
_two = lambda a, b: [{{"idmenu": a[0], "label": a[1], "linked": True}}, {{"idmenu": b[0], "label": b[1], "linked": True}}]
mr_connections.observe_mrerp_menus(mr_conn, {mr.SET!r}, {{"impaptran": _two(("68", "ซื้อ"), ("454", "ซื้อ-ค่าใช้จ่าย")),
    "impartran": _two(("70", "ขายเชื่อ"), ("71", "ขายเชื่อ 2")), "imparse": _two(("80", "ขายสด"), ("81", "ขายสด 2"))}})
frappe.db.commit()
'''


def prepare(lab, manifest, business):
    site = manifest["site"]
    lab.python(site, f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    tag = "gm" + uuid.uuid4().hex[:6]
    number = "FLOW-GM-" + uuid.uuid4().hex[:6].upper()
    try:
        out = lab.python(site, mr.SEED + MENUS + f"""
from uuid import uuid4
from pearnly_cowork.history import storage
from pearnly_cowork.companies import service as company_service
from pearnly_core.assistant import sessions
from pearnly_core.assistant.guide import run
mine = {{"tax": {mr.BOOK_TAX!r}, "name": "บริษัท โฟลว์ จำกัด"}}
other = {{"tax": {OTHER_TAX!r}, "name": "Flow party " + {number!r}}}
seller, buyer = (other, mine) if {business!r} == "purchase" else (mine, other)
day = frappe.utils.today()
fields = {{"direction": {business!r}, "seller_name": seller["name"], "seller_tax": seller["tax"], "buyer_tax": buyer["tax"],
          "buyer_name": buyer["name"], "subtotal": "100", "vat": "7", "total_amount": "107", "date": day,
          "document_type": "tax_invoice", "invoice_number": {number!r}}}
pages = [{{"page_index": 1, "fields": fields}}]
made = storage.create_recognized(_ws, pages, uuid4().hex, source="manual", confidence="high", ai_raw=pages)
frappe.db.set_value("Cowork OCR History", made["name"], {{"push_connection": mr_conn, "push_account_set": {mr.SET!r}}})
frappe.db.commit()
books = company_service.listed_books(company_service.get(mr_company), None)
s = sessions.create(project=mr_company, period=day[:7], ledger=books[0]["identity"])
sid = s["id"]
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": sid}}, "title", {tag!r})
frappe.db.commit()
try:
    started = run.start(sid, {business!r}, doc_ids=[made["name"]], at="asking")
    frappe.db.commit()
    started = {{"run": started["run"], "status": started["status"], "stage": started.get("stage") or ""}}
except Exception as error:
    frappe.db.rollback()
    started = {{"run": "", "error": repr(error)[:300]}}
print(json.dumps({{"company": mr_company, "connection": mr_conn, "doc": made["name"], "session": sid, "started": started}},
                 default=str))
""")
    except Exception:
        lab.python(site, mr.CLEANUP + 'mr_cleanup()\nprint("{}")')
        raise
    return {**out, "business": business, "number": number, "tag": tag,
            "run": (out.get("started") or {}).get("run") or "", "cases": []}


steps = docs.steps


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    asked, review, done = (by.get(k) or {} for k in ("题卡", "复核", "确定"))
    business, doc = params["business"], params["doc"]
    picked = [item.get("picked") or "" for item in asked.get("asked") or []]
    rows = done.get("rows") or []
    return [
        {"case": f"开出来的是{WORD[business]}引导卡，题卡上问到这份 MR.ERP 账套的导入菜单（选项「代码 · 名称」），逐题点完没有卡住",
         "seen": {"started": params.get("started"), **asked},
         "ok": asked.get("business") == business and not asked.get("stuck")
         and any(" · " in name and name.split(" · ")[0].isdigit() for name in picked)},
        {"case": "复核卡：只有这一张票、不挡写入，票面带单号与合计 107.00，「立即录入」能点", "seen": review,
         "ok": bool(review.get("review")) and [d.get("doc") for d in review.get("docs") or []] == [doc]
         and not any(d.get("blocking") for d in review.get("docs") or []) and params["number"] in review.get("text", "")
         and "107" in review.get("text", "") and bool(review.get("enabled"))},
        {"case": "点「立即录入」：结果卡有这一张（没有凭据登录不了 MR.ERP，不说已写入）", "seen": done,
         "ok": bool(done.get("clicked")) and done.get("status") == "done" and [r.get("doc") for r in rows] == [doc]
         and all(r.get("status") != "entered" for r in rows)},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], f"""
runs = frappe.get_all("Pearnly Assistant Guide Run", filters={{"name": {params['run']!r}}}, fields=["status", "consent", "business"],
                      limit_page_length=1)
doc = frappe.db.get_value("Cowork OCR History", {params['doc']!r}, ["push_connection", "push_account_set"], as_dict=True) or {{}}
records = frappe.get_all("Cowork Push Record", filters={{"history": {params['doc']!r}}}, fields=["account_key", "status"],
                         limit_page_length=10)
menus = json.loads(frappe.db.get_value("Cowork ERP Connection", {params['connection']!r}, "mrerp_menus") or "{{}}")
print(json.dumps({{"runs": runs, "doc": doc, "records": records, "menus": menus}}, default=str))
""")
    run = (out.get("runs") or [{}])[0]
    doc, records, menus = out.get("doc") or {}, out.get("records") or [], out.get("menus") or {}
    chosen = next((v for k, v in menus.items() if str(k).startswith(mr.SET.split(":")[0])), {}) or {}
    return [
        {"case": "引导运行办完（done），同意是页面上这一下（guide:<运行>:<版本>）", "seen": run,
         "ok": run.get("status") == "done" and run.get("business") == params["business"]
         and str(run.get("consent") or "").startswith("guide:" + params["run"] + ":")},
        {"case": "卡上答的导入菜单记在这份 MR.ERP 账套名下（同网页弹窗记法，下一张不再问）", "seen": menus,
         "ok": any(kind in chosen for kind in KINDS[params["business"]])},
        {"case": "这张票推向这份 MR.ERP 账套，有推送记录", "seen": {"doc": doc, "records": records},
         "ok": doc.get("push_connection") == params["connection"] and str(doc.get("push_account_set")) == mr.SET
         and bool(records)},
    ]


def cleanup(lab, manifest, params):
    company = params.get("company") or "none"
    lab.python(manifest["site"], mr.CLEANUP + f"""
from pearnly_core.assistant import sessions
frappe.set_user({USER!r})
for sid in frappe.get_all("Pearnly Assistant Session", filters={{"user": {USER!r}, "project": {company!r}}},
                          pluck="session_id", limit_page_length=20):
    sessions.delete(sid)
mr_cleanup()
frappe.db.delete("Cowork Push Writer Lock", {{"account_key": "", "holder": ["is", "not set"]}})
frappe.db.commit()
print(json.dumps({{"ok": True}}))
""")
