"""CC 桌面版的电脑侧短事务撞上 1020 / 1213 时整笔回滚、退避后重来，和人推同一个机制（`transaction.conflicts`，10-20）的离线检查。

D9 压测第 4 轮：1020 共 8 次，全在 register 对 `Cowork Express Agent Credential` 的加锁读（同一台电脑的心跳刚改过那把密钥行的
报到时间）。电脑登记 / 移除（`api._local_transaction`）、心跳（`heartbeat.report`）、领单 / 回执（`queue_api._run`）、认电脑
（`device_api._guarded`）、按需请求（`request_api._guarded`）、机器认证（`http_auth`）原来各写一个「不等、三次」的循环，
按需请求没有；现在都经同一处。用尽时各回原有的失败形状：register / 移除照旧是 ``QueryDeadlockError``（只是不带数据库原文），
机器方法照旧回「忙」（`http_auth.raise_busy`）。被回滚的那次不留东西（替身库：回滚丢掉这一笔里写的，提交才算数）。
不连库：Frappe 用最小替身，模块按文件载入。
"""
from pathlib import Path
import ast
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))  # Express 写账规则与 CC 线上约定在共用层（ADR-025）
COWORK = ROOT / "apps/pearnly_cowork/pearnly_cowork"
COMPANION = COWORK / "companion"
LOCK_1020 = ("(1020, \"Record has changed since last read in table 'tabCowork Express Agent Credential'; "
             "try restarting transaction\")")
LOCK_1213 = "(1213, 'Deadlock found when trying to get lock; try restarting transaction')"


class _DB:
    """一笔事务里的写先记在 ``pending``，提交才进 ``rows``；整笔回滚丢掉 ``pending``，回保存点只丢保存点之后的。"""

    def __init__(self):
        self.rows, self.pending, self.points = [], [], {}
        self.rollbacks = self.point_rollbacks = 0
        self.fail_rollback = False

    def write(self, value):
        self.pending.append(value)

    def savepoint(self, name):
        self.points[name] = len(self.pending)

    def rollback(self, save_point=None):
        if save_point:
            self.point_rollbacks += 1
            del self.pending[self.points[save_point]:]
            return
        if self.fail_rollback:
            raise RuntimeError("connection lost during rollback")
        self.rollbacks += 1
        self.pending.clear()

    def commit(self):
        self.rows.extend(self.pending)
        self.pending.clear()

    def get_value(self, *args, **kwargs):
        return None


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
        self.db = _DB()
        self.local = types.SimpleNamespace(message_log=[])
        self.flags = {}

    def throw(self, message, exc=None):
        self.local.message_log.append(message)
        raise (exc or self.ValidationError)(message)

    @staticmethod
    def _(text, lang=None):
        return text

    @staticmethod
    def whitelist(**kwargs):
        return lambda function: function


class _Busy(Exception):
    pass


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _module(name, **values):
    module = types.ModuleType(name)
    module.__dict__.update(values)
    return module


