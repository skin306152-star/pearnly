# -*- coding: utf-8 -*-
"""代录 × 新物料的接缝（11-12 晚，主会话定；CC 桌面版窗口负责这条接缝）：服务端发出的代录载荷带建新物料的四个行键
（单位码、科目组、分类、允许负库存）→ 盖 21 → CC 桌面版写账线放行（只放这四个）→ 代录开工前建档读到四个键。

服务端一侧：``common.finalize_payload`` 见到任一新键就盖 21（已有）；「代录单带这四键时照派、不因带新键拦下」由引导式窗口在
派单那侧对齐（接口约定第 8 节补的那一项）。CC 一侧：``payload_contract.OPERATOR_LINE_KEYS``、``guided_keys.operator_allowed``、
``express_operator/masters.py`` 建档时传四个键。真正按这四个键建出物料主档（逐栏）由 companion/tests/test_operator_masters.py 守。
"""
from __future__ import annotations

import ast
import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPANION = ROOT / "companion" / "src" / "companion"


class OperatorGuidedKeys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))
        sys.path.insert(0, str(ROOT / "companion" / "src"))
        try:
            cls.keys = importlib.import_module("companion.guided_keys")  # 纯函数，不依赖 dbf
            cls.client = importlib.import_module("companion.payload_contract")
        finally:
            sys.path.remove(str(ROOT / "companion" / "src"))
        from pearnly_core.express_rules.push import common

        cls.common = common

    def _operator(self, **line):
        return {"direction": "purchase", "doctype": "RR", "docdate_be": "690320", "ref_no": "INV-1", "entry_mode": "operator",
                "supplier": {"code": "S001", "name": "ร้านป้าแดง"},
                "items": [{"name": "น้ำมันเครื่อง", "qty": "2", "unit": "ถัง", "amount": "100.00", "item_mode": "stock_item", **line}],
                "items_status": "ok"}

    def test_the_four_keys_are_line_keys_of_the_guided_contract(self):
        self.assertEqual(self.client.OPERATOR_LINE_KEYS, ("unit_code", "acccod", "stkgrp", "negallow"))
        self.assertTrue(set(self.client.OPERATOR_LINE_KEYS) <= set(self.common.GUIDED_KEYS["line"]))

    def test_an_operator_payload_the_server_sends_with_the_four_keys_reaches_the_item_creation(self):
        four = {"unit_code": "ขด", "acccod": "ST01", "stkgrp": "RAW", "negallow": "N"}
        sent = self.common.finalize_payload(self._operator(**four))
        self.assertEqual(sent["payload_version"], self.common.GUIDED_PAYLOAD_VERSION)  # 服务端盖 21
        self.assertTrue(self.keys.uses(sent) and self.keys.operator_allowed(sent))  # CC 写账线放行
        line = self.keys.items(sent)[0]
        self.assertEqual(line["unit"], "ขด")  # 单位码换进 unit：建档与录单都按它
        self.assertEqual({key: line[key] for key in ("acccod", "stkgrp", "negallow")},
                         {"acccod": "ST01", "stkgrp": "RAW", "negallow": "N"})
        for other in ({"cost_choice": "none"}, {"cost": "9"}):
            self.assertFalse(self.keys.operator_allowed(self.common.finalize_payload(self._operator(**four, **other))))

    def test_the_operator_item_creation_passes_the_four_keys_to_the_shared_stock_item_creation(self):
        tree = ast.parse((COMPANION / "express_operator" / "masters.py").read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and getattr(node.func, "attr", "") == "ensure_stock_item"]
        self.assertEqual(len(calls), 1)
        self.assertTrue({"acccod", "stkgrp", "negallow", "unit"} <= {kw.arg for kw in calls[0].keywords})
        worker = (COMPANION / "worker.py").read_text(encoding="utf-8")
        self.assertIn("guided.uses(task.payload or {}) and not guided.operator_allowed(task.payload or {})", worker)


if __name__ == "__main__":
    unittest.main()
