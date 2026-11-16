"""流程用例（等接线）：Cowork CC「对账」引导——第一题选银行对账 → 来源（本对话 T.8 是 Express：读回 / 交文件两项）选交文件 → 银行账单、
总账各交对话里的一份 → 确定 → 结果卡（对账任务排队 / 在跑 / 完成都算交出去了）。值来自卡上；对账任务与网页「新建」同一组函数。"""
from __future__ import annotations

import json

from cc_guide_p3_lib import JS, by_step, cleanup, delete_conversation, open_conversation, prepare, say_hello  # noqa: F401

TITLE = "Cowork CC 对账引导：银行对账 → 交文件 → 两侧各一份 → 确定 → 结果（任务交出）"
STATEMENT = "date,description,withdrawal,deposit,balance\n01/10/2026,Opening,,,1000.00\n02/10/2026,Transfer in,,500.00,1500.00\n"
LEDGER = "date,voucher,description,debit,credit\n02/10/2026,RV001,Transfer in,500.00,\n"


def steps(params, camera_code=None):
    stmt, gl = f"{params['tag']}-stmt.csv", f"{params['tag']}-gl.csv"
    return [
        open_conversation(params), say_hello(params),
        {"name": "交两份附件", "js": JS + f"const a=await attach({json.dumps(stmt)},{json.dumps(STATEMENT)},'text/csv'); "
         f"const b=await attach({json.dumps(gl)},{json.dumps(LEDGER)},'text/csv'); const sent=await say({json.dumps(params['tag'] + ' 文件')}); await turnDone(); return {{a,b,sent}};"},
        {"name": "点对账", "js": JS + "return await openBusiness('对账中心');"},
        {"name": "选银行对账与交文件", "js": JS + "await pick(qk('kind'),'银行对账'); await tap('提交'); "
         "const sources=[...(qk('source')?.querySelectorAll('label')||[])].map(l=>l.innerText.trim()); await pick(qk('source'),'上传文件'); await tap('提交'); "
         "return {sources, keys: keys()};"},
        {"name": "交两侧文件", "js": JS + f"const a=await files('statement',[{json.dumps(stmt)}]); const b=await files('ledger',[{json.dumps(gl)}]); "
         "const ok=await tap('提交'); return {a,b,ok,status: card()?.dataset.status||''};"},
        {"name": "确定", "shot": True, "file": "1-result", "js": JS + "const ok=await tap('确定'); await until(()=>result(),120); "
         "return {ok, result: result()?.dataset.result||''};"},
        delete_conversation(params),
    ]


def judge(params, results, camera_code=None):
    by = by_step(results)
    opened, chosen, sides, done = (by.get(k) or {} for k in ("点对账", "选银行对账与交文件", "交两侧文件", "确定"))
    return [
        {"case": "菜单「对账中心」一项，第一题选种类", "seen": opened, "ok": opened.get("business") == "recon" and "kind" in (opened.get("keys") or [])},
        {"case": "本对话是 Express：来源有读回与交文件两项；选交文件后问银行账单与总账", "seen": chosen,
         "ok": len(chosen.get("sources") or []) == 2 and {"statement", "ledger"} <= set(chosen.get("keys") or [])},
        {"case": "两侧文件从对话附件里选，提交后可以确定", "seen": sides, "ok": bool(sides.get("a") and sides.get("b") and sides.get("ok"))},
        {"case": "确定后结果卡（对账任务已交出：排队 / 在跑 / 完成 / 失败带原因）", "seen": done,
         "ok": bool(done.get("ok")) and done.get("result") in ("queued", "ready", "failed")},
    ]


def verify(lab, manifest, params, camera_code=None):
    return []
