"""流程用例：引导进行中点停止 → 卡读回已失效、什么都没写（引导式退出路径，主会话 11-16）。

真页面、假智能体（开对话、让 CC 在工作）；收款数据同一套造法（`collection_lib.seed`）。不连 Express、不发 LINE、不调模型。

页面上走用户真点的路（主会话 11-19）：点输入框上方的「收款」→ 对话里出引导卡（问客户）→ 再说一句让 CC 工作
（假智能体 ``[lab-hold]`` 挂住不回，直到被停）→ 输入框的停止键出现 → 点停止键
→ 不刷新，卡当场换成失效一行（``data-status=expired``，等 20 秒）→ 服务端读回这次引导 ``expired`` / ``stopped``（``guide_view``）
→ 再刷新页面，卡读回仍是失效一行（第二道）。对话号取页面地址的 ``?c=``，不靠标题（手机上标题可能还没生成）。
库里：这次引导 ``expired`` / ``stopped``；本次专用客户没有任何来源 cc 的收款单（没有草稿、没有写入任务）。
对话在收尾里删（引导随对话删：在步骤里删的话库里就查不到这次引导了）。
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
    since = lab.python(site, 'print(json.dumps({"at": str(frappe.utils.now_datetime())}))').get("at")
    return {**seed(lab, manifest, tag), "tag": tag, "since": since, "cases": []}


def steps(params, camera_code=None):
    # 每步自己的代码包进一个块：与公用片段 JS 里同名的 card / row… 各在各的作用域（不包时报 Identifier 'card' has already been declared）
    return [{**step, "js": JS + "{" + step["js"][len(JS):] + "}"} if step["js"].startswith(JS) else step for step in _steps(params)]


SID = "const sid=new URL(location.href).searchParams.get('c')||''; "


def _steps(params):
    return [
        {"name": "开对话", "js": JS + f"const sent=await say({json.dumps(params['tag'] + ' 你好')}); await sleep(1500); return {{sent}};"},
        # 菜单项一律经 pickBusiness（这一排有就点，收进「更多」的开「更多」点）
        {"name": "点收款", "js": JS + HELP + "await until(()=>menuRow(),120); const picked=await pickBusiness('collection'); "
         "await until(()=>card()&&card().dataset.status==='asking', 120); "
         "return {picked, status: card()?.dataset.status||'', run: card()?.dataset.run||''};"},
        {"name": "让 CC 工作", "js": JS + HELP + f"const sent=await say({json.dumps(params['tag'] + ' [lab-hold]' + params['tag'] + '[/lab-hold]')}); "
         "const running=await until(()=>q('button.aui-composer-cancel'), 120); "
         "return {sent, running, status: card()?.dataset.status||''};"},
        {"name": "点停止", "shot": True, "file": "1-stopped", "js": JS + HELP + SID +
         "const run=card()?.dataset.run||''; const stop=q('button.aui-composer-cancel'); if(stop) stop.click(); "
         "const live=await until(()=>card()&&card().dataset.status==='expired', 80); await sleep(500); "
         "const server=run ? await call('pearnly_core.assistant.api.guide_view',{run_name:run}) : null; "
         "return {session: sid, run, clicked: !!stop, live, status: card()?.dataset.status||'', "
         "server: {status: server?.status||'', reason: server?.reason||''}};"},
        {"name": "刷新页面", "js": "location.reload(); return 1;", "wait_ms": 6000},
        {"name": "刷新后读卡", "shot": True, "file": "2-reloaded", "js": JS + HELP +
         "await until(()=>card(), 120); await until(()=>card().dataset.status==='expired', 60); "
         "return {status: card()?.dataset.status||'', run: card()?.dataset.run||''};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, busy = by.get("点收款") or {}, by.get("让 CC 工作") or {}
    stopped, reloaded = by.get("点停止") or {}, by.get("刷新后读卡") or {}
    return [
        {"case": "点「收款」出引导卡（在问）", "seen": opened, "ok": (opened.get("picked") or {}).get("via") in ("row", "more") and opened.get("status") == "asking"
         and bool(opened.get("run"))},
        {"case": "再说一句：CC 在工作，输入框出停止键", "seen": busy, "ok": bool(busy.get("running"))},
        {"case": "点停止键：不刷新，卡当场换成失效一行", "seen": stopped,
         "ok": bool(stopped.get("clicked")) and stopped.get("run") == opened.get("run") and stopped.get("status") == "expired"},
        {"case": "点停止键：服务端读回这次引导失效、原因是停止", "seen": stopped,
         "ok": bool(stopped.get("session")) and stopped.get("server") == {"status": "expired", "reason": "stopped"}},
        {"case": "刷新后：卡读回失效一行", "seen": reloaded,
         "ok": reloaded.get("status") == "expired" and reloaded.get("run") == opened.get("run")},
    ]


RUNS = """
runs = frappe.get_all("Pearnly Assistant Guide Run",
                      filters={{"business": "collection", "user": {user!r}, "creation": [">=", {since!r}]}},
                      fields=["name", "session_id", "status", "ended_reason"], order_by="creation desc", limit_page_length=5)
"""


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], RUNS.format(user=USER, since=params["since"]) + f"""
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
    """成功、失败、超时都跑：删这次开的对话（引导随对话删），再还原造的收款数据。"""
    if params.get("since"):
        lab.python(manifest["site"], RUNS.format(user=USER, since=params["since"]) + f"""
from pearnly_core.assistant import sessions
frappe.set_user({USER!r})
for sid in sorted({{r.session_id for r in runs if r.session_id}}):
    sessions.delete(sid)
frappe.db.commit()
print("{{}}")
""")
    restore(lab, manifest, params)
