#!/usr/bin/env python3
"""固定场景（真模型、要花钱，部署前跑）：CC 写账由用户一句话下令开始（用户 11-10；写入计划 A 线 11-11，「写入」B 线）。

在本机实验栈用真模型照用户的样子做：说一句要录 → CC 先建写入计划、一句话复述、等用户点头（这时不写）→ 用户点输入框上方的「写入」
→ CC 按计划开始 → 才写。断言（任何一条不过即不过）：

  ① 第一句之后 CC 调了 pearnly_write_plan，没调 pearnly_write_start；输入框上方出「写入」（计划在等用户点头）
  ② 站点上用户点头之前什么都没写：推送记录 / 代录交单都晚于计划开始的时刻（服务端只在复述之后用户又发话才放行）
  ③ 点「写入」之后 CC 调了 pearnly_write_start，计划转 started；直写这张单有了推送记录，代录任务交出去（排队）；「写入」消失
  ④ CC 的回话里不出现 operator、wrong_mode、内部单号这类内部词

两遍（Cowork 直写、Cowork 代录），电脑宽度、界面中文。数据用 `scripts/flows/cowork_operator_lib.py` 的合成账套与一张识别好的
采购单（付款与科目已记住，写账判定没有要问的）：不识别、不付识别费，只有对话的模型费。实验栈没有 CC 桌面版：写到交单排队为止。
ERP 实验栈的单据都在等电脑读账套主档（写不了），这里不跑 ERP；ERP 与 Cowork 走同一个共用层计划（离线测试 `tests/test_write_plan.py`）。

花费（2026-11 价）：每轮对话约 0.1–0.3 美元，两遍各两轮，合计按 1.4 美元封顶（``--budget``，按每轮 0.35 估，超了不开下一遍）。

跑法（本机实验栈；先接真引擎，密钥只放站点配置与容器环境，不进仓库、不打印）：

    PEARNLY_FLOWS_LAB=pearnly-aline3-lab REGRESSION_LAB_PORT=18951 REGRESSION_LAB_PORT_COWORK=18952 REGRESSION_LAB_PORT_DMS=18953 \\
      python3 scripts/scenarios/real_run_preflight.py ...   # 见该文件：--engine real 接上 / off 断开
    python3 scripts/scenarios/write_plan_real.py --engine-on <密钥文件> --src <实验栈源码工作树>
    python3 scripts/scenarios/write_plan_real.py --out <结果.json>
    python3 scripts/scenarios/write_plan_real.py --engine-off --src <实验栈源码工作树>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "flows"))
sys.path.insert(0, str(ROOT / "scripts" / "scenarios"))
import run as RUN  # noqa: E402
import real_run_preflight as PREFLIGHT  # noqa: E402
from cc_lib import JS  # noqa: E402
from cowork_operator_lib import BASE, CLEANUP  # noqa: E402

SITE = "cowork.regression.localhost"
USER = "admin@cowork.regression"
TURN_MAX = 0.35
CHIP = "[data-slot=\"cc-quick-replies\"] button"
LEAKS = [re.compile(r"\boperator\b", re.I), re.compile(r"wrong_mode"), re.compile(r"CWH-"), re.compile(r"\b[0-9a-f]{32,64}\b")]
LINES = {"direct": "{tag} 把单号 {ref} 这张采购单录进 Express", "operator": "{tag} 把这家这个月的单录进 Express"}


def drive(sid, steps, timeout=1500):
    job = {"base": f"http://{SITE}:{RUN.PORT}", "cookie_domain": SITE, "sid": sid, "start": "/assistant", "mobile": False,
           "width": 375, "video": "", "steps": steps,
           "ready": "document.readyState === 'complete' && !!document.querySelector('textarea[aria-label]')"}
    env = dict(os.environ, SMOKE_CHROME_RULES=f"MAP {SITE} 127.0.0.1")
    out = subprocess.run(["node", str(ROOT / "scripts/regression/ui_steps.mjs"), json.dumps(job)], capture_output=True, text=True,
                         env=env, timeout=timeout).stdout
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


# 页面里：这段对话 CC 调过哪些工具（按事件里的 agent.custom_tool_use，同对话页取事件的接口）
TOOLS_JS = JS + """
// 正在看的对话：地址带 ?c=<编号>（真模型起的标题不含标记，不按标题找）
const sidOf=async()=>new URL(location.href).searchParams.get('c')||'';
const toolsOf=async(sid)=>{ const page=(await call('pearnly_core.assistant.api.events',{session_id:sid}))||{};
  return (page.events||[]).filter(e=>e.type==='agent.custom_tool_use'||e.type==='agent.tool_use').map(e=>e.name); };
