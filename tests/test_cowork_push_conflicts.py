"""人发起的推送撞上写者锁（1020 / 1213）时整笔重来（`push.application.conflicts` 经共用的 `transaction.conflicts`，10-20 lab）
的离线检查：

前 N 次冲突后成功、用尽后给正式原因（不带 SQL 原文）、别的错误不重来、被回滚那次的提示不带出来；复核页「提交」与 Express
「确认发送」用尽时照旧把原因记在票上；所有共用写者锁的人推入口都经同一处（不一处一补）。不连库：Frappe 用最小替身。
"""
import ast
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
COWORK = ROOT / "apps/pearnly_cowork/pearnly_cowork"
LOCK = "(1020, \"Record has changed since last read in table 'tabCowork Push Writer Lock'; try restarting transaction\")"


class _Frappe(types.ModuleType):
    class ValidationError(Exception):
        pass

    class PermissionError(Exception):
        pass

    class AuthenticationError(Exception):
        pass

    class DoesNotExistError(Exception):
        pass

    class QueryDeadlockError(Exception):
        pass

    def __init__(self):
        super().__init__("frappe")
        self.rollbacks = 0
        self.local = types.SimpleNamespace(message_log=[])
        self.db = types.SimpleNamespace(rollback=self._rollback, get_value=lambda *a, **k: "H1")

    def _rollback(self, save_point=None):
        self.rollbacks += 1

    def throw(self, message, exc=None):
        self.local.message_log.append(message)
        raise (exc or self.ValidationError)(message)

    @staticmethod
    def _(text, lang=None):
        return text

    @staticmethod
    def whitelist(**kwargs):
        return lambda function: function


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _install():
    frappe = _Frappe()
    service = types.ModuleType("pearnly_cowork.push.application.service")
    packages = {name: types.ModuleType(name) for name in
                ("pearnly_cowork", "pearnly_cowork.push", "pearnly_cowork.push.application", "pearnly_cowork.companion",
                 "pearnly_cowork.transaction")}
    with mock.patch.dict(sys.modules, {"frappe": frappe, **packages, service.__name__: service}):
        shared = _load("pearnly_cowork.transaction.conflicts", COWORK / "transaction/conflicts.py")
        packages["pearnly_cowork.transaction"].conflicts = shared
        conflicts = _load("pearnly_cowork.push.application.conflicts", COWORK / "push/application/conflicts.py")
        packages["pearnly_cowork.push.application"].conflicts = conflicts
        packages["pearnly_cowork.push.application"].service = service
        api = _load("pearnly_cowork.push.application.api", COWORK / "push/application/api.py")
        submission = _load("pearnly_cowork.companion.submission", COWORK / "companion/submission.py")
    return frappe, service, shared, conflicts, api, submission


frappe, service, shared, conflicts, api, submission = _install()


