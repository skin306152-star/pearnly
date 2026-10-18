"""连接类 DocType 共用的原生入口守卫（`pearnly_cowork.connections.controller`）：用它的每张表都要能过 before_validate。

2026-10-18 第 7 批第一次在真实站点上跑多电脑补丁：账套登记 / 可达 / 账套信息三张表没有 client 字段，守卫无条件写
`self.client` 报 AttributeError，迁移整段失败。不连库、不依赖 Frappe（最小替身）。"""
import importlib.util
import json
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"
DOCTYPES = APP / "pearnly_cowork/doctype"


def _load():
    frappe = types.ModuleType("frappe")
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")

    class Document:
        pass

    document.Document = Document
    sys.modules.update({"frappe": frappe, "frappe.model": model, "frappe.model.document": document})
    package = types.ModuleType("connections_under_test")
    package.__path__ = [str(APP / "connections")]
    access = types.ModuleType("connections_under_test.access")
    access.guard = lambda doc: None
    access.deny = lambda: None
    sys.modules.update({"connections_under_test": package, "connections_under_test.access": access})
    spec = importlib.util.spec_from_file_location("connections_under_test.controller", APP / "connections/controller.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _users():
    """用这个守卫的 DocType 与它们是否有 client 字段。"""
    found = {}
    for py in DOCTYPES.glob("*/*.py"):
        if "ConnectionDocument" in py.read_text(encoding="utf-8"):
            meta = json.loads(py.with_suffix(".json").read_text(encoding="utf-8"))
            found[py.parent.name] = "client" in {f["fieldname"] for f in meta["fields"]}
    return found


class ConnectionGuardTests(unittest.TestCase):
    def test_every_doctype_using_the_guard_passes_before_validate(self):
        controller = _load()
        users = _users()
        self.assertTrue(any(users.values()) and not all(users.values()), users)
        for name, has_client in users.items():
            doc = controller.ConnectionDocument.__new__(controller.ConnectionDocument)
            doc.meta = types.SimpleNamespace(has_field=lambda field, has=has_client: field == "client" and has)
            if has_client:
                doc.client = None
            doc.before_validate()
            if has_client:
                self.assertEqual(doc.client, "", name)
            else:
                self.assertFalse(hasattr(doc, "client"), name)


if __name__ == "__main__":
    unittest.main()
