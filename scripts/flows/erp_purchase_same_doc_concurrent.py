"""流程用例：同一张采购单被几个人同时操作（付款、作废、出凭证）不报冲突、不丢付款（11-26 关快照隔离后的并发，主会话派）。

MariaDB 关了快照隔离（11-23）之后，「先普通读 → 加锁 → 普通 get_doc → save」读到读视图里的旧副本，保存前比对报
``TimestampMismatchError``（417）。11-26 起加锁之后一律加锁读（`docs/work/2026-11-26-lock-then-read.md`）。这一格在采购记录页里同时发：
- 4 张已提交的单，每张 3 笔各 100 的部分付款同时到；
- 2 张单，每张同时到「作废」「付款 100」「补开收据」。
一共 18 个请求。
- 页面上：没有一个回 ``TimestampMismatchError`` / ``QueryDeadlockError`` / 500；只付款的 12 笔全部成功；第二组只允许按业务规则拒绝
  （先作废了再付款 / 出凭证：「Document must be submitted」）。
- 库里：只付款的 4 张每张正好 3 行付款、已付 300（没有被旧副本覆盖丢掉一笔）；第二组每张已付金额 = 付款行合计，状态是已提交或已作废（作废追加一行负数冲回）。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, company, js_value, void_documents  # noqa: E402

TITLE = "ERP 同一张采购单同时付款 / 作废 / 出凭证：不报冲突、不丢付款"
USER = "purchase@erp.regression"
PAY_ONLY, MIXED, PAYMENTS = 4, 2, 3
CONFLICTS = ("TimestampMismatchError", "QueryDeadlockError", "QueryTimeoutError")


def prepare(lab, manifest):
    co = company(manifest)
    tag = uuid.uuid4().hex[:6]
    # 这一格关掉「接 Express」，单子不排进账本；格结束时 run_flow 还原
    lab.set_fields(manifest["site"], "ERP Company", co, {"express_enabled": 0})
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.purchasing import entry, suppliers
frappe.set_user({USER!r})
companies.set_active_company({co!r})
supplier = frappe.db.get_value("ERP Supplier", {{"supplier_name": "ผู้ขายโฟลว์ปุ่ม"}}) or \\
    suppliers.create_supplier(supplier_name="ผู้ขายโฟลว์ปุ่ม")["name"]
frappe.db.commit()
docs = []
for i in range({PAY_ONLY + MIXED}):
    made = entry.post({{"company": {co!r}, "supplier": supplier, "doc_date": frappe.utils.nowdate(), "currency": "THB", "fx_rate": 1,
                       "has_vat": 1, "price_mode": "exclusive", "doc_kind": "purchase_invoice", "doc_no": f"FLOW-SDC-{tag}-{{i}}",
                       "is_paid": 0, "update_stock": 0,
                       "lines": [{{"item_type": "service", "description": f"flow same doc {tag} {{i}}", "qty": 1, "unit": "งาน",
                                  "unit_price": 1000, "vat_rate": 7, "wht_rate": 3}}]}}, request_id=f"flow-sdc-{tag}-{{i}}")
    frappe.db.commit()
    docs.append(frappe.db.get_value("Purchase Document", made["name"], ["name", "system_no", "status"], as_dict=True))
print(json.dumps({{"docs": docs}}, default=str))
""")
    return {"company": co, "docs": out["docs"]}


def cleanup(lab, manifest, params):
    """这一遍开的单：作废（成功、失败、超时都跑），不积成这个月等着录的单。"""
    void_documents(lab, manifest["site"], [row["name"] for row in params["docs"]])


def _jobs(names):
    jobs = []
    for name in names[:PAY_ONLY]:
        jobs += [["pay", name, i] for i in range(PAYMENTS)]
    for name in names[PAY_ONLY:]:
        jobs += [["cancel", name, 0], ["pay", name, 0], ["voucher", name, 0]]
    return jobs