const idle=()=>!q('[data-slot="cc-run-indicator"]');
const said=()=>qa('[data-role="assistant"]').map(e=>e.innerText.trim());
// 这一轮说完：没有「正在…」的状态行，助手的字 8 秒没变（工具一步步跑时状态行会短暂消失，不能只看一眼）
const settled=async(limit)=>{ let last='', same=0; for(let k=0;k<limit;k++){ await sleep(2000);
  const now=said().join('|'); const busy=!idle()||/正在/.test(now.slice(-200));
  if(!busy&&now&&now===last){ if(++same>=4) return true; } else same=0; last=now; } return false; };
"""


def steps(mode, params):
    tag, line = params["tag"], LINES[mode].format(**params)
    kind = "chat" if mode == "direct" else "operator"
    return [
        {"name": "说一句要录", "js": TOOLS_JS + f"await toMode({json.dumps(kind)}); await until(()=>q('[data-slot=\"cc-chin-company\"]')); "
         f"const picked=await pickCompany({json.dumps(params['title'])}); const sent=await say({json.dumps(line)}); "
         "const done=await settled(300); "
         "const sid=await sidOf(); const tools=sid?await toolsOf(sid):[]; "
         f"const chip=await until(()=>!!q({json.dumps(CHIP)}),30); "
         "return {picked, sent, done, sid, tools, chip, replies: said().map(t=>t.slice(0,600))};"},
        {"name": "点写入", "js": TOOLS_JS + f"const b=q({json.dumps(CHIP)}); if(!b) return {{clicked:false}}; b.click(); "
         "await sleep(3000); const done=await settled(300); "
         "const sid=await sidOf(); const tools=sid?await toolsOf(sid):[]; "
         f"return {{clicked:true, done, sid, tools, chip: !!q({json.dumps(CHIP)}), replies: said().map(t=>t.slice(0,600))}};"},
    ]


def prepare(lab, mode):
    ref = "WPR-" + uuid.uuid4().hex[:6].upper()
    out = lab.python(SITE, CLEANUP + BASE + f"""
name = recognized({ref!r})
frappe.db.set_value("User", {USER!r}, "language", "zh")
frappe.db.commit()
print(json.dumps({{"company": company["id"], "title": company["title"], "doc": name, "period": period}}))
""")
    return {**out, "ref": ref, "tag": "wp" + uuid.uuid4().hex[:6], "mode": mode}


def verify(lab, params, sid):
    found = lab.python(SITE, f"""
frappe.set_user({USER!r})  # 代录任务只给本人读（tasks.read）
plans = frappe.get_all("Pearnly Assistant Write Plan", filters={{"session_id": {sid!r}}},
                       fields=["name", "status", "mode", "creation", "started_at", "user_turn", "documents"], order_by="creation asc")
records = frappe.get_all("Cowork Push Record", filters={{"history": {params['doc']!r}}}, fields=["name", "status", "creation"])
row = frappe.db.get_value("Pearnly Assistant Session", {{"session_id": {sid!r}}}, ["operator_task", "user_turn"], as_dict=True) or {{}}
task = {{}}
if row.get("operator_task"):
    from pearnly_core.express_operator import tasks
    task = tasks.read(row["operator_task"])
    task = {{"status": task.get("status"), "documents": len(task.get("documents") or []), "started_at": task.get("started_at")}}
