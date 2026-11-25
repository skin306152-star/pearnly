#!/usr/bin/env python3
"""写与只读判定之间的提交边界（11-25）：在回归实验室的 Cowork 站点上用真 Frappe 复现四条「先写未提交、再进 Cowork 判定」，
修后都不再报 PendingWrites（`transaction.snapshot.require_clean`，判定开头 `precheck_source.document`）。只写实验室站点的造数据，
不连 Express、不调模型、不付费；每条造完即删。

    PEARNLY_FLOWS_LAB=<栈名> REGRESSION_LAB_PORT=… python3 scripts/cowork-commit-boundaries-lab-verify.py

- A1 代录开始卡上答「已入账」（结果不明的单）→ 推送记录核对落定后同一请求进判定：推送记录 success、不再结果不明。
- A2 开始卡上选另一个月份 → 重开卡按新月份判定：任务月份改成。
- B2 写入计划开始时对话存的账套是 11-12 以前的写法：读账套不再顺手记存（只读），计划开始成功。
- B3 对话口径逐张判，第一张结果不明提前返回：读账套不记存，第二张不被拒，计划做得出来。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "flows"))
import run  # noqa: E402
import cc_conversation_ledger as ledger  # noqa: E402
import cowork_cc_guide_docs_lib as docs  # noqa: E402
from cowork_operator_lib import BASE, CLEANUP  # noqa: E402

SITE = "cowork-new.regression.localhost"

UNKNOWN = """
def unknown(number):
    name = recognized(number)
    record = prepared(name)
    native.lease(record, context=context_of(record), adapter=writer, now=datetime.now(timezone.utc) - timedelta(hours=1))
    frappe.db.commit()
    native.recover_expired(record, context=context_of(record), adapter=writer, now=datetime.now(timezone.utc))
    frappe.db.commit()
    return name, record
from pearnly_core.assistant import sessions, write_plan
from pearnly_core.assistant.guide import run
from pearnly_core.express_operator import guide_start
def attempt(fn):
    try:
        out = fn()
        frappe.db.commit()
        return out, ""
    except Exception as error:
        frappe.db.rollback()
        return None, type(error).__name__ + ": " + str(error)[:300]
def drop(sid):
    frappe.local.pearnly_assistant = None
    sessions.delete(sid)
    cleanup()
    frappe.db.commit()
"""

A1 = CLEANUP + BASE + UNKNOWN + """
name, record = unknown("FLOW-A1-%(tag)s")
made = sessions.create(project=company["id"], period=period, mode="operator")
frappe.db.commit()
started = guide_start.start(made["task"])
frappe.db.commit()
key = next(q["key"] for q in started["card"]["questions"] if q["key"].startswith(guide_start.RESOLVE_PREFIX))
got, error = attempt(lambda: run.answer(started["run"], {"answers": [{"key": key, "value": guide_start.ENTERED}]}))
state = frappe.db.get_value("Cowork Push Record", record, ["status", "result_unknown"], as_dict=True)
drop(made["id"])
print(json.dumps({"error": error, "status": state.status, "unknown": int(state.result_unknown or 0)}))
"""

A2 = CLEANUP + BASE + UNKNOWN + """
recognized("FLOW-A2-%(tag)s")
y, m = int(period[:4]), int(period[5:7])
empty = f"{y - 1 if m == 1 else y}-{12 if m == 1 else m - 1:02d}"
made = sessions.create(project=company["id"], period=empty, mode="operator")
frappe.db.commit()
started = guide_start.start(made["task"])
frappe.db.commit()
got, error = attempt(lambda: run.answer(started["run"], {"answers": [{"key": guide_start.MONTH_KEY, "value": period}]}))
moved = frappe.db.get_value("Pearnly Express Operator Task", made["task"], "period")
drop(made["id"])
print(json.dumps({"error": error, "period": moved, "wanted": period}))
"""

B2 = docs._seed_code() + """
from pearnly_core.assistant import sessions, write_plan
name = guided_doc("purchase", "FLOW-B2-%(tag)s", %(day)r)
made = sessions.create(project=company["id"], period=%(month)r, ledger=ids.get(%(t8)r, ""))
sid = made["id"]
frappe.db.commit()
frappe.local.pearnly_assistant = {"session_id": sid}
plan = write_plan.make({"documents": [name]})
frappe.db.commit()
row = frappe.db.get_value("Pearnly Assistant Session", {"session_id": sid}, ["name", "user_turn"], as_dict=True)
frappe.db.set_value("Pearnly Assistant Session", row.name, {"project_ledger": conn["name"] + "|FLOWT8",
                    "user_turn": int(row.user_turn or 0) + 1}, update_modified=False)
frappe.db.commit()
error = ""
try:
    got = write_plan.start({"plan": plan.get("plan")})
    frappe.db.commit()
except Exception as exc:
    frappe.db.rollback()
    got, error = {}, type(exc).__name__ + ": " + str(exc)[:300]
frappe.local.pearnly_assistant = None
sessions.delete(sid)
cleanup()
frappe.db.commit()
print(json.dumps({"error": error, "status": (got or {}).get("status")}))
"""

B3 = CLEANUP + BASE + UNKNOWN + """
first, record = unknown("FLOW-B3A-%(tag)s")
second = recognized("FLOW-B3B-%(tag)s")
made = sessions.create(project=company["id"], period=period)
row = frappe.db.get_value("Pearnly Assistant Session", {"session_id": made["id"]}, "name")
frappe.db.set_value("Pearnly Assistant Session", row, "project_ledger", conn["name"] + "|FLOWOP", update_modified=False)
frappe.db.commit()
frappe.local.pearnly_assistant = {"session_id": made["id"]}
got, error = attempt(lambda: write_plan.make({"documents": [first, second]}))
drop(made["id"])
print(json.dumps({"error": error, "status": (got or {}).get("status"), "count": (got or {}).get("count")}))
"""


def main() -> int:
    lab = run.Lab()
    tag = uuid.uuid4().hex[:6].upper()
    values = {"tag": tag, "day": ledger.CHAT_MONTH + "-01", "month": ledger.CHAT_MONTH, "t8": ledger.T8}
    cases = {
        "A1 开始卡答「已入账」": (A1, lambda o: not o["error"] and o["status"] == "success" and o["unknown"] == 0),
        "A2 开始卡改月份": (A2, lambda o: not o["error"] and o["period"] == o["wanted"]),
        "B2 写入计划开始（旧写法账套）": (B2, lambda o: not o["error"] and o["status"] == "started"),
        "B3 逐张判（第一张结果不明）": (B3, lambda o: not o["error"] and o["status"] == "ready" and o["count"] == 1),
    }
    failed = 0
    for title, (body, ok) in cases.items():
        out = lab.python(SITE, body % values)
        good = ok(out)
        failed += not good
        print(("通过 " if good else "不通过 ") + title + " " + json.dumps(out, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
