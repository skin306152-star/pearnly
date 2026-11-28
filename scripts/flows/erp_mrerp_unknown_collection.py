"""流程用例：ERP 收款写进 MR.ERP 结果不明（用户 11-28 批准「核对」出口）——收款页点开这一笔：状态「结果不明」、按钮「已在 MR.ERP 核对」
（没有「撤回」）→ 选「已入账」填 MR.ERP 单号 → 再点开状态「成功」、单号是填的那个，库里已写入 + 单号、一条核对评论。"""
from __future__ import annotations

import json

import erp_mrerp_unknown_lib as lib
from desk_list import IDLE

TITLE = "ERP 收款写进 MR.ERP 结果不明：收款页「已在 MR.ERP 核对」已入账 → 已写入 + 填的单号（不给撤回）"
DOCNUM = "FR691128-FLOW01"


def prepare(lab, manifest):
    return lib.prepare(lab, manifest, collections=1)


def steps(params, camera_code=None):
    name = params["collections"][0]
    return [{"name": "收款页核对已入账", "shot": True, "file": "1-collection", "js": IDLE + "await idle();"
             f"frappe.set_route('pearnly-collections', {name!r}); await idle();"
             "for(let k=0;k<80&&!(window.cur_dialog&&cur_dialog.$wrapper.is(':visible'));k++) await new Promise(r=>setTimeout(r,250)); await idle();"
             "const d=cur_dialog; const before={status:String(d.get_value('status')||''), reason:String(d.get_value('reason')||''),"
             "primary:(d.$wrapper.find('.btn-primary:visible').first().text()||'').trim(),"
             "buttons:[...d.$wrapper.find('button:visible')].map(b=>b.innerText.trim()).filter(Boolean)};"
             "d.$wrapper.find('.btn-primary:visible').first().click();"
             "for(let k=0;k<40&&!(window.cur_dialog&&cur_dialog!==d&&cur_dialog.$wrapper.is(':visible'));k++) await new Promise(r=>setTimeout(r,250));"
             "const pr=cur_dialog; await pr.set_value('outcome','entered'); await idle();"
             f"await pr.set_value('docnum', {DOCNUM!r}); pr.$wrapper.find('.btn-primary:visible').first().click();"
             "await idle(); await new Promise(r=>setTimeout(r,1200));"
             # 再点开这一笔（电脑、手机同一个弹窗；手机列表不出单号那一列）读状态与单号
             "frappe.set_route('pearnly-collections'); await idle(); await new Promise(r=>setTimeout(r,800));"
             f"frappe.set_route('pearnly-collections', {name!r}); await idle();"
             "for(let k=0;k<80&&!(window.cur_dialog&&cur_dialog!==pr&&cur_dialog.$wrapper.is(':visible'));k++) await new Promise(r=>setTimeout(r,250));"
             "const a=cur_dialog; const after={status:String(a.get_value('status')||''), docnum:String(a.get_value('docnum')||''),"
             "buttons:[...a.$wrapper.find('button:visible')].map(b=>b.innerText.trim()).filter(Boolean)}; a.hide();"
             "return {before, after};"}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") or {} for r in results if r.get("step") == "收款页核对已入账"), {})
    before = seen.get("before") or {}
    return [
        {"case": "点开：状态结果不明、原因是 MR.ERP 核对句、按钮「已在 MR.ERP 核对」、没有「撤回」", "seen": before,
         "ok": before.get("status", "").startswith("结果不明") and "MR.ERP" in before.get("reason", "")
         and before.get("primary") == "已在 MR.ERP 核对" and "撤回" not in (before.get("buttons") or [])},
        {"case": "核对已入账后再点开：状态成功、MR.ERP 单号是填的那个、不再有核对", "seen": seen.get("after"),
         "ok": (seen.get("after") or {}).get("status", "").startswith("成功") and (seen.get("after") or {}).get("docnum") == DOCNUM
         and "已在 MR.ERP 核对" not in ((seen.get("after") or {}).get("buttons") or [])},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lib.states(lab, manifest, params)
    row = got.get(params["collections"][0]) or {}
    return [{"case": "库里：已写入 + 单号、原因清空、一条核对评论", "seen": got,
             "ok": (row.get("status"), row.get("docnum"), row.get("reason")) == ("written", DOCNUM, "")
             and got.get("comments") == [params["collections"][0]]}]


cleanup = lib.cleanup
