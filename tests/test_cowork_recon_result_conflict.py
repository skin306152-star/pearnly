"""对账出结果撞数据库冲突不再要用户重交、不再付第二次识别费（④ B7，11-28，recon_center/jobs.py）。离线：不连站点，识别、
存储、状态缓存、队列 Redis 都用替身，``jobs.run`` / ``jobs._run`` 用真的。

原来：出结果那段（``create_from_documents`` / ``create_from_parsed`` …）撞死锁进 ``except Exception``：回滚、记「系统繁忙」、
对账判失败，``finally`` 里删掉源文件；用户只能重交 → 新对账号 → 新识别任务名 → 识别再付一次。
现在：是冲突（``conflicts.is_conflict``）就回滚后挂起（``waiting.park``，参数里记次数）：源文件留着，下一轮按原参数重跑，识别按
对账号找回已存的结果、不再付费；挂起次数用尽才按原来的失败处理。

    python3 tests/test_cowork_recon_result_conflict.py   （要 Frappe 镜像里的依赖）
"""
import json
import unittest
from types import SimpleNamespace
from unittest import mock

import frappe

from pearnly_cowork.recon_center import document_ocr, jobs, progress, slots, sources, storage, vat_check, waiting
from pearnly_cowork.reconciliation import income_recon_service, messages


class FakeStore:
    def __init__(self):
        self.values = {}

    def get_value(self, key):
        return self.values.get(key)

    def set_value(self, key, value, expires_in_sec=None):
        self.values[key] = value


