"""网页设置里只读的「连接」（`folder_intake.rules.computers`，2026-10-10）：连着的电脑、在不在线、网页可读取的文件夹。

在 bench 的 Python 下运行（需要 frappe 可导入，不连库）。
"""
import json
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
    def run_computers(self, records, online=(), denied=(), members=None, owners=None, user="me@example.com",
                      manager=False):
        """``members``：各条连接上的电脑（ADR-021 第 4 批：一台电脑一行）；不给就每条连接一台，在线按 ``online``。"""
        docs = {r["name"]: doc(r["name"], r["adapter"]) for r in records}
        members = members if members is not None else {
            name: {"PC-" + name: {"online": name in online}} for name in docs}

        def connection(name):
            if name in denied:
                raise frappe.PermissionError
            return docs[name]

        with mock.patch.object(rules, "_require") as require, \
                mock.patch.object(rules.connections, "records", return_value=records), \
                mock.patch.object(rules, "_connection", side_effect=connection), \
                mock.patch.object(rules.readers, "members", side_effect=lambda n: members.get(n, {})), \
                mock.patch.object(rules.devices, "owners", side_effect=lambda names: {
                    name: owner for name, owner in (owners or {}).items() if name in names}), \
                mock.patch("pearnly_cowork.companion.credentials.manages", return_value=manager), \
                mock.patch.object(rules.frappe, "session", SimpleNamespace(user=user)), \
                mock.patch.object(rules, "_reader_view",
                                  side_effect=lambda d, device="", peer=None: VIEWS.get(f"{device}|{d.name}", VIEWS[d.name])), \
                mock.patch.object(rules.frappe, "clear_messages"):
            result = rules.computers()
        require.assert_called_once_with()   # 与定时任务页同一判据：录入权限
        return result

    def test_lists_connected_computers_with_status_and_folders(self):
        result = self.run_computers([{"name": "C-E", "adapter": "express", "enabled": 1},
                                     {"name": "C-OLD", "adapter": "mrerp", "enabled": 1}], online={"C-E"})
        self.assertEqual(result, [
            {"adapter": "express", "device": "OFFICE-PC", "online": True, "version": "1.4.0", "folders": [r"D:\\Invoices"],
             "id": "PC-C-E", "connection": "C-E", "always_on": False, "can_remove": False},
            {"adapter": "mrerp", "device": "OLD-PC", "online": False, "version": "1.3.20", "folders": [],
             "id": "PC-C-OLD", "connection": "C-OLD", "always_on": False, "can_remove": False}])

    def test_the_owner_or_an_admin_may_remove_a_computer(self):
        """第 6 批「电脑」列表：登记人或管理员才给 ``can_remove``；没挂电脑的旧槽位密钥（不是电脑记录）不给。"""
        members = {"C-E": {"PC1": {"online": True, "always_on": True}, "PC2": {"online": False},
                           "KEY-OLD": {"online": False}}}
        views = {**VIEWS, "PC2|C-E": {**VIEWS["C-E"], "label": "ACC-02"}, "KEY-OLD|C-E": {**VIEWS["C-E"], "label": "OLD"}}
        owners = {"PC1": "me@example.com", "PC2": "boss@example.com"}
        records = [{"name": "C-E", "adapter": "express", "enabled": 1}]
        with mock.patch.dict(VIEWS, views):
            mine = self.run_computers(records, members=members, owners=owners)
            boss = self.run_computers(records, members=members, owners=owners, user="admin@example.com", manager=True)
        self.assertEqual([(row["id"], row["can_remove"], row["always_on"]) for row in mine],
                         [("KEY-OLD", False, False), ("PC1", True, True), ("PC2", False, False)])
        self.assertEqual([(row["id"], row["can_remove"]) for row in boss],
                         [("KEY-OLD", False), ("PC1", True), ("PC2", True)])

    def test_express_online_follows_the_heartbeat_not_the_folder_sync(self):
        result = self.run_computers([{"name": "C-E", "adapter": "express", "enabled": 1}], online=())
        self.assertFalse(result[0]["online"])

    def test_every_computer_of_a_pool_is_its_own_row(self):
        """多台电脑（ADR-021 第 4 批）：同一个 Express 池里两台电脑各一行，各看各的心跳。"""
        views = {**VIEWS, "PC2|C-E": {"label": "ACC-02", "fresh": False, "version": "1.6.0", "roots": []}}
        with mock.patch.dict(VIEWS, views):
            result = self.run_computers([{"name": "C-E", "adapter": "express", "enabled": 1}],
                                        members={"C-E": {"PC1": {"online": True}, "PC2": {"online": False}}})
        self.assertEqual([(row["device"], row["online"]) for row in result], [("OFFICE-PC", True), ("ACC-02", False)])

    def test_a_blocked_update_is_one_more_line_only_when_there_is_one(self):
        # 教程问题第 1 条：CC 桌面版的自动更新被 Windows 拦下 → 这一行多带 update_blocked（版本 + 时刻），网页如实显示一句
        blocked = {"version": "1.5.2", "at": "2026-10-13T11:18:00+00:00"}
        views = {**VIEWS, "C-E": {**VIEWS["C-E"], "update_blocked": blocked}}
        with mock.patch.dict(VIEWS, views):
            result = self.run_computers([{"name": "C-E", "adapter": "express", "enabled": 1},
                                         {"name": "C-OLD", "adapter": "mrerp", "enabled": 1}], online={"C-E"})
        self.assertEqual(result[0]["update_blocked"], blocked)
        self.assertNotIn("update_blocked", result[1])

    def test_reader_view_takes_the_block_from_the_heartbeat_for_express_and_the_sync_for_mrerp(self):
        blocked = {"version": "1.5.2", "at": "2026-10-13T11:18:00Z"}
        shown = {"version": "1.5.2", "at": "2026-10-13T11:18:00+00:00"}

        def view(adapter, report, state):
            peer = {"version": report.get("companion_version")} if adapter == "express" else {}
            with mock.patch.object(rules.readers, "state", return_value=state), \
                    mock.patch.object(rules.devices, "newest_version", return_value=""), \
                    mock.patch.object(rules.agent, "fresh", return_value=True), \
                    mock.patch.object(rules.frappe, "db", SimpleNamespace(get_value=lambda *a, **k: json.dumps(report))):
                return rules._reader_view(doc("C-1", adapter), "PC1", peer)["update_blocked"]

        # Express：以心跳报的为准（文件夹同步那份不算）
        self.assertEqual(view("express", {"update_blocked": blocked, "companion_version": "1.5.1"},
                              {"version": "1.5.1"}), shown)
        self.assertIsNone(view("express", {"companion_version": "1.5.1"},
                               {"version": "1.5.1", "update_blocked": blocked}))
        # 已经手动装上 1.5.2：旧值还没清也不显示
        self.assertIsNone(view("express", {"update_blocked": blocked, "companion_version": "1.5.2"}, {}))
        # MR.ERP 的电脑只读文件夹：以文件夹同步报的为准
        self.assertEqual(view("mrerp", {}, {"version": "1.5.1", "update_blocked": blocked}), shown)
        self.assertIsNone(view("mrerp", {"update_blocked": blocked}, {"version": "1.5.1"}))

    def test_skips_never_connected_disabled_other_and_not_permitted(self):
        result = self.run_computers([{"name": "C-M", "adapter": "mrerp", "enabled": 1},
                                     {"name": "C-E", "adapter": "express", "enabled": 0},
                                     {"name": "C-X", "adapter": "line", "enabled": 1},
                                     {"name": "C-OLD", "adapter": "mrerp", "enabled": 1}], denied={"C-OLD"})
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
