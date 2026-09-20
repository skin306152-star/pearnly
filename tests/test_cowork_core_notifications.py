"""Source-bound shared notification regression; no Site, DB, Redis or LINE IO.

Compile only the actual checked-out Core _notify/_finish AST, unchanged. All
runtime dependencies are explicit fakes. DMS's registered callback path and
signature are read from its source without importing or invoking the DMS app.
"""
import ast
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "apps/pearnly_core/pearnly_core/execution/work.py"
DMS = ROOT / "apps/pearnly_dms/pearnly_dms"


class FakeError(Exception):
    pass


class CoreNotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(WORK.read_text())
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("_notify", "_finish")]
        if len(nodes) != 2:
            raise AssertionError("Expected real Core notification and terminal functions")
        cls.code = compile(ast.Module(body=nodes, type_ignores=[]), str(WORK), "exec")
        hooks = ast.parse((DMS / "hooks.py").read_text())
        cls.dms_hooks = next(ast.literal_eval(n.value) for n in hooks.body if isinstance(n, ast.Assign)
            and any(isinstance(v, ast.Name) and v.id == "pearnly_ocr_failure_handlers" for v in n.targets))
        callback_tree = ast.parse((DMS / "line/out.py").read_text())
        cls.callback_args = next(n.args for n in callback_tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "ocr_failed")

    def setUp(self):
        self.calls = []
        self.hooks = []
        self.arguments = {"binding": {"channel_key": "synthetic"},
                          "line_user_id": "synthetic", "message_id": "m1"}
        row = SimpleNamespace(kind="dms.id_card", arguments=json.dumps(self.arguments))
        db = SimpleNamespace(
            get_value=lambda *a, **kw: "slot" if a[2] == "admission_key" else row,
            commit=lambda: self.calls.append(("commit",)),
            rollback=lambda: self.calls.append(("rollback",)),
            sql=lambda *a: [("running",)],
            set_value=lambda *a, **kw: self.calls.append(("status", a[2]["status"])),
        )
        redis = SimpleNamespace(zrem=lambda *a: self.calls.append(("release", *a)))
        self.frappe = SimpleNamespace(db=db, get_hooks=lambda name: self.hooks, get_attr=self.callback,
            logger=lambda name: SimpleNamespace(exception=lambda text: self.calls.append(("diagnostic", text))))
        inputs = ModuleType("pearnly_core.execution.inputs")
        inputs.remove = lambda name: self.calls.append(("remove", name))
        patcher = patch.dict(sys.modules, {inputs.__name__: inputs})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.env = dict(__package__="pearnly_core.execution", frappe=self.frappe, json=json,
            DT="Pearnly OCR Work", ACTIVE=("queued", "running", "deferred"),
            budget=SimpleNamespace(redis=lambda: redis, PREFIX="test:", tenant=lambda: "scope"),
            timedelta=timedelta, utcnow=lambda: datetime(2026, 9, 21), RedisError=FakeError, Deferred=FakeError)
        exec(self.code, self.env)

    def callback(self, path):
        self.assertEqual(path, "pearnly_dms.line.out.ocr_failed")
        return lambda **kw: self.calls.append(("callback", kw))

    def assert_empty_notification(self, hooks):
        self.hooks = hooks
        self.env["_notify"]("work", "provider_outcome_unknown")
        self.assertEqual(self.calls, [])

    def test_no_hooks_native_empty_list(self):
        self.assert_empty_notification([])

    def test_no_hooks_empty_mapping(self):
        self.assert_empty_notification({})

    def test_no_hooks_none(self):
        self.assert_empty_notification(None)

    def test_registered_dms_path_and_exact_arguments_preserved(self):
        self.assertEqual(self.dms_hooks, {"dms.id_card": "pearnly_dms.line.out.ocr_failed"})
        self.assertEqual([arg.arg for arg in self.callback_args.args], ["arguments", "code"])
        # Frappe merges dictionary hook values into lists of configured paths.
        self.hooks = {key: [value] for key, value in self.dms_hooks.items()}
        self.env["_notify"]("work", "provider_outcome_unknown")
        self.assertEqual(self.calls, [("callback", {"arguments": self.arguments,
                                                    "code": "provider_outcome_unknown"})])

    def test_other_workflow_does_not_invoke_dms_handler(self):
        self.assert_empty_notification({"other.workflow": ["unused"]})

    def test_callback_failure_keeps_existing_diagnostic_and_does_not_escape(self):
        self.hooks = {key: [value] for key, value in self.dms_hooks.items()}
        def fail(**kwargs):
            raise FakeError("synthetic")
        self.frappe.get_attr = lambda path: fail
        self.env["_notify"]("work", "workflow_error")
        self.assertEqual(self.calls, [("diagnostic", "OCR terminal notification failed")])

    def assert_terminal_cleanup(self, status):
        self.env["_finish"]("work", status, "provider_outcome_unknown")
        self.assertIn(("status", status), self.calls)
        self.assertIn(("remove", "work"), self.calls)
        self.assertEqual([call for call in self.calls if call[0] == "release"],
                         [("release", "test:pending", "slot"), ("release", "test:pending:scope", "slot")])
        self.assertFalse(any(call[0] == "callback" for call in self.calls))

    def test_failed_without_hook_still_removes_inputs_and_both_slots(self):
        self.assert_terminal_cleanup("failed")

    def test_uncertain_without_hook_still_removes_inputs_and_both_slots(self):
        self.assert_terminal_cleanup("uncertain")


if __name__ == "__main__":
    unittest.main()
