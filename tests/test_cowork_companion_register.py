"""CC 桌面端用登录的账号连上这台电脑（`credentials.register`，2026-10-10 起取代手抄配对码）。

在 bench 的 Python 下运行（需要 frappe 可导入，不连库）。
"""
from types import SimpleNamespace
import unittest
from unittest import mock

from frappe import _dict

from pearnly_cowork.companion import credentials

ACTOR = "admin@example.com"


def rows(*pairs):
    return [_dict(name=name, adapter=adapter) for name, adapter in pairs]


class DeviceConnection(unittest.TestCase):
    def pick(self, found, express):
        db = SimpleNamespace(sql=mock.Mock(return_value=found))
        with mock.patch.object(credentials.frappe, "db", db), \
                mock.patch.object(credentials.access, "default_workspace", return_value="WS"):
            return credentials._device_connection(express)

    def test_computer_with_express_uses_express(self):
        self.assertEqual(self.pick(rows(("C-M", "mrerp"), ("C-E", "express")), True), "C-E")

    def test_computer_without_express_reads_folders_for_mrerp_first(self):
        self.assertEqual(self.pick(rows(("C-E", "express"), ("C-M", "mrerp")), False), "C-M")
        self.assertEqual(self.pick(rows(("C-E", "express")), False), "C-E")

    def test_computer_with_express_never_takes_the_mrerp_connection(self):
        # 本站只有 MR.ERP 连接：装了 Express 的电脑不连它，回 None 让 register 新建 Express 连接
        self.assertIsNone(self.pick(rows(("C-M", "mrerp")), True))

    def test_nothing_to_connect(self):
        self.assertIsNone(self.pick([], True))
        self.assertIsNone(self.pick([], False))


class Register(unittest.TestCase):
    def run_register(self, *, connection="C-E", issued=None, **kwargs):
        db = SimpleNamespace(get_value=mock.Mock(return_value="express"))
        issue = mock.Mock(return_value=issued or {"token": "exp_C-E_secret", "generation": 1})
        configure = mock.Mock(return_value={"name": "C-NEW"})
        with mock.patch.object(credentials.frappe, "db", db), \
                mock.patch.object(credentials, "_enabled"), \
                mock.patch.object(credentials.access, "require_actor", return_value=ACTOR) as actor, \
                mock.patch.object(credentials.access, "default_workspace", return_value="WS"), \
                mock.patch.object(credentials, "_device_connection", return_value=connection), \
                mock.patch.object(credentials, "issue", issue), \
                mock.patch("pearnly_cowork.connections.service.configure", configure):
            result = credentials.register("rk", **kwargs)
        # D1（ADR-021）：录入或管理角色都能给自己的电脑登记；建连接（池）仍只限管理员
        self.assertEqual(actor.call_args_list[0], mock.call())
        return result, issue, configure

    def test_new_computer_gets_a_key_for_its_desktop(self):
        result, issue, configure = self.run_register(express=True)
        self.assertEqual(result, {"status": "created", "connection": "C-E", "adapter": "express",
                                  "token": "exp_C-E_secret"})
        issue.assert_called_once_with("C-E", "rk", False, None)
        configure.assert_not_called()

    def test_express_computer_creates_the_express_connection_when_missing(self):
        _, issue, configure = self.run_register(connection=None, express="1")
        configure.assert_called_once_with(None, "express", "rk", "Express", {})
        issue.assert_called_once_with("C-NEW", "rk", False, None)

    def test_no_express_and_no_connection_creates_nothing(self):
        result, issue, configure = self.run_register(connection=None, express=False)
        self.assertEqual(result, {"status": "no_connection"})
        issue.assert_not_called()
        configure.assert_not_called()

    def test_connection_already_used_by_another_computer_is_not_taken_over(self):
        held = {"generation": 3, "online": True, "issuer": "boss@example.com", "status": "exists"}
        result, issue, _ = self.run_register(issued=held, express=True)
        self.assertEqual(result, {"status": "in_use", "connection": "C-E", "adapter": "express", "generation": 3,
                                  "online": True, "issuer": "boss@example.com", "self": False})
        issue.assert_called_once_with("C-E", "rk", False, None)

    def test_old_client_is_told_to_update_once_a_computer_is_claimed(self):
        # issue() decides it after locking the connection (review finding 1); register only words the reply
        result, issue, _ = self.run_register(issued={"status": "update_needed", "replayed": False}, express=True)
        self.assertEqual(result, {"status": "update_needed", "connection": "C-E", "adapter": "express"})

    def run_mrerp_only(self, *, enabled, manager):
        """只有 MR.ERP、没有 Express 连接的工作空间，装了 Express 的电脑来登记（ADR-026 第 3 步，11-03 选 A）。"""
        from pearnly_core.express_operator import contracts as operator_contracts

        picked = []

        def device_connection(express, current=False):
            picked.append(express)
            return None if express else "C-M"

        db = SimpleNamespace(get_value=mock.Mock(side_effect=lambda doctype, name, field, **kw:
                                                 "mrerp" if name == "C-M" else "express"))
        issue = mock.Mock(return_value={"token": "exp_C-M_secret", "generation": 1})
        configure = mock.Mock(return_value={"name": "C-NEW"})
        with mock.patch.object(operator_contracts, "MRERP_OPERATOR_ENABLED", enabled), \
                mock.patch.object(credentials.frappe, "db", db), \
                mock.patch.object(credentials, "_enabled"), \
                mock.patch.object(credentials.access, "require_actor", return_value=ACTOR), \
                mock.patch.object(credentials.access, "default_workspace", return_value="WS"), \
                mock.patch.object(credentials, "_manager", return_value=manager), \
                mock.patch.object(credentials, "_device_connection", side_effect=device_connection), \
                mock.patch.object(credentials, "issue", issue), \
                mock.patch("pearnly_cowork.connections.service.configure", configure):
            result = credentials.register("rk", express=True)
        return result, picked, issue, configure

    def test_mrerp_only_workspace_with_the_constant_off_is_unchanged(self):
        result, picked, issue, configure = self.run_mrerp_only(enabled=False, manager=False)
        self.assertEqual(result, {"status": "no_connection"})
        self.assertEqual(picked, [True])
        issue.assert_not_called()
        configure.assert_not_called()

    def test_mrerp_only_workspace_connects_a_non_manager_to_the_mrerp_connection_with_the_constant_on(self):
        result, picked, issue, configure = self.run_mrerp_only(enabled=True, manager=False)
        self.assertEqual(result, {"status": "created", "connection": "C-M", "adapter": "mrerp", "token": "exp_C-M_secret"})
        self.assertEqual(picked, [True, False])
        issue.assert_called_once_with("C-M", "rk", False, None)
        configure.assert_not_called()

    def test_a_manager_still_creates_the_express_connection_with_the_constant_on(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                result, picked, issue, configure = self.run_mrerp_only(enabled=enabled, manager=True)
                configure.assert_called_once_with(None, "express", "rk", "Express", {})
                self.assertNotIn(False, picked)

    def test_replace_moves_the_connection_to_this_computer(self):
        _, issue, _ = self.run_register(express=True, replace="true", expected_generation=3)
        issue.assert_called_once_with("C-E", "rk", True, 3)


if __name__ == "__main__":
    unittest.main()
