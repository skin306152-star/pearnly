"""流程用例：ERP 采购 / 销售单写进 MR.ERP 结果不明，单据页 MR.ERP 弹窗「已在 MR.ERP 核对」（用户 11-28 批准）。
采购单选「已入账」填 MR.ERP 单号 → 状态「成功」、单号是填的那个；销售单选「未入账」→ 状态「失败」、原因「已核对，未入账」、
弹窗换成「重试」。库里两张账本栏照此、各一条核对评论。"""
from __future__ import annotations

import json

import erp_mrerp_unknown_lib as lib
from desk_list import ACTION, IDLE

TITLE = "ERP 单据写进 MR.ERP 结果不明：弹窗「已在 MR.ERP 核对」已入账 → 成功 + 单号；未入账 → 失败 · 已核对，未入账、可重试"
USER = lib.USER
DOCNUM = "PG691128-FLOW01"


def prepare(lab, manifest):
    return lib.prepare(lab, manifest, purchases=1, sales=1)


def _dialog(route, name, outcome, docnum=""):
    return (IDLE + ACTION + "await idle();"
            f"frappe.set_route({route!r}, {name!r}); await idle();"
            "for(let k=0;k<80&&!document.querySelector('.page-head .page-title');k++) await new Promise(r=>setTimeout(r,250));"
            "await new Promise(r=>setTimeout(r,1200)); await idle();"
            "const via=await action('MR.ERP'); if(!via) return {button:false};"
            "for(let k=0;k<80&&!(window.cur_dialog&&cur_dialog.$wrapper.is(':visible'));k++) await new Promise(r=>setTimeout(r,250)); await idle();"
            "const d=cur_dialog; const v=n=>d&&d.fields_dict&&d.fields_dict[n]?String(d.get_value(n)||''):null;"
            "const before={state:v('state'), reason:v('reason'), primary:(d.$wrapper.find('.btn-primary:visible').first().text()||'').trim()};"
            f"await d.set_value('outcome', {outcome!r}); await idle();"
            + (f"await d.set_value('check_docnum', {docnum!r});" if docnum else "") +
            "d.$wrapper.find('.btn-primary:visible').first().click(); await idle(); await new Promise(r=>setTimeout(r,800));"
            "const via2=await action('MR.ERP');"
            "for(let k=0;k<80&&!(window.cur_dialog&&cur_dialog.$wrapper.is(':visible'));k++) await new Promise(r=>setTimeout(r,250)); await idle();"
            "const e=cur_dialog; const w=n=>e&&e.fields_dict&&e.fields_dict[n]?String(e.get_value(n)||''):null;"
            "const after={state:w('state'), docnum:w('docnum'), reason:w('reason'), outcome:w('outcome'),"
            "primary:(e.$wrapper.find('.btn-primary:visible').first().text()||'').trim()}; e.hide(); await idle();"
            "return {button:true, before, after};")


def steps(params, camera_code=None):
    return [
        {"name": "采购单核对已入账", "shot": True, "file": "1-purchase", "js": _dialog("pearnly-purchase", params["purchase"][0], "entered", DOCNUM)},
        {"name": "销售单核对未入账", "shot": True, "file": "2-sales", "js": _dialog("pearnly-sales", params["sales"][0], "not_entered")},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    p, s = by.get("采购单核对已入账") or {}, by.get("销售单核对未入账") or {}
    return [
        {"case": "结果不明的单：弹窗状态「结果不明」、按钮「已在 MR.ERP 核对」", "seen": {"p": p.get("before"), "s": s.get("before")},
         "ok": all((x.get("before") or {}).get("state") == "结果不明" and (x.get("before") or {}).get("primary") == "已在 MR.ERP 核对"
                   for x in (p, s))},
        {"case": "采购单已入账：状态成功、MR.ERP 单号是填的那个、不再有核对", "seen": p.get("after"),
         "ok": (p.get("after") or {}).get("state") == "成功" and (p.get("after") or {}).get("docnum") == DOCNUM
         and (p.get("after") or {}).get("outcome") is None},
        {"case": "销售单未入账：状态失败、原因「已核对，未入账」、按钮换成「重试」", "seen": s.get("after"),
         "ok": (s.get("after") or {}).get("state") == "失败" and "已核对，未入账" in ((s.get("after") or {}).get("reason") or "")
         and (s.get("after") or {}).get("primary") == "重试"},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lib.states(lab, manifest, params)
    p, s = got.get(params["purchase"][0]) or {}, got.get(params["sales"][0]) or {}
    return [
        {"case": "库里：采购单已写入 + 单号，销售单停下「已核对，未入账」，各一条核对评论", "seen": got,
         "ok": (p.get("ledger_state"), p.get("ledger_docnum"), p.get("ledger_reason")) == ("written", DOCNUM, "")
         and (s.get("ledger_state"), s.get("ledger_reason")) == ("held", "mrerp.confirmed_not_entered")
         and sorted(got.get("comments") or []) == sorted([params["purchase"][0], params["sales"][0]])},
    ]


cleanup = lib.cleanup
