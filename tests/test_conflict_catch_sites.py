"""B ①（11-26）：关隔离后 Frappe 保存前比对抛的 ``TimestampMismatchError`` 在各处「接异常」的地方按数据库冲突处理（``conflicts.is_conflict``），
不再被当成普通错误吞掉；入口层的三次循环换成 ``conflicts.attempt``（两种冲突都整笔重来，用尽回「请稍后重试」）。

每一处都按原文加载真模块（Frappe 与其余依赖用最小替身），让里面那一步抛出「Frappe 保存前比对」那一种冲突（抛出位置是替身
``frappe/model/document.py`` 的 ``check_if_latest``，同 `transaction.conflicts._stale_save` 的判法），断言：

- 保存点 / 降级类（第 2 类）：冲突**往外抛**给外层重来——改之前这里被吞成「排读取出错」「引用没记上」「反馈失败」「记忆暂不可用」
  「查重不可用」「这一行出错」；业务自己的错误照旧按原来的降级走（对照）。
- 后台「这次不记、下一轮再来」类（第 4 类）：冲突走原来死锁那条路（不记错误日志、不延后 10 分钟 / 稍后再来 / 回滚重写检查点）。
- 入口层（第 1 类）：冲突整笔重来、第二次成功；一直冲突回「请稍后重试」，不带 Frappe 原文；业务错误不重来。

    python3 tests/test_conflict_catch_sites.py
"""
from __future__ import annotations

import contextlib
import importlib.abc
import importlib.machinery
import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = ROOT / "apps"
CONFLICTS = APPS / "pearnly_core/pearnly_core/transaction/conflicts.py"

DOCUMENT = '''
import frappe


def check_if_latest():
    raise frappe.TimestampMismatchError("Document has been modified after you have opened it")
'''


class _Frappe(types.ModuleType):
    class ValidationError(Exception):
        pass

    class QueryDeadlockError(Exception):
        pass

    class TimestampMismatchError(ValidationError):
        pass

    class PermissionError(Exception):
        pass

    class DuplicateEntryError(Exception):
        pass

    class UniqueValidationError(ValidationError):
        pass

    class DoesNotExistError(ValidationError):
        pass

    def __init__(self):
        super().__init__("frappe")
        self.__path__ = []
        self.calls = []
        self.local = types.SimpleNamespace(message_log=[], site="test")
        self.flags = {}
        self.session = types.SimpleNamespace(user="u@test")
        self.db = types.SimpleNamespace(rollback=self._rollback, savepoint=lambda name: self.calls.append(("savepoint", name)),
                                        commit=lambda: self.calls.append(("commit",)), get_value=mock.MagicMock(),
                                        sql=mock.MagicMock(), exists=mock.MagicMock(return_value=False))

    def _rollback(self, save_point=None):
        self.calls.append(("rollback", save_point))

    def _(self, text, *args, **kwargs):
        return text

    def throw(self, message, exc=None, *args, **kwargs):
        raise (exc or self.ValidationError)(message)

    def log_error(self, *args, **kwargs):
        self.calls.append(("log_error", kwargs.get("title") or (args[1] if len(args) > 1 else "")))

    def clear_messages(self):
        self.calls.append(("clear_messages",))

    def get_traceback(self, *args, **kwargs):
        return ""

    def logger(self, *args, **kwargs):
        return mock.MagicMock()

    def whitelist(self, *args, **kwargs):
        return lambda function: function

    def validate_and_sanitize_search_inputs(self, function):
        return function

    @property
    def rollbacks(self):
        return [call for call in self.calls if call[0] == "rollback"]

    @property
    def errors(self):
        return [call for call in self.calls if call[0] == "log_error"]


class _Auto(types.ModuleType):
    """导入替身：没给真模块的 ``frappe.*`` / ``pearnly_*`` 子模块一律是它，属性取到 MagicMock（同名属性每次是同一个）。"""

    def __init__(self, name):
        super().__init__(name)
        self.__path__ = []

    def __getattr__(self, item):
        if item.startswith("__"):
            raise AttributeError(item)
        if f"{self.__name__}.{item}" in sys.modules:  # 用例先放好的子模块（`from . import model`）
            return sys.modules[f"{self.__name__}.{item}"]
        # 首字母大写、不全大写的（类名：Invalid、ConnectionError、ProviderError…）给一个异常类，能被 except 接；其余给 MagicMock
        value = (type(item, (Exception,), {}) if item[0].isupper() and not item.isupper()
                 else mock.MagicMock(name=f"{self.__name__}.{item}"))
        setattr(self, item, value)
        return value


