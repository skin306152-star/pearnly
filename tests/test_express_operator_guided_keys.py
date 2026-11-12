"""代录 × 引导录入载荷 21（CC 引导式录入第 1 期，接口约定第 8 节「代录 × 新物料」，接缝负责人 CC 桌面版窗口）：
代录单只可以带四个行键（单位码、存货科目组、分类、允许负库存），带了就版本至少 21 照派；带其余载荷 21 新键（引导开关、到期日、
收款科目、出库成本、对方类型 / 账期）的代录单服务端不发——拼代录载荷时直接报错（CC 桌面版也会回 PAYLOAD_INVALID 兜底）。

    python3 tests/test_express_operator_guided_keys.py
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.express_operator import contracts  # noqa: E402
from pearnly_core.express_rules.push import common  # noqa: E402

BATCH = {"id": "b1", "index": 1, "total": 1, "start_at": 0}


class FourLineKeys(unittest.TestCase):
    def test_constant_is_the_four_line_keys_of_payload_21(self):
        self.assertEqual(contracts.OPERATOR_GUIDED_LINE_KEYS, ("unit_code", "acccod", "stkgrp", "negallow"))
        self.assertLessEqual(set(contracts.OPERATOR_GUIDED_LINE_KEYS), set(common.GUIDED_KEYS["line"]))
        self.assertEqual(contracts.GUIDED_PAYLOAD_VERSION, common.GUIDED_PAYLOAD_VERSION)

    def test_four_keys_go_out_at_21(self):
        payload = {"payload_version": 19, "items": [{"name": "Ice", "unit_code": "BOX", "acccod": "01", "stkgrp": "A",
                                                       "negallow": ""}]}
        made = contracts.for_operator(payload, BATCH)
        self.assertEqual(made["payload_version"], 21)
        self.assertEqual(made["items"][0]["unit_code"], "BOX")

    def test_without_new_keys_nothing_changes(self):
        self.assertEqual(contracts.for_operator({"payload_version": 9}, BATCH)["payload_version"], 16)

    def test_other_new_keys_are_refused(self):
        for payload in ({"guided": True}, {"due_date_be": "25691231"}, {"cash_account": "11-01"},
                        {"items": [{"cost_choice": "none"}]}, {"items": [{"cost": "1"}]},
                        {"supplier": {"type": "01"}}, {"customer": {"payterm": 30}}):
            with self.assertRaises(ValueError, msg=str(payload)):
                contracts.for_operator(payload, BATCH)


if __name__ == "__main__":
    unittest.main()
