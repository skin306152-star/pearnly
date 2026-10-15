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
                mock.patch.object(credentials.devices, "pool_claimed", return_value=False), \
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

    def test_replace_moves_the_connection_to_this_computer(self):
        _, issue, _ = self.run_register(express=True, replace="true", expected_generation=3)
        issue.assert_called_once_with("C-E", "rk", True, 3)


if __name__ == "__main__":
    unittest.main()
