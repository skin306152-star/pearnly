"""录入工作台上传（`pearnly_cowork.files.api.upload`）：原件存放处一时连不上（甲骨文 / 桶，`execution.errors.Deferred`）时回
「暂时无法上传，请稍后重试。」（``upload.deferred``，同代录上传），不回 500；死锁照旧重试三次。11-06 测试机：实验栈连不上甲骨文，
上传回 500、页面只显示通用失败。不连库、不装 Frappe：替身只给 api.py 用到的几项。

    python3 tests/test_cowork_upload_deferred.py
"""
import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))

from pearnly_core.execution.errors import Deferred  # noqa: E402


class Thrown(Exception):
    pass


class Deadlock(Exception):
    pass


def _load(service_create):
    calls = {"rollback": 0}
    frappe = types.ModuleType("frappe")
    frappe._ = lambda text: text
    frappe.whitelist = lambda **kw: (lambda fn: fn)

    def throw(message, exc=None):
        raise Thrown(message)

    frappe.throw = throw
    frappe.QueryDeadlockError = Deadlock
    frappe.DuplicateEntryError = type("Duplicate", (Exception,), {})
    frappe.db = types.SimpleNamespace(rollback=lambda: calls.__setitem__("rollback", calls["rollback"] + 1))
    content = b"%PDF-1.4 bill"
    frappe.request = types.SimpleNamespace(files={"file": types.SimpleNamespace(
        filename="bill.pdf", stream=types.SimpleNamespace(read=lambda size: content))})
    modules = {
        "frappe": frappe,
        "frappe.core": types.ModuleType("frappe.core"),
        "frappe.core.doctype": types.ModuleType("frappe.core.doctype"),
        "frappe.core.doctype.access_log": types.ModuleType("frappe.core.doctype.access_log"),
        "frappe.core.doctype.access_log.access_log": types.SimpleNamespace(make_access_log=lambda **kw: None),
        "frappe.core.api": types.ModuleType("frappe.core.api"),
        "frappe.core.api.file": types.SimpleNamespace(zip_files=lambda *a, **kw: None),
        "pearnly_cowork": types.ModuleType("pearnly_cowork"),
        "pearnly_cowork.access": types.ModuleType("pearnly_cowork.access"),
        "pearnly_cowork.access.scope": types.SimpleNamespace(
            require=lambda *a: None, require_scope=lambda *a: None, default_workspace=lambda: "W"),
        "pearnly_cowork.files": types.ModuleType("pearnly_cowork.files"),
        "pearnly_cowork.files.access": types.SimpleNamespace(),
        "pearnly_cowork.files.service": types.SimpleNamespace(MAX_BYTES=100, create=service_create),
        "pearnly_core.originals.checks": types.SimpleNamespace(),
    }
    modules["pearnly_cowork.access"].scope = modules["pearnly_cowork.access.scope"]
    modules["pearnly_cowork.files"].access = modules["pearnly_cowork.files.access"]
    modules["pearnly_cowork.files"].service = modules["pearnly_cowork.files.service"]
    saved = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        spec = importlib.util.spec_from_file_location(
            "cowork_files_api_under_test", ROOT / "apps/pearnly_cowork/pearnly_cowork/files/api.py")
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return api, calls


class UploadDeferred(unittest.TestCase):
    def test_an_unreachable_store_answers_the_retry_sentence(self):
        def create(*args):
            raise Deferred("oracle_unreachable", 10)

        api, calls = _load(create)
        with self.assertRaises(Thrown) as caught:
            api.upload("W", "k1")
        self.assertEqual(str(caught.exception), "upload.deferred")
        self.assertEqual(calls["rollback"], 1)

    def test_a_stored_file_and_deadlock_retries_are_unchanged(self):
        tries = []

        def create(workspace, filename, content, key, client):
            tries.append(key)
            if len(tries) < 3:
                raise Deadlock()
            return {"name": "CWS-1", "filename": filename, "size": len(content)}

        api, calls = _load(create)
        self.assertEqual(api.upload("W", "k1"), {"name": "CWS-1", "filename": "bill.pdf", "size": 13})
        self.assertEqual((len(tries), calls["rollback"]), (3, 2))


if __name__ == "__main__":
    unittest.main()