def steps(params, camera_code=None):
    jobs = _jobs([doc["name"] for doc in params["docs"]])
    return [{"name": "同时操作同一张单", "js": JS + f"""
await until(()=>document.querySelector('.result'), 25000); await idle();
const jobs={js_value(jobs)};
const post=async(method, body)=>{{
  const r=await fetch('/api/method/pearnly_erp_portal.purchasing.api.'+method, {{method: 'POST', credentials: 'include',
    headers: {{'X-Frappe-CSRF-Token': frappe.csrf_token, 'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}},
    body: new URLSearchParams(body)}});
  let data={{}}; try {{ data=await r.json(); }} catch (e) {{}}
  let text=''; try {{ text=JSON.parse(JSON.parse(data._server_messages||'[]')[0]||'{{}}').message||''; }} catch (e) {{}}
  return {{status: r.status, exc: data.exc_type||'', text}};
}};
const one=async([kind, name, i])=>{{
  const body = kind==='pay' ? {{name, payload: JSON.stringify({{amount: 100, method: 'cash', note: 'flow '+i}})}}
    : kind==='cancel' ? {{name, reason: 'flow same doc'}} : {{name, kind: 'substitute_receipt'}};
  const method = kind==='pay' ? 'api_purchase_payment' : kind==='cancel' ? 'api_cancel_purchase' : 'api_purchase_voucher';
  return {{kind, name, ...(await post(method, body))}};
}};
return await Promise.all(jobs.map(one));"""}]


def judge(params, results, camera_code=None):
    rows = next((r.get("value") for r in results if r.get("step") == "同时操作同一张单"), None) or []
    names = [doc["name"] for doc in params["docs"]]
    conflicts = [row for row in rows if row.get("exc") in CONFLICTS or row.get("status", 0) >= 500]
    pay_only = [row for row in rows if row.get("name") in names[:PAY_ONLY]]
    mixed_refused = [row for row in rows if row.get("name") in names[PAY_ONLY:] and row.get("status") != 200]
    return [
        {"case": "all 18 requests came back; none is a database / save conflict or a 5xx", "seen": conflicts or len(rows),
         "ok": len(rows) == len(_jobs(names)) and not conflicts},
        {"case": "the 12 concurrent partial payments on the pay-only purchases all succeed", "seen": [r for r in pay_only if r.get("status") != 200],
         "ok": len(pay_only) == PAY_ONLY * PAYMENTS and all(r.get("status") == 200 for r in pay_only)},
        {"case": "on the purchases cancelled at the same time, a refusal is only the business rule (not submitted any more)",
         "seen": mixed_refused, "ok": all(r.get("exc") == "ValidationError" and r.get("kind") in ("pay", "voucher") for r in mixed_refused)},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
out = []
for name in {[doc["name"] for doc in params["docs"]]!r}:
    doc = frappe.get_doc("Purchase Document", name)
    out.append({{"name": name, "status": doc.status, "paid": str(doc.paid_amount or 0),
                "rows": [str(row.amount) for row in doc.payments], "system_no": doc.system_no}})
print(json.dumps(out, default=str))
""")
    from decimal import Decimal

    names = [doc["name"] for doc in params["docs"]]
    pay_only = [d for d in got if d["name"] in names[:PAY_ONLY]]
    mixed = [d for d in got if d["name"] in names[PAY_ONLY:]]
    before = {doc["name"]: doc for doc in params["docs"]}
    return [
        {"case": "each pay-only purchase has exactly 3 payment rows and 300 paid (no payment lost to a stale copy)",
         "saved": [[d["name"], d["rows"], d["paid"]] for d in pay_only],
         "ok": all(len(d["rows"]) == PAYMENTS and Decimal(d["paid"]) == Decimal(100 * PAYMENTS) for d in pay_only)},
        {"case": "on the mixed purchases the paid amount equals the payment rows and the status is submitted or void (a void adds the reversal row)",
         "saved": [[d["name"], d["status"], d["rows"], d["paid"]] for d in mixed],
         "ok": all(d["status"] in ("submitted", "void") and Decimal(d["paid"]) == sum((Decimal(a) for a in d["rows"]), Decimal(0))
                   for d in mixed)},
        {"case": "document numbers are unchanged", "saved": [[d["name"], d["system_no"]] for d in got],
         "ok": all(d["system_no"] == before[d["name"]]["system_no"] for d in got)},
    ]