class ResultConflict(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.session = SimpleNamespace(user="u@example.com")
        self.paid = []            # 真付费的识别调用（同一对账号、同一份第一次读）
        self.checkpoints = set()  # 识别任务按对账号定名、按页存了检查点：再读同一份取回已存的结果
        self.deleted, self.parked, self.logged, self.finished = [], [], [], []
        self.outcomes = []        # 出结果那段每次的结果：异常就抛，否则回这份
        self.created = 0

        def reader(ocr_job, side, file_name, user):
            def read(document_type, content, filename):
                key = (ocr_job, side, document_type)
                if key not in self.checkpoints:
                    self.checkpoints.add(key)
                    self.paid.append(key)
                return {"pages": []}
            return read

        def ledger_source(content, filename, read=None):
            read("general_ledger", content, filename)
            return {"kind": "excel", "sheets": [[["gl"]]]}

        def report_source(content, filename, read=None):
            read("vat_report", content, filename)
            return {"kind": "excel", "cells": [["vat"]]}

        def create_from_parsed(*args, **kwargs):
            self.created += 1
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        def park(recon_job, arguments, pending):
            self.parked.append((recon_job, arguments, pending))
            return "parked"

        patches = [
            mock.patch.object(frappe, "session", self.session),
            mock.patch.object(frappe, "local", SimpleNamespace(site="t.example", lang="en")),
            mock.patch.object(frappe, "db", mock.MagicMock()),
            mock.patch.object(frappe, "_", side_effect=lambda key: key),
            mock.patch.object(frappe, "log_error", side_effect=lambda **kw: self.logged.append(kw)),
            mock.patch.object(frappe, "get_traceback", return_value="trace"),
            mock.patch.object(jobs.pearnly_state, "store", return_value=self.store),
            mock.patch.object(jobs, "_read", side_effect=lambda names: [(f"{name}.xlsx", b"x") for name in names]),
            mock.patch.object(jobs, "_superseded", return_value=False),
            mock.patch.object(jobs, "_prepare_view"),
            mock.patch.object(jobs, "_announce"),
            mock.patch.object(slots, "acquire", return_value=True),
            mock.patch.object(slots, "release"),
            mock.patch.object(progress, "active_add"),
            mock.patch.object(progress, "active_remove"),
            mock.patch.object(document_ocr, "reader", side_effect=reader),
            mock.patch.object(sources, "ledger_source", side_effect=ledger_source),
            mock.patch.object(sources, "report_source", side_effect=report_source),
            mock.patch.object(sources, "report_pages", return_value=[]),
            mock.patch.object(vat_check, "report_reading", return_value=([], [])),
            mock.patch.object(income_recon_service, "create_from_parsed", side_effect=create_from_parsed),
            mock.patch.object(storage, "delete", side_effect=self.deleted.append),
            mock.patch.object(messages, "error_text", side_effect=lambda error: f"data: {error}"),
            mock.patch.object(waiting, "park", side_effect=park),
            mock.patch.object(waiting, "finish", side_effect=self.finished.append),
            mock.patch("time.time", return_value=1_000_000.0),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_job(self, **arguments):
        values = {"recon_job": "J1", "kind": "income", "workspace": "ws", "title": "I01",
                  "files": {"left": ["F-GL"], "right": ["F-VAT"]}, "options": json.dumps({"lang": "zh"})}
        values.update(arguments)
        jobs.run(**values)
        return values

    def state(self):
        return self.store.values.get(jobs._key("J1")) or {}

    def test_a_deadlock_while_storing_the_result_parks_and_keeps_the_files(self):
        self.outcomes = [frappe.QueryDeadlockError("Deadlock found when trying to get lock"), {"name": "CIR-1"}]
        self.run_job()
        self.assertEqual(len(self.parked), 1, "撞死锁要挂起等下一轮，不判失败")
        recon_job, arguments, pending = self.parked[0]
        self.assertIsInstance(pending, waiting.Pending)
        self.assertEqual(pending.works, [])
        self.assertEqual(pending.due, 1_000_000.0 + jobs.CONFLICT_RETRY_SECONDS)
        self.assertEqual(json.loads(arguments["options"]), {"lang": "zh", "conflict_retries": 1})
        self.assertEqual(self.deleted, [], "挂起时源文件不能删")
        self.assertNotEqual(self.state().get("state"), "failed")
        self.assertEqual(self.logged, [])
        self.assertEqual(self.finished, [])
        self.assertEqual(len(self.paid), 2)

        # 下一轮（每分钟的 tick 按记下的参数放回队列）：识别取回已存的结果，不再付费；出了结果，源文件这才删
        jobs.run(**arguments)
        self.assertEqual(self.created, 2)
        self.assertEqual(len(self.paid), 2, "重跑不能再付识别费")
        self.assertEqual(self.state().get("state"), "done")
        self.assertEqual(self.state().get("task"), "CIR-1")
        self.assertEqual(sorted(self.deleted), ["F-GL", "F-VAT"])
        self.assertEqual(self.finished, ["J1"])

    def test_retries_used_up_fail_as_before_and_drop_the_files(self):
        self.outcomes = [frappe.QueryDeadlockError("Deadlock found when trying to get lock")]
        self.run_job(options=json.dumps({"lang": "zh", "conflict_retries": jobs.CONFLICT_RETRIES}))
        self.assertEqual(self.parked, [])
        self.assertEqual(self.state().get("state"), "failed")
        self.assertEqual(self.state().get("error"), "reconciliation.system_busy")
        self.assertEqual(sorted(self.deleted), ["F-GL", "F-VAT"])
        self.assertEqual([entry["title"] for entry in self.logged], ["cowork reconciliation job"])
        self.assertEqual(self.finished, ["J1"])

    def test_each_conflict_counts_until_the_limit(self):
        self.outcomes = [frappe.QueryDeadlockError("deadlock") for _ in range(jobs.CONFLICT_RETRIES + 1)]
        arguments = self.run_job()
        for _ in range(jobs.CONFLICT_RETRIES):
            arguments = self.parked[-1][1]
            jobs.run(**arguments)
        self.assertEqual(len(self.parked), jobs.CONFLICT_RETRIES)
        self.assertEqual(json.loads(self.parked[-1][1]["options"])["conflict_retries"], jobs.CONFLICT_RETRIES)
        self.assertEqual(self.state().get("state"), "failed")
        self.assertEqual(len(self.paid), 2, "挂起重跑期间识别只付一次")
        self.assertEqual(sorted(self.deleted), ["F-GL", "F-VAT"])

    def test_a_data_problem_still_fails_at_once(self):
        self.outcomes = [frappe.ValidationError("bad file")]
        self.run_job()
        self.assertEqual(self.parked, [])
        self.assertEqual(self.state().get("state"), "failed")
        self.assertEqual(self.state().get("error"), "data: bad file")
        self.assertEqual(sorted(self.deleted), ["F-GL", "F-VAT"])

    def test_the_count_is_not_kept_for_a_preset_rerun(self):
        captured = {}
        self.outcomes = [{"name": "CIR-1", "questions": [{"id": "q"}]}]
        with mock.patch.object(jobs.rerun, "keep", side_effect=lambda *args: captured.update(options=args[6])):
            self.run_job(options=json.dumps({"lang": "zh", "conflict_retries": 2}))
        self.assertEqual(captured["options"], {"lang": "zh"})
        self.assertEqual(self.deleted, [], "要答预设问题的源文件留着")


if __name__ == "__main__":
    unittest.main()
