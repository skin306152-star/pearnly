"""Express 账套以网页选择为准（用户 2026-09-26）：小助手上报的可写账套都可作推送目标。

在 bench 的 Python 下运行（需要 frappe 可导入，不连库）。
"""
import unittest

from pearnly_cowork.connections.preparation import reported_writable

ROOT_67 = r"\\accserver\ExpressI\2567"
ROOT_68 = r"\\accserver\ExpressI\2568"
REPORT = {"reported_account_sets": [
    {"path": ROOT_68 + r"\ACME", "root": ROOT_68, "writable": True},
    {"path": ROOT_67 + r"\ACME", "root": ROOT_67},
    {"path": ROOT_68 + r"\LOCKED", "root": ROOT_68, "writable": False},
]}


def key(value):
    from pearnly_cowork.targets.identity import normalize_express_account_key
    return normalize_express_account_key(value)


class DynamicAccount(unittest.TestCase):
    def test_any_reported_writable_account_under_its_root_is_authorized(self):
        self.assertTrue(reported_writable(REPORT, key(ROOT_68 + r"\ACME"), key(ROOT_68)))
        self.assertTrue(reported_writable(REPORT, key(ROOT_67 + r"\ACME"), key(ROOT_67)))

    def test_unreported_locked_or_other_root_is_not(self):
        self.assertFalse(reported_writable(REPORT, key(ROOT_68 + r"\LOCKED"), key(ROOT_68)))
        self.assertFalse(reported_writable(REPORT, key(ROOT_68 + r"\OTHER"), key(ROOT_68)))
        self.assertFalse(reported_writable(REPORT, key(ROOT_68 + r"\ACME"), key(ROOT_67)))
        self.assertFalse(reported_writable({}, key(ROOT_68 + r"\ACME"), key(ROOT_68)))


if __name__ == "__main__":
    unittest.main()
