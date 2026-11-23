"""流程用例：Cowork 版 CC「收银日汇总」（POS）引导真页面从菜单到结果（表单业务 pos，写账、有复核步骤）。

造数据同 `cowork_cc_guide_form_lib`。一份 POS 导出（CSV，表头认得出、ISO 日期、两天三张小票）用对话页同一个附件接口传进这段对话。
页面上（不经模型）：业务菜单「收银日汇总」→ 交导出那一题点这份文件 → 提交（表头认得出不出认列题；没有商品明细不问入账方式）→
复核卡每天一行 → 点确定那一下 → 结果卡每天一张票一行：日汇总票建好、推送另走推送链，没推过的标「未推送」（已有词条）。
库里：引导运行 done、同意来自页面；两张来源这份导出的销项票，推送目标是本对话账套 T.8，还没推。
"""
from __future__ import annotations

import json

import cowork_cc_guide_form_lib as lib
from cc_lib import JS

TITLE = "Cowork 版 CC 收银日汇总引导：交 POS 导出 → 复核每天一行 → 确定 → 结果卡每天一张票一行（未推送）；两张票推送目标 T.8"
API = "pearnly_core.assistant.api."


def _export(month):
    return ("Date,Receipt No,Payment Method,Grand Total,VAT\n"
            f"{month}-13,R0001,Cash,107.00,7.00\n{month}-13,R0002,QR,214.00,14.00\n{month}-14,R0003,Cash,53.50,3.50\n")


def prepare(lab, manifest):
    return lib.prepare(lab, manifest, "", "pos")


def steps(params, camera_code=None):
    name = f"{params['tag']}-pos.csv"
    upload = (JS + "await until(()=>q('textarea.aui-composer-input'),160); "
              f"const fd=new FormData(); fd.append('session_id',{json.dumps(params['session'])}); "
              f"fd.append('file', new File([{json.dumps(_export(lib.MONTH))}], {json.dumps(name)}, {{type:'text/csv'}})); "
              f"const r=await fetch('/api/method/{API}upload',{{method:'POST',headers:{{'X-Frappe-CSRF-Token':window.csrf_token||''}},body:fd}}); "
              "const j=await r.json().catch(()=>({})); return {file: ((j||{}).message||{}).fileId||''};")
    return [
        lib.open_conversation(params),
        {"name": "交导出进对话", "js": upload},
        lib.open_business("pos"),
        lib.answer({"export": [name]}),
        {"name": "复核", "shot": True, "file": "1-review", "js": JS + lib.HELP +
         "await until(()=>card()?.querySelector('[data-slot=\"cc-guide-review\"]'),160); const r=card()?.querySelector('[data-slot=\"cc-guide-review\"]'); "
         "return {text: r?.innerText||'', total: r?.querySelector('[data-slot=\"cc-guide-total\"]')?.innerText||''};"},
        lib.confirm(shot="2-result"),
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    uploaded, opened, answered, review, done = (by.get(k) or {} for k in ("交导出进对话", "点菜单", "作答", "复核", "确定"))
    rows = done.get("rows") or []
    return [
        {"case": "导出传进对话；业务菜单有收银日汇总，第一题交导出", "seen": {"uploaded": uploaded, "opened": opened},
         "ok": bool(uploaded.get("file")) and bool(opened.get("clicked")) and opened.get("business") == "pos"
         and (opened.get("keys") or [None])[0] == "export"},
        {"case": "表头认得出：交了导出就到复核（不出认列、入账方式题），每天一行、合计 374.50", "seen": {"answered": answered, "review": review},
         "ok": [p.get("key") for p in answered.get("pages") or ()] == ["export"] and f"{lib.MONTH}-13" in review.get("text", "")
         and f"{lib.MONTH}-14" in review.get("text", "") and "374.50" in review.get("total", "")},
        {"case": "确定后结果卡每天一张票一行；日汇总票还没推送，标「未推送」（已有词条），不说已跳过", "seen": done,
         "ok": done.get("status") == "done" and len(rows) == 2 and all(r.get("status") == "" and "未推送" in r.get("text", "")
                                                                   and "已跳过" not in r.get("text", "") for r in rows)},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], f"""
runs = frappe.get_all("Pearnly Assistant Guide Run", filters={{"session_id": {params['session']!r}, "business": "pos"}},
                      fields=["status", "consent"], limit_page_length=3)
docs = frappe.get_all("Cowork OCR History", filters={{"push_connection": {params['connection']!r}}},
                      fields=["name", "push_account_set", "last_push_status"], limit_page_length=10)
print(json.dumps({{"runs": runs, "docs": docs}}, default=str))
""")
    runs, docs = out.get("runs") or [], out.get("docs") or []
    return [
        {"case": "引导运行办完（done），同意是页面上这一下", "seen": runs,
         "ok": len(runs) == 1 and runs[0].get("status") == "done" and str(runs[0].get("consent") or "").startswith("guide:")},
        {"case": "两张日汇总票，推送目标是本对话账套 T.8，还没推", "seen": docs,
         "ok": len(docs) == 2 and all(str(d.get("push_account_set") or "").lower() == lib.ledger.T8 and not d.get("last_push_status")
                                      for d in docs)},
    ]


def cleanup(lab, manifest, params):
    lib.cleanup(lab, manifest, params)
