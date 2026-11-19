"""Cowork 推送回执里的 ``meta.created_masters`` 只列这次真新建的存货（行上 ``created`` 为真）（11-19）。

以前把这张单所有行的存货码都列进来，11-19 被误读成「CC 新建了用品名当码的存货」（实为客户自己的档）。
不连库：从 ``push/native/service.py`` 源码只取 ``_response`` 这一个函数，配纯契约的 ``express_rules.push.common`` 跑。
"""
import ast
import pathlib
import sys
import unittest
from copy import deepcopy

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))
SERVICE = ROOT / "apps/pearnly_cowork/pearnly_cowork/push/native/service.py"

from pearnly_core.express_rules.push import common  # noqa: E402


def _load_response():
    tree = ast.parse(SERVICE.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_response")
    namespace = {"common": common, "deepcopy": deepcopy}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SERVICE), "exec"), namespace)
    return namespace["_response"]


class CreatedMastersTest(unittest.TestCase):
    def setUp(self):
        self.response = _load_response()

    def test_only_items_created_on_this_document_are_listed(self):
        modes = [{"mode": common.ITEM_MODE_STOCK, "stkcod": "NEW-1", "created": True},
                 {"mode": common.ITEM_MODE_STOCK, "stkcod": "OLD-1", "created": False},
                 {"mode": common.ITEM_MODE_STOCK, "stkcod": "OLD-2"}]
        body = self.response("legacy", common.STAGE_SUCCESS, "IV1", modes, {})
        self.assertEqual(body["meta"]["created_masters"], ["NEW-1"])

    def test_a_document_that_created_nothing_has_no_list(self):
        modes = [{"mode": common.ITEM_MODE_STOCK, "stkcod": "OLD-1", "created": False}]
        body = self.response("legacy", common.STAGE_SUCCESS, "IV1", modes, {})
        self.assertNotIn("created_masters", body["meta"])

    def test_a_list_reported_by_the_desktop_is_kept(self):
        modes = [{"mode": common.ITEM_MODE_STOCK, "stkcod": "NEW-1", "created": True}]
        body = self.response("legacy", common.STAGE_SUCCESS, "IV1", modes, {"created_masters": ["X"]})
        self.assertEqual(body["meta"]["created_masters"], ["X"])

    def test_a_failed_push_lists_nothing(self):
        modes = [{"mode": common.ITEM_MODE_STOCK, "stkcod": "NEW-1", "created": True}]
        body = self.response("legacy", common.STAGE_FAILED, None, modes, {})
        self.assertNotIn("created_masters", body["meta"])


if __name__ == "__main__":
    unittest.main()