class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("frappe", "pearnly_core", "pearnly_cowork", "pearnly_erp_portal", "pearnly_control", "pearnly_dms"):
            return importlib.machinery.ModuleSpec(name, self, is_package=True)
        return None

    def create_module(self, spec):
        return _Auto(spec.name)

    def exec_module(self, module):
        pass


class Errors(types.ModuleType):
    class Deferred(Exception):
        def __init__(self, reason="", seconds=0):
            super().__init__(reason)
            self.reason, self.seconds = reason, seconds

    class QueueFull(Exception):
        pass

    def __init__(self):
        super().__init__("pearnly_core.execution.errors")


class SiteTest(unittest.TestCase):
    """每个用例一个干净的替身环境：真 `conflicts`、替身 Frappe、按原文加载被测模块。"""

    def setUp(self):
        self.frappe = _Frappe()
        self.errors = Errors()
        self.finder = _Finder()
        sys.meta_path.insert(0, self.finder)
        self.addCleanup(sys.meta_path.remove, self.finder)
        saved = {name: module for name, module in sys.modules.items()
                 if name.split(".")[0] in ("frappe", "pearnly_core", "pearnly_cowork", "pearnly_erp_portal", "pearnly_control")}
        for name in saved:
            del sys.modules[name]
        self.addCleanup(lambda: [sys.modules.pop(name) for name in list(sys.modules)
                                 if name.split(".")[0] in ("frappe", "pearnly_core", "pearnly_cowork", "pearnly_erp_portal", "pearnly_control")])
        self.addCleanup(sys.modules.update, saved)
        sys.modules["frappe"] = self.frappe
        sys.modules["pearnly_core.execution.errors"] = self.errors
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = pathlib.Path(folder.name) / "frappe" / "model" / "document.py"
        path.parent.mkdir(parents=True)
        path.write_text(DOCUMENT, encoding="utf-8")
        self.document = self._load("frappe_model_document_fake", path)
        self.conflicts = self._load("pearnly_core.transaction.conflicts", CONFLICTS)
        transaction = _Auto("pearnly_core.transaction")
        transaction.conflicts = self.conflicts
        sys.modules["pearnly_core.transaction"] = transaction
        sleep = mock.patch.object(self.conflicts.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def _load(self, name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def site(self, rel):
        name = rel.removesuffix(".py").replace("/", ".").split(".", 1)[1]  # apps/<app>/<app>/x.py → <app>.x
        return self._load(name, APPS / rel)

    def stale(self, *args, **kwargs):
        """抛「Frappe 保存前比对」那一种冲突（关隔离后的 1020）。"""
        self.document.check_if_latest()

    def business_mismatch(self, *args, **kwargs):
        raise self.frappe.TimestampMismatchError("The record changed. Reload it before saving.")


# ── 第 2 类：保存点 / 降级处，冲突往外抛 ─────────────────────────────────────────


class SavepointSitesTest(SiteTest):
    def test_cc_heartbeat_ledger_change_raises_the_conflict_instead_of_logging_it(self):
        machine = self.site("pearnly_core/pearnly_core/cc_desktop/machine.py")
        machine.ledger_contracts.express_changes.return_value = [{"book": "B"}]
        machine.products.ledger_changed.side_effect = self.stale
        with self.assertRaises(self.frappe.TimestampMismatchError):
            machine._changes(types.SimpleNamespace(device="D"), {machine.CHANGES: [{"x": 1}]})
        self.assertEqual(self.frappe.errors, [], "冲突不记成「CC ledger change not queued」")
        machine.products.ledger_changed.side_effect = RuntimeError("queue down")
        machine._changes(types.SimpleNamespace(device="D"), {machine.CHANGES: [{"x": 1}]})  # 对照：普通错误照旧不挡心跳
        self.assertEqual(self.frappe.errors, [("log_error", "CC ledger change not queued")])

    def test_wht_certificate_reference_raises_the_conflict_instead_of_returning_zero(self):
        certificates = self.site("pearnly_cowork/pearnly_cowork/history/certificates.py")
        certificates.policy_rules.skipped_certificates.return_value = [(1, {"fields": {}, "page_index": 1})]
        self.frappe.get_doc = mock.MagicMock(return_value=mock.MagicMock(insert=mock.MagicMock(side_effect=self.stale)))
        batch = types.SimpleNamespace(name="B1", workspace="W", get=lambda key: None)
        with self.assertRaises(self.frappe.TimestampMismatchError):
            certificates.keep_skipped(batch, [], {})
        self.frappe.get_doc.return_value.insert.side_effect = RuntimeError("disk")
        self.assertEqual(certificates.keep_skipped(batch, [], {}), 0)  # 对照：普通错误照旧只记一条、回 0

    def test_ocr_feedback_raises_the_conflict_instead_of_reporting_failed(self):
        feedback = self.site("pearnly_cowork/pearnly_cowork/ocr/feedback/service.py")
        doc = mock.MagicMock(pages="new", workspace="W", client=None, record_version=1)
        doc.name = "H1"
        doc.get_doc_before_save.return_value = types.SimpleNamespace(pages="old")
        feedback.access.require_scope.side_effect = self.stale
        with self.assertRaises(self.frappe.TimestampMismatchError):
            feedback.capture_saved_edit(doc)
        feedback.access.require_scope.side_effect = self.business_mismatch
        self.assertEqual(feedback.capture_saved_edit(doc)["status"], "failed")  # 对照：业务自己的「请刷新」照旧降级

    def test_category_and_name_memory_raise_the_conflict_instead_of_falling_back(self):
        for rel in ("pearnly_cowork/pearnly_cowork/history/categories.py", "pearnly_cowork/pearnly_cowork/history/names.py"):
            module = self.site(rel)
            with self.assertRaises(self.frappe.TimestampMismatchError, msg=rel):
                module._optional(self.stale, "fallback", "learn")
            self.assertEqual(module._optional(self.business_mismatch, "fallback", "learn"), "fallback", rel)

    def test_duplicate_check_raises_the_conflict_instead_of_unavailable(self):
        duplicates = self.site("pearnly_cowork/pearnly_cowork/history/duplicates.py")
        with mock.patch.object(duplicates, "enabled", return_value=True), mock.patch.object(duplicates, "_find", side_effect=self.stale):
            with self.assertRaises(self.frappe.TimestampMismatchError):
                duplicates.check("W", None, {})
        with mock.patch.object(duplicates, "enabled", return_value=True), \
                mock.patch.object(duplicates, "_find", side_effect=RuntimeError("index")):
            self.assertEqual(duplicates.check("W", None, {})["duplicate_check_status"], "unavailable")

    def test_workspace_import_row_raises_the_conflict_instead_of_a_row_error(self):
        imports = self.site("pearnly_cowork/pearnly_cowork/masters/workspace_import.py")
        imports.workspace_fields.import_values.return_value = {"tax_id": "1"}
        imports.workspaces.tax_taken.return_value = False
        imports.workspaces.DuplicateWorkspaceTax = type("DuplicateWorkspaceTax", (self.frappe.ValidationError,), {})
        imports.workspaces.create.side_effect = self.stale
        row = {"row_index": 1, "name": "A", "tax_id": "1"}
        with self.assertRaises(self.frappe.TimestampMismatchError):
            imports._judge(row, dry_run=False)
        imports.workspaces.create.side_effect = self.frappe.ValidationError("bad")
        self.assertEqual(imports._judge(row, dry_run=False)["status"], "error")  # 对照


# ── 第 4 类：后台下一轮再来 ──────────────────────────────────────────────────────


class BackgroundSitesTest(SiteTest):
    def test_folder_intake_step_skips_a_conflict_without_logging_or_delaying(self):
        pump = self.site("pearnly_cowork/pearnly_cowork/folder_intake/pump.py")
        with mock.patch.object(pump, "read", return_value=types.SimpleNamespace(name="F1")), \
                mock.patch.object(pump, "_later") as later:
            pump._each(["F1"], float("inf"), self.stale)
            self.assertEqual((self.frappe.errors, later.call_count), ([], 0), "冲突：这次不记、下一轮再来")
            pump._each(["F1"], float("inf"), mock.MagicMock(side_effect=RuntimeError("x")))
            self.assertEqual((len(self.frappe.errors), later.call_count), (1, 1))  # 对照：普通错误照旧记一条、延后

    def test_folder_intake_push_skips_a_conflict_without_logging_or_delaying(self):
        pump = self.site("pearnly_cowork/pearnly_cowork/folder_intake/pump.py")
        entry = types.SimpleNamespace(name="F1", last_code="")
        with mock.patch.object(pump, "_due", return_value=["F1"]), mock.patch.object(pump, "read", return_value=entry), \
                mock.patch.object(pump, "_pending_pushes", return_value=0), mock.patch.object(pump, "_push_target"), \
                mock.patch.object(pump, "_waits_for_purchases", return_value=False), \
                mock.patch.object(pump, "_push", side_effect=self.stale), mock.patch.object(pump, "_later") as later:
            pump._push_step({"push_pending": 5}, float("inf"))
            self.assertEqual((self.frappe.errors, later.call_count), ([], 0))

    def test_ocr_callback_checkpoint_retries_a_conflict(self):
        callback = self.site("pearnly_cowork/pearnly_cowork/ocr/remote/callback.py")
        calls = []

        def checkpoint(*args):
            calls.append(1)
            if len(calls) == 1:
                self.stale()
        callback.core_work.checkpoint.side_effect = checkpoint
        self.assertTrue(callback._record("W1", "k", "reply", {}, attempts=3))
        self.assertEqual(len(calls), 2, "冲突回滚后重写检查点，不当成 500 抛给云上")
        callback.core_work.checkpoint.side_effect = RuntimeError("bad")
        with self.assertRaises(RuntimeError):
            callback._record("W1", "k", "reply", {}, attempts=3)  # 对照：别的错误照旧抛出

    def test_ocr_result_persist_defers_a_conflict(self):
        workflow = self.site("pearnly_cowork/pearnly_cowork/ocr/execution/workflow.py")
        doc = types.SimpleNamespace(name="B1", phase="recognized", result_json="{}", settings_json="null", examples_json="null",
                                    actor="u", billing_state="")
        workflow.store.read.return_value = doc
        with mock.patch.object(workflow, "_require_work"), mock.patch.object(workflow, "_check"), \
                mock.patch.object(workflow, "_persist", side_effect=self.stale):
            with self.assertRaises(self.errors.Deferred) as caught:
                workflow.run("B1")
        self.assertEqual(caught.exception.reason, "database_busy", "冲突稍后再来，不当成识别流程出错")


# ── 第 1 类：入口层 ─────────────────────────────────────────────────────────────


ENTRIES = (  # (模块, 入口函数, 调用参数, 被包的那一步)
    ("pearnly_cowork/pearnly_cowork/reconciliation/bank_recon_api.py", "_transaction", None, None),
    ("pearnly_cowork/pearnly_cowork/reconciliation/income_recon_api.py", "_transaction", None, None),
    ("pearnly_cowork/pearnly_cowork/stocktake/api.py", "_transaction", None, None),
    ("pearnly_cowork/pearnly_cowork/stocktake/desk_api.py", "_transaction", None, None),
    ("pearnly_cowork/pearnly_cowork/history/category_api.py", "_call", None, None),
    ("pearnly_cowork/pearnly_cowork/connections/api.py", "_call", None, None),
    ("pearnly_cowork/pearnly_cowork/folder_intake/api.py", "_retried", None, None),
    ("pearnly_cowork/pearnly_cowork/work/api.py", "_run", None, None),
    ("pearnly_erp_portal/pearnly_erp_portal/work/api.py", "_run", None, None),
)


class EntrySitesTest(SiteTest):
    def _flaky(self, failures, result="ok"):
        calls = []

        def write(*args, **kwargs):
            calls.append(1)
            if len(calls) <= failures:
                self.stale()
            return result
        return write, calls

    def setUp(self):
        super().setUp()
        stocktake = _Auto("pearnly_cowork.stocktake.access")  # desk_api 载入时拆 access.TYPES
        stocktake.TYPES = ("T", "I", "E", "O")
        sys.modules["pearnly_cowork.stocktake.access"] = stocktake
        sys.modules["pearnly_cowork.connections.model"] = _Auto("pearnly_cowork.connections.model")

    def test_wrapping_entries_retry_a_conflict_and_answer_busy_when_used_up(self):
        for rel, name, _args, _step in ENTRIES:
            with self.subTest(rel):
                entry = getattr(self.site(rel), name)
                write, calls = self._flaky(1)
                self.assertEqual(entry(write), "ok")
                self.assertEqual(len(calls), 2)
                write, calls = self._flaky(99)
                with self.assertRaises(self.frappe.ValidationError) as caught:
                    entry(write)
                self.assertEqual(str(caught.exception), "Please try again later")
                self.assertEqual(len(calls), self.conflicts.ATTEMPTS)
                once = []

                def business(*args):
                    once.append(1)
                    raise self.frappe.TimestampMismatchError("The record changed. Reload it before saving.")
                with self.assertRaises(self.frappe.TimestampMismatchError):
                    entry(business)
                self.assertEqual(len(once), 1, "业务自己的「请刷新」不重来")

    def test_named_entries_retry_a_conflict(self):
        cases = (
            ("pearnly_core/pearnly_core/line_binding.py", "generate_bind_code", ("u",), "_generate_bind_code"),
            ("pearnly_cowork/pearnly_cowork/history/api.py", "create", ("W", [], "k"), "storage.create_manual"),
            ("pearnly_cowork/pearnly_cowork/history/api.py", "save", ("H", [], 1), "storage.update_pages"),
            ("pearnly_cowork/pearnly_cowork/masters/workspace_api.py", "commit_workspace_import", ([], "k"), "workspace_import.commit"),
            ("pearnly_cowork/pearnly_cowork/ocr/api.py", "submit", ("F", "k"), "service.submit"),
        )
        for rel, name, args, step in cases:
            with self.subTest(rel + ":" + step):
                module = self.site(rel)
                write, calls = self._flaky(1, result={})
                owner, attr = (module, step) if "." not in step else (getattr(module, step.split(".")[0]), step.split(".")[1])
                with mock.patch.object(owner, attr, side_effect=write):
                    getattr(module, name)(*args)
                self.assertEqual(len(calls), 2)

    def test_board_attachment_rereads_the_upload_on_a_retry(self):
        import io
        for rel in ("pearnly_cowork/pearnly_cowork/work/api.py", "pearnly_erp_portal/pearnly_erp_portal/work/api.py"):
            with self.subTest(rel):
                api = self.site(rel)
                upload = types.SimpleNamespace(filename="a.png", stream=io.BytesIO(b"PNGDATA"))
                self.frappe.request = types.SimpleNamespace(files={"file": upload})
                seen = []

                def attach(name, given):
                    seen.append(given.stream.read())
                    if len(seen) == 1:
                        self.stale()
                    return "attached"
                with mock.patch.object(api.service, "attach", side_effect=attach):
                    self.assertEqual(api.attach("CARD-1"), "attached")
                self.assertEqual(seen, [b"PNGDATA", b"PNGDATA"], "重来那次读到的是同一份内容，不是空的")

    def test_stock_opening_retries_only_when_the_request_owns_its_transaction(self):
        inventory = self.site("pearnly_erp_portal/pearnly_erp_portal/inventory/api.py")
        write, calls = self._flaky(1)
        self.frappe.db.transaction_writes = 0
        self.assertEqual(inventory._opening_transaction(write), "ok")
        self.assertEqual(len(calls), 2)
        self.frappe.db.transaction_writes = 3  # 调用方已有没提交的写：不重来，原样抛出
        write, calls = self._flaky(1)
        with self.assertRaises(self.frappe.TimestampMismatchError):
            inventory._opening_transaction(write)
        self.assertEqual(len(calls), 1)

    def test_file_upload_retries_a_conflict_or_a_concurrent_insert(self):
        files = self.site("pearnly_cowork/pearnly_cowork/files/api.py")
        source = (APPS / "pearnly_cowork/pearnly_cowork/files/api.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("conflicts.is_conflict(error) or isinstance(error, frappe.DuplicateEntryError)"), 2)
        write, calls = self._flaky(1)
        with mock.patch.object(files.service, "finish_direct", side_effect=write), mock.patch.object(files, "scope"):
            self.assertEqual(files.finish_upload("f", 1, "k", "W"), "ok")
        self.assertEqual(len(calls), 2)


class IsConflictTest(SiteTest):
    def test_is_conflict(self):
        try:
            self.stale()
        except Exception as error:  # noqa: BLE001
            stale = error
        self.assertTrue(self.conflicts.is_conflict(stale))
        self.assertTrue(self.conflicts.is_conflict(self.frappe.QueryDeadlockError("1213")))
        self.assertFalse(self.conflicts.is_conflict(self.frappe.TimestampMismatchError("business")))
        self.assertFalse(self.conflicts.is_conflict(RuntimeError("x")))

    def test_attempt_rolls_back_and_answers_busy_without_frappe_text(self):
        with self.assertRaises(self.frappe.ValidationError) as caught:
            self.conflicts.attempt(self.stale)
        self.assertEqual(str(caught.exception), "Please try again later")
        self.assertIsNone(caught.exception.__cause__)


if __name__ == "__main__":
    unittest.main()
