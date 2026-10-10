"""网页设置里只读的「连接」（`folder_intake.rules.computers`，2026-10-10）：连着的电脑、在不在线、网页可读取的文件夹。

在 bench 的 Python 下运行（需要 frappe 可导入，不连库）。
"""
from types import SimpleNamespace
import unittest
from unittest import mock

import frappe

from pearnly_cowork.folder_intake import rules


def doc(name, adapter):
    return SimpleNamespace(name=name, adapter=adapter, label=adapter.upper())


VIEWS = {
    "C-E": {"label": "OFFICE-PC", "fresh": True, "version": "1.4.0", "roots": [{"id": "r1", "label": r"D:\\Invoices"}]},
    "C-M": {"label": "MR.ERP", "fresh": False, "version": "", "roots": []},
    "C-OLD": {"label": "OLD-PC", "fresh": False, "version": "1.3.20", "roots": []},
}


class Computers(unittest.TestCase):
    def run_computers(self, records, online=(), denied=()):
        docs = {r["name"]: doc(r["name"], r["adapter"]) for r in records}

        def connection(name):
            if name in denied:
                raise frappe.PermissionError
            return docs[name]

        with mock.patch.object(rules, "_require") as require, \
                mock.patch.object(rules.connections, "records", return_value=records), \
                mock.patch.object(rules, "_connection", side_effect=connection), \
                mock.patch.object(rules, "_reader_view", side_effect=lambda d: VIEWS[d.name]), \
                mock.patch("pearnly_cowork.companion.credentials.online", side_effect=lambda n: n in online), \
                mock.patch.object(rules.frappe, "clear_messages"):
            result = rules.computers()
        require.assert_called_once_with()   # 与定时任务页同一判据：录入权限
        return result

    def test_lists_connected_computers_with_status_and_folders(self):
        result = self.run_computers([{"name": "C-E", "adapter": "express", "enabled": 1},
                                     {"name": "C-OLD", "adapter": "mrerp", "enabled": 1}], online={"C-E"})
        self.assertEqual(result, [
            {"adapter": "express", "device": "OFFICE-PC", "online": True, "version": "1.4.0", "folders": [r"D:\\Invoices"]},
            {"adapter": "mrerp", "device": "OLD-PC", "online": False, "version": "1.3.20", "folders": []}])

    def test_express_online_follows_the_heartbeat_not_the_folder_sync(self):
        result = self.run_computers([{"name": "C-E", "adapter": "express", "enabled": 1}], online=())
        self.assertFalse(result[0]["online"])

    def test_skips_never_connected_disabled_other_and_not_permitted(self):
        result = self.run_computers([{"name": "C-M", "adapter": "mrerp", "enabled": 1},
                                     {"name": "C-E", "adapter": "express", "enabled": 0},
                                     {"name": "C-X", "adapter": "line", "enabled": 1},
                                     {"name": "C-OLD", "adapter": "mrerp", "enabled": 1}], denied={"C-OLD"})
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