print(json.dumps({{"plans": plans, "records": records, "task": task, "turn": row.get("user_turn")}}, default=str))
""")
    return found


def judge(mode, params, results, found):
    by = {r.get("step"): r.get("value") or {} for r in results}
    first, second = by.get("说一句要录") or {}, by.get("点写入") or {}
    started = [p for p in found["plans"] if p["status"] == "started"]
    begun = min((p["started_at"] for p in started if p.get("started_at")), default=None)
    writes = [r["creation"] for r in found["records"]] if mode == "direct" else ([found["task"]["started_at"]]
                                                                                  if found["task"].get("started_at") else [])
    # 合成账套名「Flow operator 69」、公司「Flow Operator Co」是造的数据，不算内部词
    text = " ".join((first.get("replies") or []) + (second.get("replies") or [])).replace("Flow operator", "").replace("Flow Operator", "")
    leaks = [p.pattern for p in LEAKS if p.search(text)]
    return [
        {"case": "① 第一句之后 CC 建了写入计划、没开始；输入框上方出「写入」",
         "seen": {k: first.get(k) for k in ("tools", "chip", "replies")},
         "ok": "pearnly_write_plan" in (first.get("tools") or []) and "pearnly_write_start" not in (first.get("tools") or [])
         and bool(first.get("chip"))},
        {"case": "② 用户点头之前什么都没写（写入都晚于计划开始）", "seen": {"started": begun, "writes": writes},
         "ok": bool(begun) and all(str(w) >= str(begun) for w in writes)},
        {"case": "③ 点「写入」后 CC 按计划开始，计划 started，写了（直写有推送记录 / 代录任务交出去），「写入」消失",
         "seen": {"tools": second.get("tools"), "chip": second.get("chip"), "plans": [(p["status"], p["mode"]) for p in found["plans"]],
                  "records": len(found["records"]), "task": found["task"]},
         "ok": bool(second.get("clicked")) and "pearnly_write_start" in (second.get("tools") or []) and bool(started)
         and not second.get("chip")
         and (len(found["records"]) >= 1 if mode == "direct" else found["task"].get("status") in ("queued", "running", "attention",
                                                                                                    "finished", "stopped"))},
        {"case": "④ 回话里没有内部词", "seen": leaks, "ok": not leaks},
    ]


def run_one(lab, mode):
    params = prepare(lab, mode)
    sid = lab.session(SITE, USER)
    try:
        results = drive(sid, steps(mode, params))
    finally:
        lab.drop(SITE, USER, sid)
    by = {r.get("step"): r.get("value") or {} for r in results}
    conversation = (by.get("点写入") or {}).get("sid") or (by.get("说一句要录") or {}).get("sid") or ""
    found = verify(lab, params, conversation) if conversation else {"plans": [], "records": [], "task": {}}
    checks = judge(mode, params, results, found)
    errors = [e for r in results for e in (r.get("page_errors") or [])]
    checks.append({"case": "页面没有报错", "seen": errors, "ok": not errors})
    lab.python(SITE, CLEANUP + f"""
from pearnly_core.assistant import sessions
frappe.set_user({USER!r})
for sid in {json.dumps([conversation] if conversation else [])}:
    sessions.delete(sid)
cleanup()
frappe.db.commit()
print("{{}}")
""")
    return {"mode": mode, "ok": all(c["ok"] for c in checks), "checks": checks, "found": found,
            "steps": [{"step": r.get("step"), "value": r.get("value")} for r in results]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["direct", "operator", "all"], default="all")
    parser.add_argument("--out", default="")
    parser.add_argument("--budget", type=float, default=1.4, help="最多花多少美元（每轮按 0.35 估，超了不开下一遍）")
    parser.add_argument("--engine-on", metavar="密钥文件")
    parser.add_argument("--engine-off", action="store_true")
    parser.add_argument("--src", default="")
    args = parser.parse_args(argv)
    lab = RUN.Lab()
    if args.engine_on or args.engine_off:
        if not args.src:
            parser.error("--engine-on / --engine-off 要 --src")
        PREFLIGHT.engine(lab, args.src, "real" if args.engine_on else "off", args.engine_on or "")
        print("真引擎已接上" if args.engine_on else "已回到流程用的样子（假智能体）")
        return 0
    results, spent = [], 0.0
    for mode in (["direct", "operator"] if args.mode == "all" else [args.mode]):
        cost = 2 * TURN_MAX
        if spent + cost > args.budget:
            print(f"停下：这一遍最多约 {cost} 美元，已估 {spent}，会超过上限 {args.budget}（{mode} 没跑）", flush=True)
            break
        spent += cost
        started = time.monotonic()
        result = run_one(lab, mode)
        result["seconds"] = round(time.monotonic() - started)
        results.append(result)
        if args.out:  # 每遍跑完就写，后一遍出错也留得下前一遍的结果
            Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str))
        bad = [c for c in result["checks"] if not c["ok"]]
        print(f"{'通过' if result['ok'] else '不通过'} {mode} {result['seconds']}s "
              f"{json.dumps(bad, ensure_ascii=False, default=str)[:1500] if bad else ''}", flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    ok = bool(results) and all(r["ok"] for r in results)
    print(f"场景：{'通过' if ok else '不通过'}（估价上限 {spent} 美元）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
