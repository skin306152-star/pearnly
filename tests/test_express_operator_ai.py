"""代录 AI 接手的模型请求转发（`pearnly_core.express_operator.ai`，2026-11-03）：只认两种调用、两个模型、白名单字段与
``max_tokens`` 上限；每批费用上限到了回 budget（不再调）；没配密钥不调；累计按价目表估算。"""
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))


class Cache:
    def __init__(self):
        self.values = {}

    def get_value(self, key):
        return self.values.get(key)

    def set_value(self, key, value, expires_in_sec=None):
        self.values[key] = value


frappe = types.ModuleType("frappe")
frappe.cache = Cache()
frappe.logger = lambda name=None: types.SimpleNamespace(info=lambda *a: None, warning=lambda *a: None)
sys.modules.setdefault("frappe", frappe)
sys.modules["frappe"].cache = frappe.cache
sys.modules["frappe"].logger = frappe.logger

from pearnly_core.express_operator import ai  # noqa: E402

HAIKU, OPUS = ai.HAIKU, ai.OPUS


def body(**request):
    return {"batch": "B1", "kind": "turn", "request": {"model": HAIKU, "max_tokens": 8000, "messages": [], **request}}


class Forward(unittest.TestCase):
    def setUp(self):
        sys.modules["frappe"].cache.values.clear()
        patcher = mock.patch.dict(os.environ, {"PEARNLY_ANTHROPIC_API_KEY": "k"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sent = []

    def post(self, key, request):
        self.sent.append(request)
        return {"content": [], "stop_reason": "end_turn", "usage": {"input_tokens": 1_000_000, "output_tokens": 0}}

    def test_only_the_two_calls_and_models_and_whitelisted_fields_go_out(self):
        for bad in ({"batch": "", "kind": "turn", "request": {"model": HAIKU, "messages": []}},
                    {"batch": "B1", "kind": "other", "request": {"model": HAIKU, "messages": []}},
                    body(model="claude-other")):
            self.assertTrue(ai.forward(bad, post=self.post)["error"].startswith("invalid_"))
        self.assertEqual(self.sent, [])
        ai.forward(body(max_tokens=99999, metadata={"x": 1}, tools=[{"type": "computer_toolset_20260801"}]), post=self.post)
        self.assertEqual(self.sent[0]["max_tokens"], ai.MAX_TOKENS)
        self.assertNotIn("metadata", self.sent[0])
        self.assertEqual(self.sent[0]["tools"], [{"type": "computer_toolset_20260801"}])

    def test_the_batch_budget_stops_further_calls(self):
        out = ai.forward(body(model=OPUS), post=self.post)  # 100 万输入 token × Opus 4 美元 = 4 美元 ≥ 上限
        self.assertEqual(out["stop_reason"], "end_turn")
        self.assertAlmostEqual(ai.spent("B1"), 4.0)
        self.assertEqual(ai.forward(body(), post=self.post), {"error": "budget"})
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(ai.forward({**body(), "batch": "B2"}, post=self.post)["stop_reason"], "end_turn")  # 别的批次照常

    def test_without_a_key_nothing_is_called(self):
        with mock.patch.dict(os.environ, {"PEARNLY_ANTHROPIC_API_KEY": ""}):
            self.assertEqual(ai.forward(body(), post=self.post), {"error": "not_configured"})
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()


class Prices(unittest.TestCase):
    def test_the_price_table_is_the_recognition_engine_s(self):
        import ast
        source = (ROOT / "apps/pearnly_core/pearnly_core/claude_ocr/engine.py").read_text(encoding="utf-8")
        found = {}
        for node in ast.parse(source).body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") in ("PRICE", "HAIKU", "OPUS") \
                    or isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Tuple):
                exec(compile(ast.Module([node], []), "engine", "exec"), {}, found)  # noqa: S102 — 只取常量
        self.assertEqual((found["HAIKU"], found["OPUS"]), (ai.HAIKU, ai.OPUS))
        self.assertEqual(found["PRICE"], ai.PRICE)


class OlderCcDesktop(unittest.TestCase):
    """1.12.13 及更早的 CC 桌面版不调 AI 转发：服务端代录的派单、领取、回执一字不变。AI 转发只从两条新的机器方法进来，
    代录的任务、领取、回执、产品接入都不引用它；原有机器方法的表不变（新方法只加在新表 / 新键上）。"""

    def test_only_the_two_new_machine_methods_reach_the_ai_forwarding(self):
        import ast
        callers = []
        for path in sorted((ROOT / "apps").rglob("*.py")):
            if "/tests/" in str(path) or path.name.startswith("test_"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
                names += [f"{node.module}.{a.name}" for a in getattr(node, "names", [])
                          if isinstance(node, ast.ImportFrom) and node.module]
                if any(name == "pearnly_core.express_operator.ai" for name in names):
                    callers.append(str(path.relative_to(ROOT)))
        self.assertEqual(sorted(set(callers)), ["apps/pearnly_core/pearnly_core/cc_desktop/machine.py",
                                                "apps/pearnly_cowork/pearnly_cowork/companion/operator_api.py"])

    def test_the_existing_machine_methods_are_unchanged(self):
        source = (ROOT / "apps/pearnly_core/pearnly_core/cc_desktop/http_auth.py").read_text(encoding="utf-8")
        for operation, method in (("heartbeat", "heartbeat"), ("lease", "lease"), ("ack", "acknowledge"),
                                  ("request_next", "next_request"), ("request_complete", "complete_request"),
                                  ("leave", "leave"), ("mrerp_login", "mrerp_login")):
            self.assertIn(f'"{operation}": _PREFIX + "{method}"', source)
        cowork = (ROOT / "apps/pearnly_cowork/pearnly_cowork/companion/http_auth.py").read_text(encoding="utf-8")
        self.assertIn("METHODS = {'heartbeat': METHOD, 'lease': 'pearnly_cowork.companion.queue_api.lease',", cowork)
        self.assertIn("FOLDER_ONLY_ALLOWED = {**FOLDER_METHODS, **REQUEST_METHODS, **DEVICE_METHODS, **LOGIN_METHODS}", cowork)
