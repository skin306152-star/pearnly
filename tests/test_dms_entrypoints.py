"""Resolve every moved module and published legacy callable using real Frappe.

No calls to business functions: verifies RPC/queue resolution and unchanged
whitelist registration without issuing documents or sending messages.
"""
import ast
import importlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import frappe

ROOT = Path(__file__).resolve().parents[1]
MAP = json.loads((ROOT / "docs/development/dms-module-map.json").read_text())


class EntryPoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Patch before importing domain modules: some bind `from frappe import _`.
        # Translation packs themselves are still loaded by the real Frappe reader.
        translate = patch.object(frappe, "_", lambda text, *a, **kw: text)
        translate.start()
        cls.addClassCleanup(translate.stop)

    def test_all_domain_modules_import(self):
        for name in MAP["modules"].values():
            with self.subTest(name=name):
                module = importlib.import_module(name)
                self.assertTrue(Path(module.__file__).is_relative_to(ROOT))

    def test_queue_payload_and_card_language_contracts(self):
        import contextlib
        import io
        import runpy

        frappe.local.conf = frappe._dict(host_name="http://sample.test")
        frappe.local.site = "sample.test"
        frappe.local.lang = "th"
        with patch.object(frappe, "_", lambda text, *a, **kw: text):
            for case in ("flow-enqueue", "cards"):
                with self.subTest(case=case):
                    output = io.StringIO()
                    logs = self.assertLogs("pearnly_dms.intake.flow", level="ERROR") if case == "flow-enqueue" else contextlib.nullcontext()
                    with contextlib.redirect_stdout(output), logs:
                        runpy.run_path(str(ROOT / f"scripts/sample-verify-dms-{case}.py"), run_name="__main__")
                    self.assertIn("RESULT: ALL PASS", output.getvalue())
                    self.assertNotIn("RESULT: FAIL", output.getvalue())

    def test_old_rpc_and_queue_paths_resolve_to_same_function(self):
        frappe.local.flags = frappe._dict(in_uninstall=False, in_install=False)
        with patch.object(frappe, "get_installed_apps", return_value=["frappe", "pearnly_dms"]):
            for old, entry in MAP["compatibility"].items():
                target = importlib.import_module(entry["module"])
                tree = ast.parse(Path(target.__file__).read_text())
                rpc = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)
                       and any("whitelist" in ast.unparse(d) for d in n.decorator_list)}
                for name in entry["exports"]:
                    with self.subTest(path=f"{old}.{name}"):
                        func = frappe.get_attr(f"{old}.{name}")
                        self.assertIs(func, getattr(target, name))
                        self.assertEqual(func.__module__, entry["module"])
                        if name in rpc:
                            self.assertIn(func, frappe.whitelisted)


if __name__ == "__main__":
    unittest.main()
