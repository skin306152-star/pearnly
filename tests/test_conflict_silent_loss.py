"""B ③（11-28）离线回归：扣费撞冲突的重试、用尽留记录，销售单 request_id 唯一索引的补丁，ERP 提交查重加锁读。

真并发（两个连接、关快照隔离）的修前红 / 修后绿在本机实验栈跑：``scripts/conflict-silent-loss-lab-verify.py``
（记录 `docs/work/2026-11-28-conflict-silent-loss.md`）。这里锁住不连库就能判的那几条：

- 扣费：入口那次「先结束读快照」的空提交在重试包装外面；撞一次冲突 → 整笔回滚重来 → 只扣一次；一直冲突 → 抛出，
  留一条带 reference 的「没记上」记录；保存前比对那一种冲突同样重试；已提交过写的不重试。
- 补丁：空串改 NULL，重号的最早那张不动、后来的改成「原号#单号」，不删单；第二次执行什么都不改。
- 采购 / 销售 ``post`` 拿到锁后的查重是加锁读，且不再经普通 ``get_doc``。

    python3 tests/test_conflict_silent_loss.py
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = ROOT / "apps"
CONFLICTS = APPS / "pearnly_core/pearnly_core/transaction/conflicts.py"


class _Callbacks:
    def __init__(self):
        self.items = []

    def add(self, function):
        self.items.append(function)

    def run(self):
        while self.items:
            self.items.pop(0)()

    def reset(self):
        self.items.clear()


class _Frappe(types.ModuleType):
    class ValidationError(Exception):
        pass

    class QueryDeadlockError(Exception):
        pass

    class TimestampMismatchError(ValidationError):
        pass

    class PermissionError(Exception):
        pass

    def __init__(self):
        super().__init__("frappe")
        self.__path__ = []
        self.local = types.SimpleNamespace(message_log=[])
        self.flags = {}
        self.conf = {}
        self.logs = []
        self.calls = []
        self.db = types.SimpleNamespace(commit=self._commit, rollback=self._rollback, after_commit=_Callbacks(),
                                        before_commit=_Callbacks(), sql=mock.MagicMock(), sql_list=mock.MagicMock())

    def _commit(self):
        self.calls.append("commit")
        self.db.before_commit.run()
        self.db.after_commit.run()

    def _rollback(self, save_point=None):
        self.calls.append("rollback")
        self.db.before_commit.reset()
        self.db.after_commit.reset()

    def _(self, text, *args, **kwargs):
        return text

    def throw(self, message, exc=None, *args, **kwargs):
        raise (exc or self.ValidationError)(message)

    def whitelist(self, *args, **kwargs):
        return lambda function: function

    def log_error(self, title=None, message=None, **kwargs):
        self.logs.append((title, message))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _Env(unittest.TestCase):
    def setUp(self):
        self.saved = {name: sys.modules.get(name) for name in (
            "frappe", "frappe.utils", "pearnly_core", "pearnly_core.transaction", "pearnly_core.transaction.conflicts",
            "pearnly_control", "pearnly_control.billing", "pearnly_control.payments",
            "pearnly_control.payments.transactions", "pearnly_control.billing_api")}
        self.frappe = _Frappe()
        sys.modules["frappe"] = self.frappe
        sys.modules["frappe.utils"] = types.SimpleNamespace(now=lambda: "2026-11-28 00:00:00")
        for package in ("pearnly_core", "pearnly_core.transaction", "pearnly_control", "pearnly_control.payments"):
            module = types.ModuleType(package)
            module.__path__ = []
            sys.modules[package] = module
        self.conflicts = _load("pearnly_core.transaction.conflicts", CONFLICTS)
        sys.modules["pearnly_core.transaction"].conflicts = self.conflicts
        self.conflicts.time = types.SimpleNamespace(sleep=lambda seconds: None)

    def tearDown(self):
        for name, module in self.saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class BillingCharge(_Env):
    def setUp(self):
        super().setUp()
        self.billing = types.ModuleType("pearnly_control.billing")
        sys.modules["pearnly_control.billing"] = self.billing
        sys.modules["pearnly_control"].billing = self.billing
        self.transactions = _load("pearnly_control.payments.transactions",
                                  APPS / "pearnly_control/pearnly_control/payments/transactions.py")
        self.transactions.time = types.SimpleNamespace(sleep=lambda seconds: None)
        sys.modules["pearnly_control.payments"].transactions = self.transactions
        self.api = _load("pearnly_control.billing_api", APPS / "pearnly_control/pearnly_control/billing_api.py")
        self.api._require_service = lambda *args, **kwargs: None
        self.api._tenant_or_throw = lambda tenant: tenant
        self.ledger = []  # 已提交的台账行（reference）

    def charge_with(self, failures, *, error=None):
        """``billing.charge`` 的替身：前 ``failures`` 次在「钱包已改、台账未写」时撞冲突；成功那次写一行台账并提交（同真函数）。"""
        state = {"left": failures, "calls": 0, "pending": []}
        frappe = self.frappe

        def charge(tenant, *, product, kind, units, tier, reference, description):
            state["calls"] += 1
            state["pending"].append(reference)
            if state["left"] > 0:
                state["left"] -= 1
                raise (error or frappe.QueryDeadlockError)("Deadlock found when trying to get lock")
            frappe.db.commit()
            self.ledger.append(reference)
            state["pending"].clear()
            return {"ok": True, "charged_thb": 1.5}

        original = frappe.db.rollback

        def rollback(save_point=None):
            state["pending"].clear()  # 整笔回滚：没提交的钱包改动、台账都没了
            original(save_point)

        frappe.db.rollback = rollback
        self.billing.charge = charge
        return state

    def test_one_conflict_is_retried_and_charged_once(self):
        state = self.charge_with(1)
        answer = self.api.service_charge("T1", "erp", "pdf", 1, "B", "ref-1", "x")
        self.assertEqual(answer["ok"], True)
        self.assertEqual(state["calls"], 2)
        self.assertEqual(self.ledger, ["ref-1"])
        self.assertIn("rollback", self.frappe.calls)

    def test_the_save_time_check_conflict_is_retried_too(self):
        document = types.ModuleType("frappe.model.document")
        exec(compile("import frappe\ndef check_if_latest():\n    raise frappe.TimestampMismatchError('modified')\n",
                     str(pathlib.Path("frappe", "model", "document.py")), "exec"), document.__dict__)
        state = {"calls": 0}

        def charge(tenant, **kwargs):
            state["calls"] += 1
            if state["calls"] == 1:
                document.check_if_latest()
            return {"ok": True}

        self.billing.charge = charge
        self.assertEqual(self.api.service_charge("T1", "erp", "pdf", 1, "B", "ref-2", "x"), {"ok": True})
        self.assertEqual(state["calls"], 2)

    def test_used_up_raises_and_leaves_a_record_with_the_reference(self):
        state = self.charge_with(99)
        with self.assertRaises(self.frappe.QueryDeadlockError):
            self.api.service_charge("T1", "erp", "pdf", 3, "B", "ref-3", "x")
        self.assertEqual(state["calls"], 12)
        self.assertEqual(self.ledger, [])
        self.assertEqual([title for title, _message in self.frappe.logs], [self.api.CHARGE_NOT_RECORDED])
        self.assertIn("reference=ref-3", self.frappe.logs[0][1])
        self.assertIn("units=3", self.frappe.logs[0][1])

    def test_a_business_error_is_not_retried_or_logged_as_not_recorded(self):
        def charge(tenant, **kwargs):
            raise self.frappe.ValidationError("The billing reference was already used for another request")

        self.billing.charge = charge
        with self.assertRaises(self.frappe.ValidationError):
            self.api.service_charge("T1", "erp", "pdf", 1, "B", "ref-4", "x")
        self.assertEqual(self.frappe.logs, [])

    def test_a_conflict_after_a_committed_write_is_not_replayed(self):
        calls = {"n": 0}

        def work():
            calls["n"] += 1
            self.frappe.db.commit()  # 写过、提交了
            raise self.frappe.QueryDeadlockError("after commit")

        with self.assertRaises(self.frappe.QueryDeadlockError):
            self.transactions.retry_service(work)()
        self.assertEqual(calls["n"], 1)

    def test_the_snapshot_commit_sits_outside_the_retry(self):
        """修前的写法：空提交在重试包装里面，一提交就被标成「已提交」，之后的冲突不再重试（漏收费）。"""
        source = (APPS / "pearnly_control/pearnly_control/billing_api.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "service_charge")
        self.assertNotIn("retry_service", [ast.unparse(item) for item in function.decorator_list])
        self.assertIn("retry_service(billing.charge)", ast.unparse(function))


class SalesRequestIdPatch(_Env):
    def setUp(self):
        super().setUp()
        self.rows = []  # [name, request_id, creation]
        frappe = self.frappe
        frappe.db.table_exists = lambda doctype: True
        frappe.db.has_column = lambda doctype, column: True

        def sql(query, values=None, **kwargs):
            text = " ".join(query.split())
            if text.startswith("select count(*)"):
                return [[sum(1 for row in self.rows if row[1] == "")]]
            if "group by request_id having count(*) > 1" in text:
                seen = {}
                for row in self.rows:
                    if row[1]:
                        seen[row[1]] = seen.get(row[1], 0) + 1
                return [(key,) for key, count in seen.items() if count > 1]
            if text.startswith("update") and "set request_id = null" in text:
                for row in self.rows:
                    if row[1] == "":
                        row[1] = None
                return []
            if text.startswith("update"):
                request_id, name = values
                next(row for row in self.rows if row[0] == name)[1] = request_id
                return []
            if text.startswith("delete"):
                raise AssertionError("the patch must not delete documents")
            raise AssertionError(text)

        def sql_list(query, values=None):
            return [row[0] for row in sorted(self.rows, key=lambda row: (row[2], row[0])) if row[1] == values[0]]

        frappe.db.sql = sql
        frappe.db.sql_list = sql_list
        self.patch = _load("v1_34", APPS / "pearnly_erp_portal/pearnly_erp_portal/patches/v1_34_sales_request_id_unique.py")

    def test_duplicates_keep_the_first_and_rename_the_rest_without_deleting(self):
        self.rows = [["SD-1", "line-intake:A", 1], ["SD-2", "line-intake:A", 2], ["SD-3", "", 3], ["SD-4", "", 4],
                     ["SD-5", "x" * 140, 5], ["SD-6", "x" * 140, 6], ["SD-7", None, 7], ["SD-8", "ok", 8]]
        self.patch.execute()
        values = {row[0]: row[1] for row in self.rows}
        self.assertEqual(values["SD-1"], "line-intake:A")
        self.assertEqual(values["SD-2"], "line-intake:A#SD-2")
        self.assertEqual((values["SD-3"], values["SD-4"], values["SD-7"]), (None, None, None))
        self.assertEqual(values["SD-5"], "x" * 140)
        self.assertTrue(values["SD-6"].endswith("#SD-6") and len(values["SD-6"]) == 140)
        self.assertEqual(len(self.rows), 8)
        named = [row[1] for row in self.rows if row[1]]
        self.assertEqual(len(named), len(set(named)))
        self.assertEqual(len(self.frappe.logs), 1)
        self.patch.execute()  # 第二次什么都不改、不再记日志
        self.assertEqual({row[0]: row[1] for row in self.rows}, values)
        self.assertEqual(len(self.frappe.logs), 1)

    def test_clean_data_changes_nothing(self):
        self.rows = [["SD-1", "a", 1], ["SD-2", None, 2]]
        self.patch.execute()
        self.assertEqual(self.rows, [["SD-1", "a", 1], ["SD-2", None, 2]])
        self.assertEqual(self.frappe.logs, [])

    def test_the_patch_runs_before_the_model_sync_and_the_field_is_unique(self):
        import json

        text = (APPS / "pearnly_erp_portal/pearnly_erp_portal/patches.txt").read_text(encoding="utf-8")
        pre = text.split("[post_model_sync]")[0]
        self.assertIn("pearnly_erp_portal.patches.v1_34_sales_request_id_unique", pre)
        meta = json.loads((APPS / "pearnly_erp_portal/pearnly_erp_portal/pearnly_erp_portal/doctype/erp_sales_document/"
                                  "erp_sales_document.json").read_text(encoding="utf-8"))
        field = next(row for row in meta["fields"] if row["fieldname"] == "request_id")
        self.assertEqual(field.get("unique"), 1)


class PostDuplicateCheck(unittest.TestCase):
    def test_the_check_under_the_lock_is_a_locked_read_and_not_a_plain_get_doc(self):
        for path in ("sales/entry.py", "purchasing/entry.py"):
            tree = ast.parse((APPS / "pearnly_erp_portal/pearnly_erp_portal" / path).read_text(encoding="utf-8"))
            post = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "post")
            with_block = next(node for node in ast.walk(post) if isinstance(node, ast.With))
            calls = [node for node in ast.walk(with_block) if isinstance(node, ast.Call)]
            lookup = next(call for call in calls if ast.unparse(call.func) == "frappe.db.get_value"
                          and "request_id" in ast.unparse(call.args[1]))
            self.assertIn("for_update", {keyword.arg for keyword in lookup.keywords}, path)
            self.assertFalse([call for call in calls if ast.unparse(call.func) == "access.get_document"], path)


class LockedReadsStay(unittest.TestCase):
    """B ③ 改成加锁读的几处：去掉 ``for_update`` 就失败（两连接实证在实验栈，见文件头）。"""

    CASES = (
        ("pearnly_erp_portal/pearnly_erp_portal/express/push.py", "void", "frappe.db.get_value"),
        ("pearnly_cowork/pearnly_cowork/history/push_state.py", "withdrawn", "frappe.db.get_value"),
        ("pearnly_cowork/pearnly_cowork/history/push_state.py", "retarget", "frappe.db.get_value"),
        ("pearnly_core/pearnly_core/express_operator/listed.py", "remember", "frappe.db.get_value"),
        ("pearnly_cowork/pearnly_cowork/companies/automation.py", "_row", "frappe.db.get_value"),
    )

    def test_the_reads_used_for_the_write_are_locked(self):
        for path, function, call_name in self.CASES:
            tree = ast.parse((APPS / path).read_text(encoding="utf-8"))
            fn = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == function)
            calls = [node for node in ast.walk(fn) if isinstance(node, ast.Call) and ast.unparse(node.func) == call_name]
            self.assertTrue(calls, (path, function))
            for call in calls:
                self.assertIn("for_update", {keyword.arg for keyword in call.keywords}, (path, function, ast.unparse(call)))

    def test_welcome_decides_from_the_locked_select(self):
        source = (APPS / "pearnly_control/pearnly_control/onboarding/welcome.py").read_text(encoding="utf-8")
        self.assertIn("SELECT welcome_email_queue FROM `tabPearnly Issue` WHERE name=%s FOR UPDATE", source)


class VoidEntries(unittest.TestCase):
    """作废这一类（11-28 B ③ 第二段）：入口先结束读视图再整笔重来、先锁后读；撤件按来源单据；领件兜底加锁读；账本栏保存不盖回。"""

    def function(self, path, name):
        tree = ast.parse((APPS / "pearnly_erp_portal/pearnly_erp_portal" / path).read_text(encoding="utf-8"))
        return next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)

    def test_void_entries_commit_then_attempt(self):
        for path, name in (("purchasing/api.py", "api_cancel_purchase"), ("purchasing/api.py", "api_void_purchase"),
                           ("sales/api.py", "api_cancel_sale"), ("inventory/api.py", "api_void_stock_entry")):
            source = ast.unparse(self.function(path, name))
            self.assertIn("frappe.db.commit()", source, name)
            self.assertIn("conflicts.attempt(", source, name)
            self.assertLess(source.index("frappe.db.commit()"), source.index("conflicts.attempt("), name)

    def test_lock_comes_before_any_plain_read(self):
        for path, name in (("purchasing/access.py", "get_document"), ("sales/access.py", "get_document"),
                           ("purchasing/service.py", "set_status")):
            source = ast.unparse(self.function(path, name))
            self.assertLess(source.index("for update"), source.index("guard.require"), name)

    def test_void_cancels_every_unwritten_job_of_the_source(self):
        source = " ".join(ast.unparse(self.function("express/push.py", "_cancel_unwritten")).lower().split())
        self.assertIn("for update", source)
        self.assertIn("source_doctype = %s and source_name = %s", source)
        self.assertIn("_cancel_unwritten(doctype, name)", ast.unparse(self.function("express/push.py", "void")))

    def test_claim_time_check_and_send_recheck_see_a_fresh_void(self):
        self.assertIn("for_update=True", ast.unparse(self.function("express/push.py", "still_wanted")))
        source = (APPS / "pearnly_erp_portal/pearnly_erp_portal/express/push.py").read_text(encoding="utf-8")
        line = next(row for row in source.splitlines() if row.startswith("LEDGER_SEEN = "))
        self.assertIn('"status"', line)

    def test_ledger_columns_are_kept_on_every_save(self):
        for doctype in ("purchase_document", "erp_sales_document", "erp_stock_entry", "erp_stock_count", "erp_pos_day_summary"):
            path = APPS / f"pearnly_erp_portal/pearnly_erp_portal/pearnly_erp_portal/doctype/{doctype}/{doctype}.py"
            tree = ast.parse(path.read_text(encoding="utf-8"))
            validate = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "validate")
            self.assertEqual(ast.unparse(validate.body[0]), "ledger_guard.keep_current(self)", doctype)
        guard = (APPS / "pearnly_erp_portal/pearnly_erp_portal/express/ledger_guard.py").read_text(encoding="utf-8")
        self.assertIn('SUBMITTED_ONLY = ("printed_total",)', guard)
        self.assertIn("for_update=True", guard)


if __name__ == "__main__":
    unittest.main()
