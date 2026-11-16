"""流程用例（等接线）：Cowork CC「增值税申报表」引导——月份（推荐对话月份）→ 这个月没有回读：回读那一步出卡时请电脑读一次，
题目下一行「正在回读」、确定不了（实验室没有真电脑读完）。库里：只建了读回请求，没有草稿。共用片段 `cc_guide_p3_lib`。"""
from __future__ import annotations

from cc_guide_p3_lib import JS, by_step, cleanup, delete_conversation, open_conversation, prepare, say_hello  # noqa: F401

TITLE = "Cowork CC 增值税引导：月份 → 本月没有回读时出卡请读一次、题目下一行在读、确定不了"


def steps(params, camera_code=None):
    return [
        open_conversation(params), say_hello(params),
        {"name": "点增值税", "js": JS + "return await openBusiness('增值税申报表');"},
        {"name": "选月份", "shot": True, "file": "1-reading", "js": JS + "const inp=qk('period')?.querySelector('[data-slot=\"cc-guide-value\"]'); "
         "if(inp&&!inp.value) setVal(inp,'2026-10'); await tap('提交'); await until(()=>qk('readback'),60); "
         "const box=qk('readback'); const sure=[...(card()?.querySelectorAll('button')||[])].find(b=>b.innerText.trim()==='确定'); "
         "return {keys: keys(), facts: box?.innerText||'', confirmable: !!sure&&!sure.disabled};"},
        delete_conversation(params),
    ]


def judge(params, results, camera_code=None):
    by = by_step(results)
    opened, month = by.get("点增值税") or {}, by.get("选月份") or {}
    return [
        {"case": "菜单「增值税申报表」，第一题是月份", "seen": opened, "ok": opened.get("business") == "vat" and opened.get("keys") == ["period"]},
        {"case": "本月没有回读：回读那一步出来、题目下一行在读，确定不了", "seen": month,
         "ok": "readback" in (month.get("keys") or []) and "正在回读" in (month.get("facts") or "") and not month.get("confirmable")},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], f"""
drafts = frappe.db.count("Cowork Tax Draft", {{"kind": "vat_return", "period": "2026-10"}})
reads = frappe.db.count("Cowork Express Readback", {{"connection": {params['connection']!r}, "period": "2026-10"}})
print(json.dumps({{"drafts": drafts, "reads": reads}}))
""")
    return [{"case": "库里：请了一次读回（或电脑没来领、没建），没有出草稿", "seen": out, "ok": out.get("drafts") == 0 and out.get("reads") <= 1}]
