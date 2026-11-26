"""关掉快照隔离之后（11-23）Frappe 保存前比对撞上的并发修改，与 1020 / 1213 同样整笔回滚重来（`transaction.conflicts.retried`）的离线检查。

11-26 回归实验室：并发出采购凭证（`erp.purchase.voucher-concurrent`）在隔离开着的线上版 10 / 10 过，关掉后 7 / 10 有一个请求 417
``TimestampMismatchError``——先普通读过、再加锁、再普通读出旧副本，``doc.save()`` 的 ``check_if_latest`` 对不上；开着隔离时同一处是
加锁读报 1020、被这里重来接住。检查：Frappe 保存前比对抛的那一种重来（次数、回滚、提示不带出与 1020 相同），用尽抛 Exhausted；
业务自己抛的 ``TimestampMismatchError``（「记录变了、请刷新」）原样抛出、不重来；替身里没有这个异常类时别的错误照旧原样抛出。
不连库：Frappe 用最小替身，保存前比对放在一个路径为 ``frappe/model/document.py`` 的替身模块里（判定按抛出位置认）。
"""
import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFLICTS = ROOT / "apps/pearnly_core/pearnly_core/transaction/conflicts.py"

DOCUMENT = '''
import frappe


def check_if_latest():
    frappe.local.message_log.append("Error: has been modified after you have opened it")
    raise frappe.TimestampMismatchError("modified after you have opened it")
'''


class _Frappe(types.ModuleType):
    class ValidationError(Exception):
        pass

    class QueryDeadlockError(Exception):
        pass

    class TimestampMismatchError(ValidationError):
        pass

    def __init__(self):
        super().__init__("frappe")
        self.rollbacks = 0
        self.local = types.SimpleNamespace(message_log=[])
        self.flags = {}
        self.db = types.SimpleNamespace(rollback=self._rollback)

    def _rollback(self, save_point=None):
        self.rollbacks += 1


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StaleSaveRetryTest(unittest.TestCase):
    def setUp(self):
        self.frappe = _Frappe()
        modules = mock.patch.dict(sys.modules, {"frappe": self.frappe})
        modules.start()
        self.addCleanup(modules.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = pathlib.Path(folder.name) / "frappe" / "model" / "document.py"
        path.parent.mkdir(parents=True)
        path.write_text(DOCUMENT, encoding="utf-8")
        self.document = _load("frappe_model_document_fake", path)
        self.conflicts = _load("pearnly_core.transaction.conflicts", CONFLICTS)
        sleep = mock.patch.object(self.conflicts.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def _stale_then_ok(self, failures):
        calls = []

        def attach():
            calls.append(1)
            if len(calls) <= failures:
                self.document.check_if_latest()   # 同 doc.save() 里 Frappe 保存前比对
            return "attached"
        return attach, calls

    def test_stale_save_is_retried_like_1020(self):
        attach, calls = self._stale_then_ok(2)
        self.assertEqual(self.conflicts.retried(attach), "attached")
        self.assertEqual((len(calls), self.frappe.rollbacks), (3, 2))
        self.assertEqual(self.frappe.local.message_log, [], "被回滚那次的 Frappe 提示不带给重来的那次")

    def test_stale_save_every_time_exhausts_without_frappe_text(self):
        attach, calls = self._stale_then_ok(99)
        with self.assertRaises(self.conflicts.Exhausted) as caught:
            self.conflicts.retried(attach)
        self.assertEqual(len(calls), self.conflicts.ATTEMPTS)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def test_business_mismatch_is_raised_unchanged(self):
        calls = []

        def confirm():
            calls.append(1)
            raise self.frappe.TimestampMismatchError("The record changed. Reload it before saving.")
        with self.assertRaises(self.frappe.TimestampMismatchError):
            self.conflicts.retried(confirm)
        self.assertEqual((len(calls), self.frappe.rollbacks), (1, 0))

    def test_deadlock_still_retried(self):
        calls = []

        def write():
            calls.append(1)
            if len(calls) == 1:
                raise self.frappe.QueryDeadlockError("1020")
            return "ok"
        self.assertEqual(self.conflicts.retried(write), "ok")
        self.assertEqual(self.frappe.rollbacks, 1)

    def test_other_errors_are_not_retried_even_without_the_frappe_class(self):
        self.frappe.TimestampMismatchError = None

        def boom():
            raise self.frappe.ValidationError("no")
        with self.assertRaises(self.frappe.ValidationError):
            self.conflicts.retried(boom)
        self.assertEqual(self.frappe.rollbacks, 0)


if __name__ == "__main__":
    unittest.main()
