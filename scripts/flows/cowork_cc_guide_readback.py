"""流程用例（等接线）：Cowork CC「回读」引导——本对话账套是 Express（T.8）时账套不问、只问月份（推荐对话月份、点了才算）→「确定」→
结果卡（实验室没有真 CC 桌面版来读：在读 / 电脑没来领都算对，不说读完）。库里：读回请求记在 T.8、这个月；不写账本。共用片段 `cc_guide_p3_lib`。"""
from __future__ import annotations

from cc_guide_p3_lib import JS, by_step, cleanup, delete_conversation, open_conversation, prepare, say_hello  # noqa: F401

TITLE = "Cowork CC 回读引导：账套 = 本对话 T.8 不问，只问月份 → 确定 → 结果卡（在读 / 电脑没来领），请求记在 T.8"


def steps(params, camera_code=None):
    return [
        open_conversation(params), say_hello(params),
        {"name": "点回读", "js": JS + "return await openBusiness('回读');"},
        {"name": "选月份", "js": JS + "const box=qk('period'); const inp=box?.querySelector('[data-slot=\"cc-guide-value\"]'); "
         "const pre=inp?.value||''; if(inp&&!inp.value) setVal(inp,'2026-10'); const ok=await tap('提交'); "
         "return {pre, ok, status: card()?.dataset.status||'', decided: [...(card()?.querySelectorAll('[data-slot=\"cc-guide-decided\"]')||[])].map(x=>x.innerText.trim())};"},
        {"name": "确定", "shot": True, "file": "1-result", "js": JS + "const ok=await tap('确定'); await until(()=>result(),120); "
         "return {ok, result: result()?.dataset.result||'', text: result()?.innerText||''};"},
        delete_conversation(params),
    ]


def judge(params, results, camera_code=None):
    by = by_step(results)
    opened, month, done = by.get("点回读") or {}, by.get("选月份") or {}, by.get("确定") or {}
    return [
        {"case": "菜单有「回读」，卡上只问月份（账套 = 本对话 T.8 进「已定」）", "seen": opened,
         "ok": opened.get("business") == "readback" and opened.get("keys") == ["period"]},
        {"case": "月份提交后可以确定", "seen": month, "ok": bool(month.get("ok"))},
        {"case": "确定后结果卡：在读或电脑没来领（实验室没有真电脑），不说读完", "seen": done,
         "ok": bool(done.get("ok")) and done.get("result") in ("queued", "failed")},
        {"case": "删掉这段会话", "seen": by.get("删除会话"), "ok": bool((by.get("删除会话") or {}).get("ids"))},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], f"""
rows = frappe.get_all("Cowork Express Readback", filters={{"connection": {params['connection']!r}, "period": "2026-10"}},
                      fields=["account_set_key", "status"], limit_page_length=5)
print(json.dumps({{"rows": rows}}, default=str))
""")
    rows = out.get("rows") or []
    return [{"case": "库里：回读请求只在 T.8（或电脑没来领、没建）；没有写账本", "seen": out,
             "ok": all(str(row.get("account_set_key") or "").upper().endswith("T8") or "T.8" in str(row.get("account_set_key")) for row in rows)}]
