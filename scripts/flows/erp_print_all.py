"""流程用例：ERP 打印（打印第一、二批，fcb65cca 逐入口实测）—— 每类单据取库里最新一张，打开原生打印页：200、出版面标题、没有模板报错。

入库单（收货单）、出库单（领料单）、入了库的采购单（收货单）、销售单、盘点单、收银小票、交班汇总。七类单据先由 `erp_lib.ensure_documents`
保证各有一张（不靠别的流程先建单），每类都要打出来。库里：打出的那张就是这类单据最新的一张、确实存在。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, ensure_documents, js_value  # noqa: E402

TITLE = "ERP 打印：七类单据原生打印页"
TARGETS = [
    ["ERP Stock Entry", {"direction": "in", "status": "submitted"}, "Pearnly Goods Receipt Note", "ใบรับสินค้า|GOODS RECEIPT"],
    ["ERP Stock Entry", {"direction": "out", "status": "submitted"}, "Pearnly Goods Issue Slip", "ใบเบิกสินค้า|GOODS ISSUE"],
    ["Purchase Document", {"status": "submitted", "stock_posted_at": ["is", "set"]}, "Pearnly Purchase Goods Receipt", "ใบรับสินค้า|GOODS RECEIPT"],
    ["ERP Sales Document", {"status": "submitted", "doc_kind": "receipt", "source": ["!=", "pos"]}, "Pearnly Sales Document",
     "ใบเสร็จรับเงิน|RECEIPT|ใบส่งของ|DELIVERY"],
    ["ERP Stock Count", {}, "Pearnly Stock Count", "ใบตรวจนับ|STOCK COUNT"],
    ["ERP Sale", {}, "Pearnly POS Receipt", "ใบเสร็จรับเงิน|RECEIPT|ใบรับคืน|RETURN"],
    ["ERP Shift", {"status": "closed"}, "Pearnly Shift Summary", "สรุปการปิดกะ|SHIFT SUMMARY"],
]


def prepare(lab, manifest):
    ensure_documents(lab, manifest)
    return {"targets": TARGETS}


def steps(params, camera_code=None):
    return [{"name": "打印七类单据", "js": JS + f"""
const targets={js_value(params['targets'])}; const out=[];
for (const [doctype, filters, format, title] of targets) {{
  const doc=((await call('frappe.client.get_list', {{doctype, filters, fields: ['name'], order_by: 'creation desc', limit_page_length: 1}}, 'GET'))||[])[0];
  if (!doc) {{ out.push({{doctype, format, skipped: true}}); continue; }}
  const res=await fetch('/printview?doctype='+encodeURIComponent(doctype)+'&name='+encodeURIComponent(doc.name)+'&format='+encodeURIComponent(format)+'&no_letterhead=1', {{credentials: 'include'}});
  const html=await res.text(); const hit=new RegExp(title).test(html);
  out.push({{doctype, format, name: doc.name, status: res.status, title: hit, template_error: !hit && /Jinja|Traceback/.test(html)}});
}}
return out;"""}]


def judge(params, results, camera_code=None):
    rows = next((r.get("value") for r in results if r.get("step") == "打印七类单据"), None) or []
    params["printed"] = [r for r in rows if not r.get("skipped")]
    checks = [{"case": f"{r['doctype']} · {r['format']}", "seen": r, "ok": not r.get("skipped") and r.get("status") == 200 and bool(r.get("title"))}
              for r in rows]
    checks.append({"case": "七类都打出", "seen": len(params["printed"]), "ok": len(params["printed"]) == len(params["targets"])})
    return checks


def verify(lab, manifest, params, camera_code=None):
    names = [[r["doctype"], r["name"]] for r in params.get("printed", [])]
    found = lab.python(manifest["site"], f"print(json.dumps([bool(frappe.db.exists(d, n)) for d, n in {names!r}]))")
    return [{"case": "打出的单据库里都在", "seen": names, "ok": all(found) and len(found) == len(names)}]
