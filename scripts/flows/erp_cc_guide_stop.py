"""流程用例：引导进行中点停止 → 卡读回已失效、什么都没写（引导式退出路径，主会话 11-16）。

真页面、假智能体（只用来开一段对话）；收款数据同一套造法（`collection_lib.seed`）。不连 Express、不发 LINE、不调模型。

页面上：点输入框上方的「收款」→ 对话里出引导卡（问客户）→ 点停止（对话页停止键调的同一个接口 ``assistant.api.interrupt``）
→ 卡按事件换成失效一行（``data-status=expired``）。
库里：这次引导 ``expired`` / ``stopped``；本次专用客户没有任何来源 cc 的收款单（没有草稿、没有写入任务）。
"""
from __future__ import annotations

import json
import uuid

from cc_approval_lib import JS, set_language
from collection_lib import restore, seed

TITLE = "ERP 版 CC 收款引导卡进行中点停止：卡读回已失效，没有收款单、没有写入任务"
USER = "admin@erp.regression"
CARD = "qa('[data-slot=\"cc-guide-run\"]').pop()"
HELP = "const card=()=>" + CARD + "; "


def prepare(lab, manifest):
    site = manifest["site"]
    set_language(lab, site, USER, "zh")
    tag = "gs" + uuid.uuid4().hex[:6]
    return {**seed(lab, manifest, tag), "tag": tag, "cases": []}


def steps(params, camera_code=None):
    # 每步自己的代码包进一个块：与公用片段 JS 里同名的 card / row… 各在各的作用域（不包时报 Identifier 'card' has already been declared）
    return [{**step, "js": JS + "{" + step["js"][len(JS):] + "}"} if step["js"].startswith(JS) else step for step in _steps(params)]


def _steps(params):
    tag = json.dumps(params["tag"])
    return [
        {"name": "开对话", "js": JS + f"const sent=await say({json.dumps(params['tag'] + ' 你好')}); await sleep(1500); return {{sent}};"},
        {"name": "点收款", "js": JS + HELP + "const row=()=>q('[data-slot=\"cc-business-menu\"]'); await until(()=>row(),120); "
         "[...row().querySelectorAll('button')].find(b=>b.innerText.trim()==='收款')?.click(); "
         "await until(()=>card()&&card().dataset.status==='asking', 120); return {status: card()?.dataset.status||''};"},
        {"name": "点停止", "shot": True, "file": "1-stopped", "js": JS + HELP +
         f"const mine=(await listed('chat')).filter(s=>(s.title||'').includes({tag})); const sid=mine[0]?.id; "
         "if(sid) await call('pearnly_core.assistant.api.interrupt',{session_id:sid},true); "
         "await until(()=>card()&&card().dataset.status==='expired', 60); await sleep(500); "
         "return {session: sid||'', status: card()?.dataset.status||''};"},
        {"name": "删除会话", "js": JS + f"const mine=(await listed('chat')).filter(s=>(s.title||'').includes({tag})); "
         "for(const s of mine) await call('pearnly_core.assistant.api.delete',{session_id:s.id},true); return {ids: mine.map(s=>s.id)};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, stopped = by.get("点收款") or {}, by.get("点停止") or {}
    return [
        {"case": "点「收款」出引导卡（在问）", "seen": opened, "ok": opened.get("status") == "asking"},
        {"case": "点停止：卡读回失效一行", "seen": stopped, "ok": bool(stopped.get("session")) and stopped.get("status") == "expired"},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], f"""
runs = frappe.get_all("Pearnly Assistant Guide Run", filters={{"business": "collection", "user": {USER!r}}},
                      fields=["name", "session_id", "status", "ended_reason"], order_by="creation desc", limit_page_length=1)
rows = frappe.get_all("ERP Collection", filters={{"customer": {params['customer']!r}, "source": "cc"}}, pluck="name")
print(json.dumps({{"runs": runs, "collections": rows}}, default=str))
""")
    top = (out.get("runs") or [{}])[0]
    return [
        {"case": "库里：这次引导失效、原因是停止", "seen": top,
         "ok": top.get("status") == "expired" and top.get("ended_reason") == "stopped"},
        {"case": "库里：本次客户没有来源 cc 的收款单（没有草稿、没有写入任务）", "seen": out.get("collections"),
         "ok": out.get("collections") == []},
    ]


def cleanup(lab, manifest, params):
    restore(lab, manifest, params)
