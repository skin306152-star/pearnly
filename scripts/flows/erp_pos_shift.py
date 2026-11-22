"""流程用例：ERP 收银整班（收银岗位）—— 各种码在收银台查码认对商品 → 开班 → 卖一张（两件）→ 退一件 → 交班；收银记录、班次记录两页打得开
（fcb65cca 逐入口实测：收银小票逐行税 / 退货 / 日汇总改过的那条链）。

每套数据：数据清单里每一种认法的码在收银台查码要认出那件；开班备用金 500，卖 第一件 ×2 + 第二件 ×1（单价 100、现金 300），
退第一件 1 件（现金 100），当班汇总 1 笔销售 1 笔退货、应有现金 700；实点 700 交班。库里：这一班已交、实点 700；
收银台那个仓第一件的账面少 1（卖 2 退 1，新开户零库存时允许负数）。最后打开收银记录、班次记录两页，出列表或原生空状态。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, as_user, codes, company, js_value, stock_of  # noqa: E402

TITLE = "ERP 收银整班：查码、开班、卖、退、交班"
API = "pearnly_erp_portal.cashier.api."


def prepare(lab, manifest):
    """这家公司的收银台（新开户没有就按正式口径建默认那台，没挂仓就挂第一个仓），记下第一件在收银台仓的账面。"""
    co = company(manifest)
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.cashier import terminals
from pearnly_erp_portal.masters import company as companies
frappe.set_user("admin@erp.regression")
companies.set_active_company({co!r})
terminal = terminals.ensure_default(company={co!r})
if not frappe.db.get_value("ERP POS Terminal", terminal, "warehouse"):
    # 接 Express 的公司默认建出来的收银台不带仓：照用户在公司页「收银台」里那样给它挂上这家公司的第一个仓
    first = frappe.get_all("ERP Warehouse", filters={{"company": {co!r}, "is_active": 1}}, pluck="name", order_by="express_code asc, creation asc")[0]
    terminals.update_terminal(terminal, warehouse=first)
frappe.db.commit()
print(json.dumps({{"terminal": terminal, "warehouse": frappe.db.get_value("ERP POS Terminal", terminal, "warehouse")}}))
""")
    found = codes(manifest)
    items = list(found.values())
    return {"company": co, "terminal": out["terminal"], "warehouse": out["warehouse"], "codes": found,
            "first": items[0]["product"], "second": items[1]["product"],
            "before": stock_of(lab, manifest["site"], items[0]["product"], out["warehouse"])}


