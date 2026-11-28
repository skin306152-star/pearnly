"""流程用例：ERP 版 CC 直写对话里核对 MR.ERP 结果不明（用户 11-28 批准）。假智能体（``[lab-tool]``，`cc_approval_lib`）调
``erp_collection_resolve_result``（没有入账）→ 出「允许」卡片，问句「已在 MR.ERP 核对」、卡上客户 · 发票 · 金额 · 结果 → 点「允许」→
这笔收款失败、原因「已核对，未入账」。单据那条（``erp_resolve_push_result``，要用户同意过的写入计划）在站点里按同一个工具函数核：
已入账记已写入 + 单号；不在同意了的计划里不收。"""
from __future__ import annotations

import erp_mrerp_unknown_lib as lib
from cc_approval_lib import JS, set_language, tool

TITLE = "ERP 版 CC 核对 MR.ERP 结果不明：收款出允许卡、允许后记「已核对，未入账」；单据按工具函数记已入账 + 单号（只在同意了的计划里）"
TOOL = "erp_collection_resolve_result"
ASK = "已在 MR.ERP 核对"
DOCNUM = "PG691128-FLOWCC"


def prepare(lab, manifest):
    set_language(lab, manifest["site"], lib.USER, "zh")
    params = lib.prepare(lab, manifest, purchases=1, collections=1)
    return {**params, "ask": tool(params["tag"], TOOL, {"collection": params["collections"][0], "entered": False})}


def steps(params, camera_code=None):
    return [
        {"name": "收款核对：出卡", "shot": True, "file": "1-card", "js": JS + f"return await ask({params['ask']!r}, {TOOL!r});"},
        {"name": "收款核对：允许", "js": JS + f"const r=await answer(true, {TOOL!r}); return {{result: r}};"},
        {"name": "删除会话", "js": JS + f"const mine=(await ownChats({params['tag']!r},'chat')); "
         "for(const s of mine) await call('pearnly_core.assistant.api.delete',{session_id:s.id},true); return {ids: mine.map(s=>s.id)};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    card, allowed = by.get("收款核对：出卡") or {}, by.get("收款核对：允许") or {}
    rows = card.get("rows") or []
    return [
        {"case": "出允许卡：问句「已在 MR.ERP 核对」，卡上客户、发票、金额、结果「未入账」", "seen": card,
         "ok": bool(card.get("shown")) and card.get("question") == ASK and len(rows) == 4 and "未入账" in rows[3]
         and not card.get("overflow")},
        {"case": "点允许：模型收到 saved", "seen": allowed, "ok": (allowed.get("result") or {}).get("status") == "saved"},
    ]


def verify(lab, manifest, params, camera_code=None):
    name = params["purchase"][0]
    doc = lab.python(manifest["site"], f"""
from pearnly_core.assistant import sessions
from pearnly_erp_portal.express import cc_tools
frappe.set_user({lib.USER!r})
frappe.local.lang = "zh"
made = sessions.create(project={params['company']!r}, period=frappe.utils.today()[:7], mode="chat")
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": made["id"]}}, "title", {params['tag']!r})
frappe.local.pearnly_assistant = {{"session_id": made["id"]}}
outside = json.loads(cc_tools.resolve_push_result({{"document": "P:" + {name!r}, "entered": True, "docnum": {DOCNUM!r}}}))
frappe.db.commit()
print(json.dumps({{"outside": outside}}, default=str))
""")
    got = lib.states(lab, manifest, params)
    row = got.get(params["collections"][0]) or {}
    purchase = got.get(name) or {}
    return [
        {"case": "库里：收款失败、原因「已核对，未入账」、一条核对评论", "seen": got,
         "ok": (row.get("status"), row.get("reason")) == ("failed", "mrerp.confirmed_not_entered")
         and params["collections"][0] in (got.get("comments") or [])},
        {"case": "单据：不在同意了的写入计划里，CC 核对工具不收、单子仍结果不明", "seen": {"tool": doc, "doc": purchase},
         "ok": (doc.get("outside") or {}).get("status") == "not_pushed" and purchase.get("ledger_state") == "held"
         and str(purchase.get("ledger_reason") or "").startswith("mrerp.result_unknown")},
    ]


cleanup = lib.cleanup