def _install():
    frappe = _Frappe()
    busy = []

    def raise_busy(error, *, stage):
        busy.append((stage, error))
        raise _Busy(stage) from None

    class ReportError(Exception):
        def __init__(self, code):
            super().__init__(code)
            self.code = code

    class CredentialError(Exception):
        def __init__(self, code):
            super().__init__(code)
            self.code = code

    class ContractError(Exception):
        def __init__(self, code, **extra):
            super().__init__(code)
            self.code, self.extra = code, extra

    class QueueContractError(Exception):
        code = "companion.queue_request_invalid"

    http_auth = _module("pearnly_cowork.companion.http_auth", raise_busy=raise_busy,
                        current_request=lambda operation="heartbeat": ({"operation": operation}, {"body": 1}),
                        clear_private_messages=lambda: frappe.local.message_log.clear(),
                        reject_request=lambda *a, **k: (_ for _ in ()).throw(_Busy("rejected")),
                        _deny_permission=lambda: (_ for _ in ()).throw(_Busy("denied")))
    companion = _module("pearnly_cowork.companion", http_auth=http_auth,
                        credentials=_module("pearnly_cowork.companion.credentials"),
                        pool=_module("pearnly_cowork.companion.pool"),
                        devices=_module("pearnly_cowork.companion.devices"),
                        release=_module("pearnly_core.cc_desktop.release"),
                        transport=_module("pearnly_cowork.companion.transport"),
                        queue_contracts=_module("pearnly_core.cc_desktop.wire.queue_contracts",
                                                QueueContractError=QueueContractError),
                        device_contracts=_module("pearnly_cowork.companion.device_contracts", PROTOCOL=1),
                        request_service=_module("pearnly_cowork.companion.request_service"),
                        request_contracts=_module("pearnly_core.cc_desktop.wire.request_contracts",
                                                  ContractError=ContractError))
    connections = _module("pearnly_cowork.connections", access=_module("pearnly_cowork.connections.access"),
                          collector=_module("pearnly_cowork.connections.collector"),
                          model=_module("pearnly_cowork.connections.model",
                                        ConnectionError=type("ConnectionError", (Exception,), {})))
    stubs = {
        "frappe": frappe,
        "pearnly_cowork": _module("pearnly_cowork"),
        "pearnly_core.transaction": _module("pearnly_core.transaction"),
        "pearnly_cowork.companion": companion,
        "pearnly_cowork.companion.contracts": _module("pearnly_cowork.companion.contracts",
                                                      CredentialError=CredentialError),
        "pearnly_cowork.connections": connections,
        "pearnly_cowork.connections.reports": _module("pearnly_cowork.connections.reports", ReportError=ReportError,
                                                      plan_report=None),
        "pearnly_cowork.push": _module("pearnly_cowork.push"),
        "pearnly_cowork.push.native": _module("pearnly_cowork.push.native"),
        "pearnly_cowork.push.native.service": _module("pearnly_cowork.push.native.service"),
        "pearnly_cowork.targets": _module("pearnly_cowork.targets"),
        "pearnly_cowork.targets.projection": _module("pearnly_cowork.targets.projection",
                                                     ProjectionContractError=type("ProjectionContractError",
                                                                                  (Exception,), {})),
    }
    with mock.patch.dict(sys.modules, stubs):
        shared = _load("pearnly_core.transaction.conflicts", Path(__file__).resolve().parents[1] / "apps/pearnly_core/pearnly_core/transaction/conflicts.py")
        stubs["pearnly_core.transaction"].conflicts = shared
        modules = {name: _load(f"pearnly_cowork.companion.{name}", COMPANION / f"{name}.py")
                   for name in ("api", "heartbeat", "queue_api", "device_api", "request_api")}
    errors = types.SimpleNamespace(ReportError=ReportError, CredentialError=CredentialError,
                                   ContractError=ContractError)
    return frappe, shared, modules, companion, busy, errors


frappe, shared, modules, companion, busy, errors = _install()


