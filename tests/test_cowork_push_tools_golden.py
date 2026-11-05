"""Cowork 对话直写工具逐字节锁（评估 2026-11-02 §13 第 9 步：与 ERP 共用的部分下沉 `pearnly_core` 前后，Cowork 不变）。

黄金样本 `fixtures/assistant/cowork-push-tools-golden.json` 在**下沉之前**的代码上生成并单独提交（``--write``）。之后每次用同一批
固定输入重算、逐项比对：

- 工具声明：Cowork 全部工具（名字、步骤名、给模型的说明、参数、处理函数、要不要「允许」…），整份序列化后逐字节相同；
- 说话规矩（`GUIDANCE`）与进行中的说法（`step_names.STEP_NAMES`）；
- 若干调用结果：列单（按页、带推送时刻换成当地时间）、推送结果、推送（已入账的不再推、没有去向的）、要选的项、记选择、
  结果不明的核对（找不到、没有推送记录、记下、被拒），以及给模型的 JSON 时间换算。

替身与 `test_cowork_assistant_tools` 同一套（同一个 `_install`）。
"""
import json
import sys
import types
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import test_cowork_assistant_tools as harness

frappe, tools = harness.frappe, harness.tools
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "assistant" / "cowork-push-tools-golden.json"
STEP_NAMES = harness.COWORK / "assistant/step_names.py"


def _steps() -> dict:
    module = types.ModuleType("cowork_step_names_golden")
    exec(compile(STEP_NAMES.read_text(encoding="utf-8"), str(STEP_NAMES), "exec"), module.__dict__)
    return module.STEP_NAMES


def _listing(case) -> dict:
    harness.ListAndRunsTests.setUp(case)
    patcher = mock.patch.object(frappe, "utils", types.SimpleNamespace(get_system_timezone=lambda: "Asia/Bangkok"),
                                create=True)
    patcher.start()
    case.addCleanup(patcher.stop)
    frappe.rows[0].update(last_push_status="success", last_pushed_at=datetime(2026, 10, 13, 8, 40, 5),
                          creation=datetime(2026, 10, 13, 15, 30, 9, 120000), invoice_date=date(2026, 10, 1),
                          invoice_no="RR-1", seller_name="Thai Steel")
    frappe.rows[1].update(last_push_status="manual", last_push_reason="choice_needed:account",
                          creation=datetime(2026, 10, 13, 15, 31))
    return {
        "list_page": tools.list_documents({"offset": 0, "limit": 2, "connection": "EXP1", "account_set_key": "K"}),
        "list_success": tools.list_documents({"push_state": "success", "limit": 1}),
        "list_waiting": tools.list_documents({"push_state": "waiting", "limit": 3, "account_set_key": "K"}),
        "push_results_failed": tools.list_documents({"push_state": "failed", "limit": 5, "account_set_key": "K"}),
        "json_times": tools._json({"at": datetime(2026, 10, 13, 8, 43), "local": "2026-10-13 15:44:59.5",
                                   "day": date(2026, 10, 20), "kept": "2026-10-13 15:40:12.123456"},
                                  utc=("at",), local=("local",)),
    }


def _follow_up(case) -> dict:
    out = {}
    doc = types.SimpleNamespace(push_connection="EXP1", push_account_set="K", posting_kind="stock",
                                last_push_record="PR1", last_push_status="manual")
    reads = {"H1": doc, "H0": types.SimpleNamespace(push_connection="EXP1", push_account_set="K", posting_kind="",
                                                    last_push_record="", last_push_status="")}

    def read(name):
        if name not in reads:
            raise frappe.DoesNotExistError(name)
        return reads[name]

    calls = []

    def refused(*a):
        raise frappe.ValidationError("This push is not waiting for a confirmation.")

    for target, name, value in (
            (tools.storage, "read", read),
            (tools.application_api, "resolve_unknown",
             lambda *a: calls.append(("resolve",) + a) or {"status": "success", "docnum": "RR6910-007", "reason": ""}),
            (frappe.db, "get_value", lambda *a, **k: "express")):
        patcher = mock.patch.object(target, name, value, create=True)
        patcher.start()
        case.addCleanup(patcher.stop)
    out["resolve_not_found"] = tools.resolve_push_result({"document": "NOPE", "entered": True})
    out["resolve_no_push"] = tools.resolve_push_result({"document": "H0", "entered": False})
    out["resolve_saved"] = tools.resolve_push_result({"document": "H1", "entered": True, "docnum": "RR6910-007"})
    out["resolve_not_entered"] = tools.resolve_push_result({"document": "H1", "entered": False})
    with mock.patch.object(tools.application_api, "resolve_unknown", refused):
        out["resolve_refused"] = tools.resolve_push_result({"document": "H1", "entered": True})
    out["calls"] = json.dumps(calls, ensure_ascii=False, default=str)
    return out


def _push(case) -> dict:
    docs = {"A": types.SimpleNamespace(last_push_status="success"),
            "B": types.SimpleNamespace(last_push_status="", push_connection=""),
            "C": types.SimpleNamespace(last_push_status="pending")}
    patcher = mock.patch.object(tools.storage, "read", lambda name: docs[name])
    patcher.start()
    case.addCleanup(patcher.stop)
    return {"push_already_and_no_target": tools.push_documents({"documents": ["A", "B", "C"]})}


def snapshot() -> dict:
    class Case(unittest.TestCase):  # 只借 addCleanup / doCleanups；放在函数里，不被当成一条测试
        def runTest(self):
            pass

    found = {"tools": json.dumps(tools.TOOLS, ensure_ascii=False, sort_keys=True),
             "guidance": json.dumps(tools.GUIDANCE, ensure_ascii=False),
             "step_names": json.dumps(_steps(), ensure_ascii=False, sort_keys=True)}
    for build in (_listing, _follow_up, _push):
        case = Case()
        try:
            found.update(build(case))
        finally:
            case.doCleanups()
    return found


class CoworkPushToolsGoldenTests(unittest.TestCase):
    maxDiff = None

    def test_cowork_tools_are_byte_identical_to_the_golden_sample(self):
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        now = snapshot()
        self.assertEqual(sorted(now), sorted(golden))
        for key in golden:
            self.assertEqual(now[key], golden[key], key)


if __name__ == "__main__":
    if "--write" in sys.argv:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(snapshot(), ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {GOLDEN}")
    else:
        unittest.main()