class RetriedTests(unittest.TestCase):
    def setUp(self):
        frappe.rollbacks, frappe.local.message_log = 0, []
        self.sleeps, self.calls = [], 0
        for target, name, value in ((shared.time, "sleep", self.sleeps.append),
                                    (shared.random, "uniform", lambda low, high: 1.0),
                                    (shared, "frappe", frappe), (conflicts, "frappe", frappe)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _failing(self, times, error=None):
        def run(value):
            self.calls += 1
            frappe.local.message_log.append(f"attempt {self.calls}")
            if self.calls <= times:
                raise error or frappe.QueryDeadlockError(LOCK)
            return value
        return run

    def test_the_first_conflicts_are_rolled_back_and_retried_with_backoff(self):
        for times in range(1, shared.ATTEMPTS):
            self.setUp()
            frappe.local.message_log.append("before")
            self.assertEqual(conflicts.retried(self._failing(times), "ok"), "ok")
            self.assertEqual((self.calls, frappe.rollbacks), (times + 1, times))
            self.assertEqual(self.sleeps, list(shared.BACKOFF[:times]))
            # 被回滚的那几次弹过的话不带出来，调用之前的照留
            self.assertEqual(frappe.local.message_log, ["before", f"attempt {times + 1}"])

    def test_backoff_is_jittered_within_the_stated_bounds(self):
        seen = []
        with mock.patch.object(shared.random, "uniform", lambda low, high: seen.append((low, high)) or high):
            conflicts.retried(self._failing(1), "ok")
        self.assertEqual(seen, [shared.JITTER])
        self.assertEqual(self.sleeps, [shared.BACKOFF[0] * shared.JITTER[1]])
        self.assertLess(sum(shared.BACKOFF) * shared.JITTER[1], 3)  # 短于 CC 桌面版取单间隔

    def test_exhausted_gives_the_formal_reason_without_the_database_text(self):
        with self.assertRaises(conflicts.Exhausted) as caught:
            conflicts.retried(self._failing(99), "ok")
        self.assertEqual(str(caught.exception), "push.native_lock_conflict")
        self.assertIsInstance(caught.exception, frappe.ValidationError)  # 各入口「失败记在票上」照常接住
        self.assertIsNone(caught.exception.__context__)
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual((self.calls, frappe.rollbacks), (shared.ATTEMPTS, shared.ATTEMPTS))
        self.assertEqual(self.sleeps, list(shared.BACKOFF))
        self.assertEqual(frappe.local.message_log, ["push.native_lock_conflict"])
        # 词条已有（不加新文案）
        terms = (COWORK / "i18n/messages.tsv").read_text(encoding="utf-8")
        self.assertIn("\tpush.native_lock_conflict\n", terms)

    def test_other_errors_are_not_retried(self):
        with self.assertRaises(frappe.ValidationError):
            conflicts.retried(self._failing(1, frappe.ValidationError("push.native_target_changed")), "ok")
        self.assertEqual((self.calls, frappe.rollbacks, self.sleeps), (1, 0, []))


class EntryTests(unittest.TestCase):
    """复核页「提交」与 Express「确认发送」用尽时：整笔回滚、原因记在票上（同它们别的失败，用户 2026-09-26 不抛给页面）。"""

    def setUp(self):
        frappe.local.message_log = []
        self.failed = []
        for target, name, value in (
                (shared.time, "sleep", lambda seconds: None), (shared, "frappe", frappe),
                (service, "submission_failed", lambda history, reason: self.failed.append((history, reason))
                 or {"status": "failed", "reason": reason, "history": history}),
                (conflicts, "frappe", frappe), (api, "frappe", frappe), (submission, "frappe", frappe)):
            patcher = mock.patch.object(target, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_review_page_submit_records_the_reason_once_exhausted(self):
        calls = []

        def submit(*args, **kwargs):
            calls.append(args[2])
            raise frappe.QueryDeadlockError(LOCK)

        with mock.patch.object(service, "submit", submit, create=True):
            out = api.submit("H1", "EXP1", "k1", 1, 3)
        self.assertEqual(out["status"], "failed")
        self.assertEqual(self.failed, [("H1", "push.native_lock_conflict")])
        self.assertEqual(calls, ["k1"] * shared.ATTEMPTS)  # 每次同一请求号

    def test_review_page_submit_retries_then_returns_the_preparation(self):
        attempts = []

        def submit(*args, **kwargs):
            attempts.append(args[2])
            if len(attempts) < 3:
                raise frappe.QueryDeadlockError(LOCK)
            return {"status": "prepared", "name": "CWP-1"}

        with mock.patch.object(service, "submit", submit, create=True):
            self.assertEqual(api.submit("H1", "EXP1", "k1", 1, 3)["status"], "prepared")
        self.assertEqual((len(attempts), self.failed), (3, []))

    def test_express_confirm_records_the_reason_once_exhausted(self):
        def confirm(*args):
            raise frappe.QueryDeadlockError("(1213, 'Deadlock found when trying to get lock')")

        with mock.patch.object(service, "confirm", confirm, create=True):
            out = submission.confirm("CWP-1", 1, "confirm:k1")
        self.assertEqual(out["status"], "failed")
        self.assertEqual(self.failed, [("H1", "push.native_lock_conflict")])


class SamePlaceTests(unittest.TestCase):
    """所有共用写者锁的人推入口都经 `conflicts.retried`：复核页 RPC（准备 / 提交 / 确认 / 撤回 / 结果不明 / 记选择）、Express
    确认发送、CC 工具与待处理页（当场推 / 当场选后重推 / 换账套推 / 提议推 / MR.ERP 当场选）共用的 `tools._push_one`。"""

    @staticmethod
    def _functions(path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}

    def test_every_review_page_rpc_runs_in_the_retried_transaction(self):
        functions = self._functions(COWORK / "push/application/api.py")
        whitelisted = [node for node in functions.values()
                       if any("whitelist" in ast.unparse(item) for item in node.decorator_list)]
        self.assertGreaterEqual(len(whitelisted), 10)
        for node in whitelisted:
            self.assertIn("_local_transaction(", ast.unparse(node), node.name)
        self.assertIn("conflicts.retried(", ast.unparse(functions["_local_transaction"]))
        confirm = self._functions(COWORK / "companion/submission.py")["confirm"]
        self.assertIn("conflicts.retried(_confirm", ast.unparse(confirm))

    def test_cc_tool_and_pending_page_pushes_share_one_retried_path(self):
        functions = self._functions(COWORK / "assistant/tools.py")
        push_one = ast.unparse(functions["_push_one"])
        self.assertIn("conflicts.retried(_submit_one", push_one)
        self.assertIn("conflicts.retried(destinations.of(adapter).confirm", push_one)
        submit_one = ast.unparse(functions["_submit_one"])
        self.assertIn("application.submit(", submit_one)
        self.assertNotIn("destinations.of(", submit_one)
        # 其余地方不直接调写者锁的准备 / 确认：都经 `_push_one`
        for name, node in functions.items():
            if name not in ("_push_one", "_submit_one"):
                text = ast.unparse(node)
                self.assertNotIn("application.submit(", text, name)
                self.assertNotIn(").confirm(", text, name)
        pending = (COWORK / "assistant/pending_api.py").read_text(encoding="utf-8")
        self.assertNotIn("application.submit(", pending)
        self.assertNotIn("application.confirm(", pending)
        self.assertGreaterEqual(pending.count("tools._push_one("), 3)  # 当场推 / 选后重推、MR.ERP 当场选、换账套推


if __name__ == "__main__":
    unittest.main()
