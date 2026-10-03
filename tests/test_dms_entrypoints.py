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


class RichMenuPublish(unittest.TestCase):
    """Re-publishing the same menus must not re-upload an image LINE already has.

    Regression for the 2026-09-20 failure: LINE answers 400 "An image has already been
    uploaded to the richmenu", which aborted the tenant's `set_line` step.
    """

    def publish_twice(self):
        from pearnly_dms.menu import rich_menu

        calls = []
        state = {"menus": {}, "images": set(), "next_id": 0}

        class Response:
            def __init__(self, status, payload=None, content=b""):
                self.status_code, self.content = status, content or b"{}"
                self._payload = payload or {}
                self.text = json.dumps(self._payload)

            def json(self):
                return self._payload

        class Lock:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class Cache:
            def make_key(self, key):
                return key

            def lock(self, *a, **kw):
                return Lock()

        def request(method, url, **kwargs):
            path = url.rsplit("/v2/bot/", 1)[-1]
            if method == "GET" and path.endswith("/content"):
                calls.append(("read-image", path))
                return Response(200 if path.split("/")[1] in state["images"] else 404, {}, b"png")
            calls.append(("image" if kwargs.get("data") else method, path))
            if method == "GET" and path == "richmenu/list":
                return Response(200, {"richmenus": [{"name": name, "richMenuId": menu_id}
                                                    for name, menu_id in state["menus"].items()]})
            if method == "POST" and path.endswith("/content"):
                state["images"].add(path.split("/")[1])
                return Response(200)
            if method == "POST" and path == "richmenu":
                state["next_id"] += 1
                menu_id = "richmenu-created-%d" % state["next_id"]
                state["menus"][kwargs["json"]["name"]] = menu_id
                return Response(200, {"richMenuId": menu_id})
            return Response(200)

        conf = frappe._dict(pearnly_line_channel_access_token="token",
                            pearnly_line_liff_id="1234-abcd", host_name="https://sample.test")
        with patch.object(frappe, "conf", conf), \
             patch.object(frappe, "cache", lambda *a, **kw: Cache()), \
             patch.object(rich_menu, "_state_store", lambda: Cache()), \
             patch.object(rich_menu, "reconcile"), \
             patch.object(rich_menu.line.requests, "request", side_effect=request), \
             patch("frappe.installer.update_site_config"):
            rich_menu.publish()
            first = len([call for call in calls if call[0] == "image"])
            rich_menu.publish()
        return first, len([call for call in calls if call[0] == "image"]) - first, calls

    def test_re_publishing_the_same_menus_does_not_upload_the_image_again(self):
        from pearnly_dms.menu import rich_menu

        first, second, calls = self.publish_twice()
        # 每种语言 × 基础/带查询 各一套菜单（用户 2026-09-27：菜单跟着手机语言）。
        self.assertEqual(first, len(rich_menu.LANGUAGES) * len(rich_menu.VARIANTS),
                         "the first publish uploads one image per menu")
        self.assertEqual(second, 0, "the second publish must not re-upload an existing image")
        self.assertTrue([call for call in calls if call[0] == "POST" and call[1] == "user/all/richmenu/richmenu-created-1"],
                        "the OA default menu is still set on every publish")


if __name__ == "__main__":
    unittest.main()
