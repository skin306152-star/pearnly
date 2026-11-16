"""流程用例（等接线）：Cowork CC「预扣税申报表」引导——月份（推荐对话月份）、卡上没有公司可选 →「确定」→ 结果：两张表的收款方数与税额、
工作簿进对话。付款公司就是本对话公司（库里草稿的税号 = 这家的税号）。实验室这家这个月没有费用票，草稿是空的也照出。"""
from __future__ import annotations

from cc_guide_p3_lib import JS, by_step, cleanup, delete_conversation, open_conversation, prepare, say_hello  # noqa: F401

TITLE = "Cowork CC 预扣税引导：月份 → 确定 → 本对话公司的草稿与工作簿"


def steps(params, camera_code=None):
    return [
        open_conversation(params), say_hello(params),
        {"name": "点预扣税", "js": JS + "return await openBusiness('预扣税申报表');"},
        {"name": "选月份并确定", "shot": True, "file": "1-result", "js": JS + "const inp=qk('period')?.querySelector('[data-slot=\"cc-guide-value\"]'); "
         "if(inp&&!inp.value) setVal(inp,'2026-10'); await tap('提交'); const ok=await tap('确定'); await until(()=>result(),120); "
         "return {ok, result: result()?.dataset.result||'', files: resultFiles()};"},
        delete_conversation(params),
    ]


def judge(params, results, camera_code=None):
    by = by_step(results)
    opened, done = by.get("点预扣税") or {}, by.get("选月份并确定") or {}
    return [
        {"case": "菜单「预扣税申报表」，只问月份（没有公司可选）", "seen": opened, "ok": opened.get("business") == "wht" and opened.get("keys") == ["period"]},
        {"case": "确定后结果：草稿出来、工作簿进对话", "seen": done,
         "ok": bool(done.get("ok")) and done.get("result") == "ready" and bool(done.get("files"))},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], f"""
company = frappe.db.get_value("Cowork Company", {params['company']!r}, "tax_id")
drafts = frappe.get_all("Cowork Tax Draft", filters={{"kind": "withholding", "period": "2026-10"}}, fields=["record_id", "summary_json"], limit_page_length=5)
print(json.dumps({{"company": company, "mine": [d for d in drafts if company and company in (d.get("summary_json") or "")]}}, default=str))
""")
    return [{"case": "库里：草稿的付款公司就是本对话公司", "seen": {"company": out.get("company"), "count": len(out.get("mine") or [])},
             "ok": bool(out.get("mine"))}]
