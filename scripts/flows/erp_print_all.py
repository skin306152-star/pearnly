"""流程用例：ERP 打印（打印第一、二批，fcb65cca 逐入口实测）—— 每类单据取库里最新一张，打开原生打印页：200、出版面标题、没有模板报错。

入库单（收货单）、出库单（领料单）、入了库的采购单（收货单）、销售单、盘点单、收银小票、交班汇总。某类单据这套数据里还没有就记「没有这类单」，
不算通过也不算不通过；但每套数据至少要打出一张。库里：打出的那张就是这类单据最新的一张、确实存在。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, js_value  # noqa: E402

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
    checks = [{"case": f"{r['doctype']} · {r['format']}", "seen": r, "ok": r.get("skipped") or (r.get("status") == 200 and r.get("title"))}
              for r in rows]
    checks.append({"case": "至少打出一张", "seen": len(params["printed"]), "ok": bool(params["printed"])})
    return checks


def verify(lab, manifest, params, camera_code=None):
    names = [[r["doctype"], r["name"]] for r in params.get("printed", [])]
    found = lab.python(manifest["site"], f"print(json.dumps([bool(frappe.db.exists(d, n)) for d, n in {names!r}]))")
    return [{"case": "打出的单据库里都在", "seen": names, "ok": all(found) and len(found) == len(names)}]
