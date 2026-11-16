"""流程用例（等接线）：Cowork CC「收银日汇总」引导——对话里交一份 POS 导出（CSV，表头认得出）→ 交导出那一题选它 → 不出认列题（认得出），
没有商品明细不问入账方式 → 复核卡每天一行（两天）、合计对 →「写入」→ 结果：建好两张日汇总票。库里：两张来源这份导出的销项票，推送目标是 T.8。
认列答案只来自卡上（这一份认得出，不问）。共用片段 `cc_guide_p3_lib`。"""
from __future__ import annotations

import json

from cc_guide_p3_lib import JS, by_step, cleanup, delete_conversation, open_conversation, prepare, say_hello  # noqa: F401

TITLE = "Cowork CC 收银日汇总引导：交 POS 导出 → 复核每天一行 → 写入 → 建好两张日汇总票（推送目标 T.8）"
EXPORT = ("Date,Receipt No,Payment Method,Total,VAT\n"
          "01/10/2026,R0001,Cash,107.00,7.00\n01/10/2026,R0002,QR,214.00,14.00\n02/10/2026,R0003,Cash,53.50,3.50\n")


def steps(params, camera_code=None):
    name = f"{params['tag']}-pos.csv"
    return [
        open_conversation(params), say_hello(params),
        {"name": "交 POS 导出", "js": JS + f"const a=await attach({json.dumps(name)},{json.dumps(EXPORT)},'text/csv'); "
         f"const sent=await say({json.dumps(params['tag'] + ' 导出')}); await turnDone(); return {{a,sent}};"},
        {"name": "点收银日汇总", "js": JS + "return await openBusiness('收银日汇总');"},
        {"name": "交导出", "js": JS + f"const a=await files('export',[{json.dumps(name)}]); const ok=await tap('提交'); "
         "await until(()=>card()?.dataset.status==='reviewing',60); "
         "const r=card()?.querySelector('[data-slot=\"cc-guide-review\"]'); "
         "return {a, ok, status: card()?.dataset.status||'', keys: keys(), text: r?.innerText||'', total: r?.querySelector('[data-slot=\"cc-guide-total\"]')?.innerText||''};"},
        {"name": "写入", "shot": True, "file": "1-result", "js": JS + "const ok=await tap('写入'); await until(()=>result(),120); "
         "return {ok, result: result()?.dataset.result||'', text: result()?.innerText||''};"},
        delete_conversation(params),
    ]


def judge(params, results, camera_code=None):
    by = by_step(results)
    opened, given, wrote = (by.get(k) or {} for k in ("点收银日汇总", "交导出", "写入"))
    return [
        {"case": "菜单「收银日汇总」，第一题交导出", "seen": opened, "ok": opened.get("business") == "pos" and opened.get("keys") == ["export"]},
        {"case": "表头认得出：不出认列题、不问入账方式，直接复核；每天一行、合计 374.50", "seen": given,
         "ok": given.get("status") == "reviewing" and "2026-10-01" in given.get("text", "") and "2026-10-02" in given.get("text", "")
         and "374.50" in given.get("total", "")},
        {"case": "写入后结果：建好日汇总票", "seen": wrote, "ok": bool(wrote.get("ok")) and wrote.get("result") == "ready"},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], f"""
rows = frappe.get_all("Cowork OCR History", filters={{"push_connection": {params['connection']!r}, "creation": [">", frappe.utils.add_to_date(None, hours=-1)]}},
                      fields=["name", "push_account_set", "posting_kind"], limit_page_length=10)
print(json.dumps({{"rows": [r for r in rows if r.get("posting_kind") == "service"]}}, default=str))
""")
    rows = out.get("rows") or []
    return [{"case": "库里：两张日汇总票，推送目标是 T.8", "seen": out,
             "ok": len(rows) >= 2 and all(str(r.get("push_account_set") or "").upper().endswith("T8") for r in rows)}]
