"""流程用例：好几个人同时给采购单出凭证（补开收据 / 预扣税凭证）不报错、不挂重复（11-08 PDF 并发评估「另外发现」，主会话审定）。

采购岗在采购记录页里同时发出凭证请求：8 张已提交、带预扣税的单 × 两种凭证，外加其中 2 张的补开收据各再点一次（同一份点两次），
一共 18 个请求一起出去（浏览器同一站点最多 6 条并发连接，网页进程里同时有好几份在挂附件）。
修之前：挂附件时两笔事务争子表索引末尾的间隙锁，死锁 500（`QueryDeadlockError`）；同一网页进程两份 PDF 同时解析样式还会 `SyntaxErr` 500。
- 页面上：18 个请求全部成功；同一张单同一种凭证点两次，两次拿到的是同一个文件。
- 库里：每张单每种凭证正好一份生成的附件、对应的 File 在、磁盘上有这个文件；没有挂着同名凭证却没人引用的多余文件；单据号、状态不变。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, company, js_value  # noqa: E402

TITLE = "ERP 采购凭证并发：8 张单 × 两种凭证 + 重复点，同时出、各挂一份"
USER = "purchase@erp.regression"
KINDS = ("substitute_receipt", "wht_cert")


def prepare(lab, manifest):
    co = company(manifest)
    tag = uuid.uuid4().hex[:6]
    # 这一格关掉「接 Express」，单子不排进账本；格结束时 run_flow 还原（`Lab.set_fields`，成功、失败、超时都还原）
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
for i in range(8):
    made = entry.post({{"company": {co!r}, "supplier": supplier, "doc_date": frappe.utils.nowdate(), "currency": "THB", "fx_rate": 1,
                       "has_vat": 1, "price_mode": "exclusive", "doc_kind": "purchase_invoice", "doc_no": f"FLOW-VCH-{tag}-{{i}}",
                       "is_paid": 0, "update_stock": 0,
                       "lines": [{{"item_type": "service", "description": f"flow voucher {tag} {{i}}", "qty": 1, "unit": "งาน",
                                  "unit_price": 1000, "vat_rate": 7, "wht_rate": 3}}]}}, request_id=f"flow-vch-{tag}-{{i}}")
    frappe.db.commit()
    docs.append(frappe.db.get_value("Purchase Document", made["name"], ["name", "system_no", "status"], as_dict=True))
print(json.dumps({{"docs": docs}}, default=str))
""")
    return {"company": co, "docs": out["docs"]}


def steps(params, camera_code=None):
    names = [doc["name"] for doc in params["docs"]]
    jobs = [[name, kind] for name in names for kind in KINDS] + [[names[0], KINDS[0]], [names[1], KINDS[0]]]
    return [{"name": "同时出凭证", "js": JS + f"""
await until(()=>document.querySelector('.result'), 25000); await idle();
const jobs={js_value(jobs)};
const one=async([name, kind])=>{{
  const r=await fetch('/api/method/pearnly_erp_portal.purchasing.api.api_purchase_voucher', {{method: 'POST', credentials: 'include',
    headers: {{'X-Frappe-CSRF-Token': frappe.csrf_token, 'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}},
    body: new URLSearchParams({{name, kind}})}});
  let body={{}}; try {{ body=await r.json(); }} catch (e) {{}}
  return {{name, kind, status: r.status, exc: body.exc_type||'', file: (body.message||{{}}).file||''}};
}};
return await Promise.all(jobs.map(one));"""}]


def judge(params, results, camera_code=None):
    rows = next((r.get("value") for r in results if r.get("step") == "同时出凭证"), None) or []
    params["answers"] = rows
    failed = [row for row in rows if row.get("status") != 200 or not row.get("file")]
    names = [doc["name"] for doc in params["docs"]]
    twice = {}
    for row in rows:
        if row.get("kind") == KINDS[0] and row.get("name") in names[:2]:
            twice.setdefault(row["name"], set()).add(row.get("file"))
    return [
        {"case": "all 18 voucher requests sent together come back with a file", "seen": failed or len(rows),
         "ok": len(rows) == 18 and not failed},
        {"case": "the same voucher clicked twice at once returns the same file", "seen": {k: sorted(v) for k, v in twice.items()},
         "ok": len(twice) == 2 and all(len(files) == 1 for files in twice.values())},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
import glob, os
out = []
for name in {[doc["name"] for doc in params["docs"]]!r}:
    doc = frappe.get_doc("Purchase Document", name)
    rows = [row for row in doc.attachments if row.generated]
    files = []
    for row in rows:
        f = frappe.db.get_value("File", row.file, ["name", "file_url", "attached_to_name"], as_dict=True)
        path = frappe.get_site_path("private", "files", os.path.basename(f.file_url)) if f else ""
        files.append({{"kind": row.kind, "file": row.file, "url": f.file_url if f else None, "attached_to": f.attached_to_name if f else None,
                      "on_disk": bool(path) and os.path.exists(path)}})
    on_disk = sorted(os.path.basename(p) for p in glob.glob(frappe.get_site_path("private", "files", name + "-*.pdf")))
    out.append({{"name": name, "system_no": doc.system_no, "status": doc.status, "kinds": sorted(row.kind for row in rows),
                "files": files, "on_disk": on_disk,
                "file_rows": frappe.db.count("File", {{"attached_to_doctype": "Purchase Document", "attached_to_name": name}})}})
print(json.dumps(out, default=str))
""")
    before = {doc["name"]: doc for doc in params["docs"]}
    return [
        {"case": "each purchase has exactly one generated voucher of each kind", "saved": [[d["name"], d["kinds"]] for d in got],
         "ok": all(d["kinds"] == sorted(KINDS) for d in got)},
        {"case": "every voucher file row exists, belongs to its purchase and is on disk", "saved": [d["files"] for d in got],
         "ok": all(f["url"] and f["attached_to"] == d["name"] and f["on_disk"] for d in got for f in d["files"])},
        {"case": "no extra File rows and no stray voucher PDFs on disk", "saved": [[d["name"], d["file_rows"], d["on_disk"]] for d in got],
         "ok": all(d["file_rows"] == 2 and len(d["on_disk"]) == 2 for d in got)},
        {"case": "document numbers and status are unchanged", "saved": [[d["name"], d["system_no"], d["status"]] for d in got],
         "ok": all(d["system_no"] == before[d["name"]]["system_no"] and d["status"] == before[d["name"]]["status"] for d in got)},
    ]
