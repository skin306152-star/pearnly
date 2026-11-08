#!/usr/bin/env python3
"""前端请求方式与白名单接口声明不符的检查（scripts/check-http-methods.py）的规则自测。"""
import importlib.util
import pathlib
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("http_methods", pathlib.Path(__file__).resolve().parents[1] / "scripts/check-http-methods.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

RULES = {"app.companion.api.online": "GET", "app.companion.api.register": "POST", "pearnly_core.assistant.api.send": "POST",
         "pearnly_core.assistant.api.events": "GET"}


class HttpMethods(unittest.TestCase):
    def test_post_to_a_get_only_method_is_flagged(self):
        # 11-08 那两处：frappe.xcall 不给第三个参数按 POST 发
        source = 'const API = {companion: "app.companion.api."};\nawait frappe.xcall(API.companion + "online", {connection});\n'
        self.assertEqual(M.problems(source, RULES), [(2, "app.companion.api.online", "POST", "GET")])
        for call in ('frappe.call({method: "app.companion.api.online", args: {}})',
                     'p.call("app.companion.api.online", {}, "POST")',
                     'postMethod("app.companion.api.online", {})',
                     'site.post<unknown>("app.companion.api.online", {})'):
            self.assertEqual(len(M.problems(call, RULES)), 1, call)

    def test_get_to_a_post_only_method_is_flagged(self):
        for call in ('p.call("app.companion.api.register", {})',
                     'getMethod<Row[]>("app.companion.api.register")',
                     'frappe.xcall("app.companion.api.register", {}, "GET")',
                     'frappe.call({method: "app.companion.api.register", type: "GET"})'):
            self.assertEqual(len(M.problems(call, RULES)), 1, call)

    def test_matching_calls_and_unknown_methods_pass(self):
        for call in ('p.call("app.companion.api.online", {connection})',
                     'frappe.xcall("app.companion.api.online", {}, "GET")',
                     'frappe.call({method: "app.companion.api.online", args, type: "GET", silent: true})',
                     'frappe.xcall("app.companion.api.register", {})',
                     'const COMPANION = "app.companion.api.";\ngetMethod(COMPANION + "online", {});',
                     'const API = "app.companion.api.";\np.call(`${API}online`);',
                     'p.call(api + method)',  # 运行时拼的不判
                     'frappe.xcall("app.other.api.anything", {})'):
            self.assertEqual(M.problems(call, RULES), [], call)

    def test_assistant_page_helpers_carry_the_base_prefix(self):
        source = 'import { get, post } from "@/lib/frappe";\nawait get("send", {});\nawait post("events");\nawait post("send", {});\n'
        self.assertEqual([row[1:] for row in M.problems(source, RULES)],
                         [("pearnly_core.assistant.api.send", "GET", "POST"), ("pearnly_core.assistant.api.events", "POST", "GET")])
        # 没从 @/lib/frappe 引入的同名函数（Map.get、axios.post……）不算
        self.assertEqual(M.problems('const v = cache.get("send"); await other.post("send");', RULES), [])

    def test_declared_reads_only_single_method_whitelists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            module = root / "apps/demo/demo/api.py"
            module.parent.mkdir(parents=True)
            module.write_text('import frappe\n\n@frappe.whitelist(methods=["GET"])\ndef one():\n    pass\n\n'
                              "@frappe.whitelist(methods=['POST'])\ndef two():\n    pass\n\n"
                              '@frappe.whitelist(methods=["GET", "POST"])\ndef both():\n    pass\n\n'
                              "@frappe.whitelist()\ndef free():\n    pass\n")
            self.assertEqual(M.declared(root), {"demo.api.one": "GET", "demo.api.two": "POST"})

    def test_the_repository_is_clean(self):
        self.assertEqual(M.main([]), 0)


if __name__ == "__main__":
    unittest.main()
