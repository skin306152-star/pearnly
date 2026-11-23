"""流程用例：ERP 版 CC 对话里撤回收款先出「允许 / 拒绝」卡片（用户 11-12；任务记录 docs/work/2026-11-14-cc-approval-cards.md）。

真页面、假智能体（``[lab-tool]``，见 `cc_approval_lib.py`）：造一笔收款草稿（收款流程同一套造数据 `collection_lib.seed`：本次专用的
客户与两张未收清发票），让假 CC 调 ``erp_collection_withdraw`` → 出卡，卡上「客户 · 发票 · 金额」→ 点「拒绝」→ 这笔还是草稿、模型收到
denied；再说一次 → 点「允许」→ 这笔标「已撤回」。电脑与手机宽度各跑一遍，各截一张卡片图。不连 Express、不发 LINE、不调模型。
"""
from __future__ import annotations

import json
import uuid

from cc_approval_lib import JS, set_language, tool
from collection_lib import restore, seed

TITLE = "ERP 版 CC 对话里撤回收款先出「允许 / 拒绝」卡片：卡上客户、发票、金额，拒绝不撤回，允许才撤回"
READY = "document.readyState === 'complete' && !!document.querySelector('textarea[aria-label]')"
USER = "admin@erp.regression"
WITHDRAW = "erp_collection_withdraw"
ASK = "确定要撤回此收款吗？"


def prepare(lab, manifest):
    site = manifest["site"]
    set_language(lab, site, USER, "zh")
    tag = "wd" + uuid.uuid4().hex[:6]
    seeded = seed(lab, manifest, tag)
    made = lab.python(site, f"""
from pearnly_erp_portal.collection import service
frappe.set_user({USER!r})
card = service.create_draft({seeded['company']!r}, {seeded['customer']!r}, [{{"docnum": {seeded['invoices'][0]!r}, "amount": "100"}}],
                            cash="100", source="cc")
frappe.db.commit()
print(json.dumps({{"name": card["name"], "status": card["status"]}}, default=str))
""")
    return {**seeded, "tag": tag, "collection": made["name"], "cases": [],
            "withdraw": tool(tag, WITHDRAW, {"collection": made["name"]})}


def steps(params, camera_code=None):
    read = (f"const state=async()=>((await call('frappe.client.get_value',{{doctype:'ERP Collection',"
            f"filters:JSON.stringify({{name:{json.dumps(params['collection'])}}}),fieldname:'status'}}))||{{}}).status; ")
    return [
        {"name": "撤回：出卡", "shot": True, "file": "1-withdraw-card", "js": JS + f"return await ask({json.dumps(params['withdraw'])}, {WITHDRAW!r});"},
        {"name": "撤回：拒绝", "js": JS + read + f"const r=await answer(false, {WITHDRAW!r}); return {{result: r, status: await state()}};"},
        {"name": "撤回：再来一次并允许", "shot": True, "file": "2-withdraw-allowed", "js": JS + read +
         f"const opened=await ask({json.dumps(params['withdraw'])}, {WITHDRAW!r}); const r=await answer(true, {WITHDRAW!r}); "
         "await sleep(600); return {card: opened, result: r, status: await state()};"},
        {"name": "删除会话", "js": JS + f"const mine=(await ownChats({json.dumps(params['tag'])},'chat')); "
         "for(const s of mine) await call('pearnly_core.assistant.api.delete',{session_id:s.id},true); return {ids: mine.map(s=>s.id)};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    first, denied, again = (by.get(k) or {} for k in ("撤回：出卡", "撤回：拒绝", "撤回：再来一次并允许"))
    rows = first.get("rows") or []
    return [
        {"case": "出允许卡：问句中文，卡上客户（码 · 名称）、发票号、金额", "seen": first,
         "ok": bool(first.get("shown")) and first.get("question") == ASK and len(rows) == 3
         and params["customer"] in rows[0] and params["invoices"][0] in rows[1] and "100.00" in rows[2]
         and not first.get("overflow")},
        {"case": "点「拒绝」：模型收到 denied，这笔还是草稿", "seen": denied,
         "ok": (denied.get("result") or {}).get("status") == "denied" and denied.get("status") == "draft"},
        {"case": "再说一次、点「允许」：模型收到 withdrawn，这笔标「已撤回」", "seen": again,
         "ok": bool((again.get("card") or {}).get("shown")) and (again.get("result") or {}).get("status") == "withdrawn"
         and again.get("status") == "withdrawn"},
        {"case": "删掉这段会话", "seen": by.get("删除会话"), "ok": bool((by.get("删除会话") or {}).get("ids"))},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], f"""
print(json.dumps({{"status": frappe.db.get_value("ERP Collection", {params['collection']!r}, "status")}}))
""")
    return [{"case": "库里：这笔收款已撤回（记录保留）", "seen": out, "ok": out.get("status") == "withdrawn"}]


def cleanup(lab, manifest, params):
    restore(lab, manifest, params)
