"""DMS 挪到不淘汰 Redis 的状态存储：真站点、真 Redis、不替换任何被测函数（只在流程实验室跑）。

在 dms-flow-lab 的 bench 里：
    env/bin/python /workspace/scripts/test-dms-state-store-native.py

2026-10-03：上一批把锁、票据挪到 ``pearnly_core.execution.state`` 时，``editor/browser_ticket._state_store`` 写成了
自己调用自己，所有 DMS 站点的 LIFF 编辑器打不开约 7.5 小时；当时的测试都把这个函数替换掉了，所以没测出来。
这里对每个 ``_state_store`` 真写真读真删一个只属于本次的键，并把编辑票据走一遍发出 → 读回 → 作废。
不连 LINE / DMS；临时 LINE 绑定与键都在收尾按名字删掉。
"""
import importlib
import runpy
import unittest
import uuid
from pathlib import Path

import frappe

MODULES = (
    "pearnly_dms.config.pacing", "pearnly_dms.booking.delivery", "pearnly_dms.booking.recovery",
    "pearnly_dms.adapter.change_sync", "pearnly_dms.adapter.session_cache", "pearnly_dms.intake.customer_attempt",
    "pearnly_dms.menu.rich_menu", "pearnly_dms.account.api", "pearnly_dms.query.snapshot_sync",
    "pearnly_dms.approval.approval_store", "pearnly_dms.editor.browser_ticket",
)


class StateStoreNative(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runpy.run_path(str(Path(__file__).with_name("test-dms-native-flow.py")))["connect"]()

    @classmethod
    def tearDownClass(cls):
        frappe.destroy()

    def test_every_moved_store_really_writes_and_reads(self):
        # 防漏：DMS 里新增的 `_state_store` 也要登记到这里
        found = sorted(".".join(path.with_suffix("").parts[path.parts.index("pearnly_dms"):])
                       for path in Path(frappe.get_app_path("pearnly_dms")).rglob("*.py")
                       if "def _state_store(" in path.read_text())
        self.assertEqual(found, sorted(MODULES))
        for name in MODULES:
            with self.subTest(module=name):
                store = importlib.import_module(name)._state_store()
                key = f"pearnly:dms:state-store-native:{uuid.uuid4().hex}"
                try:
                    store.set_value(key, {"module": name}, expires_in_sec=60)
                    self.assertEqual(store.get_value(key), {"module": name})
                finally:
                    store.delete_value(key)
                self.assertIsNone(store.get_value(key))

    def test_editor_ticket_round_trip(self):
        from pearnly_core import line_binding
        from pearnly_dms.editor import browser_ticket as tickets

        line_user_id = "U" + uuid.uuid4().hex
        line_binding.bind(line_user_id, "Administrator", "state store native")
        frappe.db.commit()
        try:
            ticket = tickets.issue(line_user_id, "customer")
            resolved = tickets.resolve(ticket)
            self.assertEqual((resolved["line_user_id"], resolved["mode"]), (line_user_id, "customer"))
            tickets.revoke(ticket)
            self.assertIsNone(tickets.resolve(ticket))
        finally:
            line_binding.unbind(line_user_id)
            frappe.db.commit()


if __name__ == "__main__":
    unittest.main()