def steps(params, camera_code=None):
    terminal = js_value(params["terminal"])
    lookups = js_value({key: item["scan"] for key, item in params["codes"].items()})
    return [
        {"name": "收银台查码", "js": JS + f"""
const out={{}}; const codes={lookups};
for (const key of Object.keys(codes)) {{ const row=await call('{API}api_counter_lookup', {{value: codes[key], terminal: {terminal}}}, 'GET'); out[key]=row&&row.name; }}
return out;"""},
        {"name": "开班卖退交班", "js": JS + f"""
const current=await call('{API}api_current_shift', {{terminal: {terminal}}}, 'GET');
if (current && current.shift) await call('{API}api_close_shift', {{name: current.shift.name, counted_cash: current.expected_cash}});
const shift=(await call('{API}api_open_shift', {{terminal: {terminal}, opening_float: 500}})).name;
const sale=await call('{API}api_create_sale', {{terminal: {terminal}, shift, client_uuid: crypto.randomUUID(),
  lines: [{{product: {js_value(params['first'])}, qty: 2, unit_price: 100}}, {{product: {js_value(params['second'])}, qty: 1, unit_price: 100}}],
  payments: [{{method: 'cash', amount: 300}}]}});
const detail=await call('{API}api_get_sale', {{name: sale.name}}, 'GET');
const line=(detail.lines||[]).find(r=>r.product==={js_value(params['first'])});
const refund=await call('{API}api_refund_sale', {{name: sale.name, client_uuid: crypto.randomUUID(), reason: 'ชำรุด',
  lines: [{{original_line: line.name, qty: 1}}], payments: [{{method: 'cash', amount: line.line_total / line.qty}}]}});
const summary=await call('{API}api_shift_summary', {{name: shift}}, 'GET');
await call('{API}api_close_shift', {{name: shift, counted: {{cash: summary.expected_cash}}}});
return {{shift, sale: sale.name, refund: refund && refund.name, total: Number(detail.grand_total ?? detail.total),
  sales_count: summary.sales_count, refund_count: summary.refund_count, expected_cash: Number(summary.expected_cash)}};"""},
        {"name": "收银记录与班次记录", "js": JS + """
const out={};
for (const route of ['pearnly-pos-receipts', 'pearnly-pos-shifts']) {
  frappe.set_route(route);
  const page=await until(()=>{const el=pageOf(route); const t=el?el.innerText.trim():''; return el&&(t.length>20||t.includes(__('Nothing to show')))?el:null;}, 30000);
  out[route]={opened: !!page, not_permitted: !!(page&&page.querySelector('.message-page'))};
}
return out;"""},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    seen, shift, pages = by.get("收银台查码", {}), by.get("开班卖退交班", {}), by.get("收银记录与班次记录", {})
    checks = [{"case": f"查码 {key}", "seen": seen.get(key), "ok": seen.get(key) == item["product"]} for key, item in params["codes"].items()]
    checks.append({"case": "开班卖退交班", "seen": shift, "ok": shift.get("total") == 300 and bool(shift.get("refund"))
                   and shift.get("sales_count") == 1 and shift.get("refund_count") == 1 and shift.get("expected_cash") == 700})
    checks += [{"case": route, "seen": value, "ok": value.get("opened") and not value.get("not_permitted")} for route, value in pages.items()]
    if len(pages) != 2:
        checks.append({"case": "收银两页", "seen": pages, "ok": False})
    params["shift"] = shift.get("shift")
    return checks


def verify(lab, manifest, params, camera_code=None):
    shift = params.get("shift")
    row = lab.python(manifest["site"], f"""
print(json.dumps(frappe.db.get_value("ERP Shift", {shift!r}, ["status", "counted_cash", "expected_cash"], as_dict=True) or {{}}, default=str))
""") if shift else {}
    after = stock_of(lab, manifest["site"], params["first"], params["warehouse"])
    return [{"case": "班次已交、实点 700", "seen": row, "ok": row.get("status") == "closed" and float(row.get("counted_cash") or 0) == 700},
            {"case": "收银台仓账面卖 2 退 1", "seen": {"before": params["before"], "after": after}, "ok": after == params["before"] - 1},
            _receipt_ledger_keys(lab, manifest)]


def _receipt_ledger_keys(lab, manifest):
    """收银记录列表的「Express」一栏取日汇总的通用账本栏（评估 2026-11-02 §12 第 6 条）：行上是 ledger_state / ledger_docnum，
    没有旧的 Express 状态 / 单号键（小票自己的日汇总、补单、待写退货三个键照旧）；日汇总 DocType 里也没有旧栏。"""
    got = as_user(lab, manifest["site"], "cashier@erp.regression", """
from pearnly_erp_portal.cashier import api
rows = api.api_list_sales(start=0, limit=5)
keys = sorted(set().union(*[set(row) for row in rows])) if rows else []
old = [f.fieldname for f in frappe.get_meta("ERP POS Day Summary").fields if f.fieldname.startswith("express_")]
print(json.dumps({"rows": len(rows), "ledger": [k for k in keys if k.startswith("ledger_")],
                  "old": [k for k in keys if k.startswith("express_")
                          and k not in ("express_summary", "express_extra", "express_pending_refund")] + old}))
""")
    return {"case": "收银记录行带通用账本栏", "seen": got,
            # 行里带通用账本的状态与单号（界面仍是原来那一栏）；原因可以一起回（11-22：状态词按状态 + 原因算），不按字段个数判
            "ok": got["rows"] > 0 and {"ledger_docnum", "ledger_state"} <= set(got["ledger"])
            <= {"ledger_docnum", "ledger_reason", "ledger_state"} and not got["old"]}