class _Case(unittest.TestCase):
    def setUp(self):
        frappe.db = _DB()
        frappe.local = types.SimpleNamespace(message_log=[])
        busy.clear()
        self.sleeps, self.calls = [], 0
        for target, name, value in ((shared.time, "sleep", self.sleeps.append),
                                    (shared.random, "uniform", lambda low, high: 1.0)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def failing(self, times, value, lock=LOCK_1020, error=None):
        """前 ``times`` 次：先写一行（半截写入），再撞锁 / 报错；之后写一行并返回 ``value``。"""
        def run(*args, **kwargs):
            self.calls += 1
            frappe.db.write(("attempt", self.calls))
            frappe.local.message_log.append(f"attempt {self.calls}")
            if self.calls <= times:
                raise error or frappe.QueryDeadlockError(lock)
            return value
        return run

    def assert_retried(self, times):
        self.assertEqual((self.calls, frappe.db.rollbacks), (times + 1, times))
        self.assertEqual(self.sleeps, list(shared.BACKOFF[:times]))
        # 被回滚的那几次什么也没留下：只剩成功那次写的一行
        frappe.db.commit()
        self.assertEqual(frappe.db.rows, [("attempt", times + 1)])


class RegisterTests(_Case):
    """电脑登记（对话页每次打开 CC 桌面版都调）与移除电脑：`api._local_transaction`。"""

    def register(self):
        return modules["api"].register("rk-1", express=True, device_key="k" * 43, device_name="PC")

    def test_lock_conflicts_on_the_first_one_to_three_attempts_are_retried(self):
        for lock in (LOCK_1020, LOCK_1213):
            for times in range(1, shared.ATTEMPTS):
                self.setUp()
                with mock.patch.object(companion.credentials, "register",
                                       self.failing(times, {"status": "registered"}, lock), create=True):
                    self.assertEqual(self.register(), {"status": "registered"})
                self.assert_retried(times)
                self.assertTrue(frappe.local.cowork_private_response)

    def test_four_conflicts_give_the_existing_error_without_the_database_text(self):
        with mock.patch.object(companion.credentials, "register", self.failing(99, None), create=True):
            with self.assertRaises(frappe.QueryDeadlockError) as caught:
                self.register()
        self.assertEqual(caught.exception.args, ())  # 没有 1020 / 表名原文
        self.assertIsNone(caught.exception.__context__)
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual((self.calls, frappe.db.rollbacks), (shared.ATTEMPTS, shared.ATTEMPTS))
        self.assertEqual(self.sleeps, list(shared.BACKOFF))
        self.assertEqual(frappe.db.pending + frappe.db.rows, [])  # 一次也没留下

    def test_other_errors_are_not_retried(self):
        with mock.patch.object(companion.credentials, "register",
                               self.failing(1, None, error=frappe.PermissionError("Not Permitted")), create=True):
            with self.assertRaises(frappe.PermissionError):
                self.register()
        self.assertEqual((self.calls, frappe.db.rollbacks, self.sleeps), (1, 0, []))

    def test_register_hands_credentials_the_read_view_end(self):
        """电脑登记锁连接行之前由这里结束读视图（提交；`credentials.register` 自己不提交）：之后的读视图建在拿到锁之后。"""
        seen = []

        def register(*args, before_lock=None):
            frappe.db.write("read only so far")
            before_lock()
            seen.append(list(frappe.db.rows))
            return {"status": "exists"}

        with mock.patch.object(companion.credentials, "register", register, create=True):
            self.assertEqual(self.register(), {"status": "exists"})
        self.assertEqual(seen, [["read only so far"]])  # 调 before_lock 时提交了

    def test_remove_device_uses_the_same_transaction(self):
        with mock.patch.object(companion.credentials, "remove_device", self.failing(2, {"removed": True}), create=True):
            self.assertEqual(modules["api"].remove_device("DEV-1"), {"removed": True})
        self.assert_retried(2)


class HeartbeatTests(_Case):
    """心跳（`heartbeat.report`）：撞锁整笔重来；用尽回「忙」，CC 桌面版本来就忽略、下一轮再报。"""

    def test_lock_conflicts_on_the_first_one_to_three_attempts_are_retried(self):
        for lock in (LOCK_1020, LOCK_1213):
            for times in range(1, shared.ATTEMPTS):
                self.setUp()
                with mock.patch.object(modules["heartbeat"], "_receive", self.failing(times, {"ok": True}, lock)):
                    self.assertEqual(modules["heartbeat"].report(), {"ok": True})
                self.assert_retried(times)

    def test_four_conflicts_answer_busy(self):
        with mock.patch.object(modules["heartbeat"], "_receive", self.failing(99, None)):
            with self.assertRaises(_Busy):
                modules["heartbeat"].report()
        self.assertEqual([stage for stage, _ in busy], ["report"])
        self.assertIsInstance(busy[0][1], shared.Exhausted)  # 记下的只是类型名，不是数据库原文
        self.assertEqual((self.calls, frappe.db.rollbacks), (shared.ATTEMPTS, shared.ATTEMPTS))
        self.assertEqual(frappe.db.pending + frappe.db.rows, [])

    def test_report_errors_roll_back_to_the_savepoint_and_are_not_retried(self):
        with mock.patch.object(modules["heartbeat"], "_receive",
                               self.failing(1, None, error=errors.ReportError("companion.report_invalid"))):
            with self.assertRaises(frappe.ValidationError) as caught:
                modules["heartbeat"].report()
        self.assertEqual(str(caught.exception), "companion.report_invalid")
        self.assertEqual((self.calls, frappe.db.rollbacks, frappe.db.point_rollbacks, self.sleeps), (1, 0, 1, []))
        self.assertEqual(frappe.db.pending, [])


class MachineMethodTests(_Case):
    """领单 / 回执、认电脑、按需请求：同一处重来，用尽各回原有的「忙」。"""

    def test_lease_retries_then_commits_once(self):
        with mock.patch.object(companion.transport, "lease", self.failing(3, {"jobs": []}), create=True):
            self.assertEqual(modules["queue_api"].lease(), {"jobs": []})
        self.assertEqual((self.calls, frappe.db.rollbacks), (4, 3))
        self.assertEqual(frappe.db.rows, [("attempt", 4)])  # `_run` 自己提交

    def test_ack_exhausted_answers_busy_and_a_failed_rollback_stops_at_once(self):
        with mock.patch.object(companion.transport, "acknowledge", self.failing(99, None), create=True):
            with self.assertRaises(_Busy):
                modules["queue_api"].acknowledge()
        self.assertEqual(([stage for stage, _ in busy], self.calls), (["ack"], shared.ATTEMPTS))
        self.setUp()
        frappe.db.fail_rollback = True
        with mock.patch.object(companion.transport, "lease", self.failing(99, None), create=True):
            with self.assertRaises(_Busy):
                modules["queue_api"].lease()
        # 回滚失败：恢复不明，不再领第二次；记下的异常不挂原来的（可能带回执密钥的）异常帧
        self.assertEqual(([stage for stage, _ in busy], self.calls, self.sleeps), (["lease_rollback"], 1, []))
        self.assertIsInstance(busy[0][1], shared.RollbackFailed)
        self.assertIsNone(busy[0][1].__context__)

    def test_device_hello_retries_and_credential_errors_are_answered_without_retry(self):
        handler = self.failing(1, {"status": "attached"})
        self.assertEqual(modules["device_api"]._guarded("device_hello", handler),
                         {"ok": True, "protocol": 1, "status": "attached"})
        self.assertEqual((self.calls, frappe.db.rollbacks), (2, 1))
        self.setUp()
        refused = self.failing(1, None, error=errors.CredentialError("companion.device_superseded"))
        self.assertEqual(modules["device_api"]._guarded("device_hello", refused),
                         {"ok": False, "code": "companion.device_superseded"})
        self.assertEqual((self.calls, frappe.db.rollbacks, frappe.db.point_rollbacks), (1, 0, 1))
        self.assertEqual(frappe.db.pending, [])
        self.setUp()
        with self.assertRaises(_Busy):
            modules["device_api"]._guarded("device_leave", self.failing(99, None))
        self.assertEqual([stage for stage, _ in busy], ["device_leave"])

    def test_on_demand_request_retries_then_answers_busy_once_exhausted(self):
        self.assertEqual(modules["request_api"]._guarded("request_next", self.failing(2, {"request": None})),
                         {"ok": True, "protocol": modules["request_api"].PROTOCOL, "request": None})
        self.assert_retried(2)
        self.setUp()
        with self.assertRaises(_Busy):
            modules["request_api"]._guarded("request_complete", self.failing(99, None))
        self.assertEqual(([stage for stage, _ in busy], self.calls), (["request_complete"], shared.ATTEMPTS))


class NativeMessageTests(_Case):
    """被回滚的那次留下的两路原生提示（``message_log`` 与 ``frappe.flags.error_message``，回复里的 ``_error_message``）
    都不带给重来的那次：机器方法那一次里可能带着配对码 / 回执密钥（10-20 lab `queue-http` 夹具：心跳认证重来后回复里
    带出了配对码）。调用之前已有的照留。"""

    def attempt(self, times):
        def run():
            self.calls += 1
            if self.calls <= times:
                frappe.flags["error_message"] = "exp_secret"
                frappe.local.message_log.append("exp_secret")
                raise frappe.QueryDeadlockError(LOCK_1020)
            return "ok"
        return run

    def test_a_retried_attempt_leaves_no_error_message(self):
        frappe.flags = {}
        self.assertEqual(shared.retried(self.attempt(2)), "ok")
        self.assertNotIn("error_message", frappe.flags)
        self.assertEqual(frappe.local.message_log, [])

    def test_an_earlier_error_message_is_kept(self):
        frappe.flags = {"error_message": "before"}
        frappe.local.message_log.append("before")
        with self.assertRaises(shared.Exhausted):
            shared.retried(self.attempt(99))
        self.assertEqual((frappe.flags, frappe.local.message_log), ({"error_message": "before"}, ["before"]))


class SamePlaceTests(unittest.TestCase):
    """电脑侧短事务都经 `transaction.conflicts.retried`，不再各写一个循环；人推经 `push.application.conflicts` 也到同一处。"""

    ENTRIES = {"api.py": "_local_transaction", "heartbeat.py": "report", "queue_api.py": "_run",
               "device_api.py": "_guarded", "request_api.py": "_guarded", "http_auth.py": "_authenticate_request"}

    @staticmethod
    def _functions(path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}

    def test_every_machine_side_entry_runs_in_the_shared_retry(self):
        for filename, function in self.ENTRIES.items():
            source = (COMPANION / filename).read_text(encoding="utf-8")
            self.assertIn("from pearnly_core.transaction import conflicts", source, filename)
            self.assertIn("conflicts.retried(", ast.unparse(self._functions(COMPANION / filename)[function]), filename)

    def test_no_second_retry_loop_is_left_in_companion(self):
        for path in sorted(COMPANION.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("for attempt in range(", text, path.name)
            for node in ast.walk(ast.parse(text)):
                # 只剩「原样抛给外层整笔重来」的那几处（保存点包装、心跳与认电脑的单次尝试），不在这里自己回滚重来
                if isinstance(node, ast.ExceptHandler) and node.type is not None \
                        and "QueryDeadlockError" in ast.unparse(node.type):
                    self.assertEqual([ast.unparse(item) for item in node.body][-1], "raise", path.name)

    def test_push_goes_through_the_same_mechanism(self):
        push = ast.unparse(self._functions(COWORK / "push/application/conflicts.py")["retried"])
        self.assertIn("shared.retried(", push)
        self.assertNotIn("sleep", push)


if __name__ == "__main__":
    unittest.main()
