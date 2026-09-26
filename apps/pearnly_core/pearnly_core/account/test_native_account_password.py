"""云端站点（身份服务原生账户）上，旧的改密 / 退出全部设备接口明确拒绝，不去取管理令牌。零 IO。"""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pearnly_core import password, sessions


class Refused(Exception):
    pass


class TestNativeAccountRefusal(unittest.TestCase):
    def setUp(self):
        self.native = MagicMock()
        self.native.PermissionError = Refused
        self.native.AuthenticationError = Refused
        self.native.throw.side_effect = lambda message, exc=Exception: (_ for _ in ()).throw(exc(message))
        self.native.session = SimpleNamespace(user="owner@example.test", sid="sid-1")
        self.native.conf = {"pearnly_native_account": True}
        for module in (password, sessions):
            for target, value in (("frappe", self.native), ("_", str)):
                p = patch.object(module, target, value)
                p.start()
                self.addCleanup(p.stop)

    def test_cloud_site_refuses_before_any_identity_admin_call(self):
        with patch.object(password, "requests") as http, patch.object(password, "_identity_for") as identity:
            with self.assertRaises(Refused):
                password.change_password("old-password", "new-password1", "new-password1")
            with self.assertRaises(Refused):
                sessions.logout_everywhere()
            identity.assert_not_called()
            http.post.assert_not_called()
            http.put.assert_not_called()
        self.native.get_all.assert_not_called()

    def test_local_sample_site_keeps_the_old_path(self):
        self.native.conf = {}
        with patch.object(sessions, "_my_rows", return_value=[]) as rows, \
                patch.object(sessions, "_mark_revoked"), patch.object(sessions, "_drop_local_sessions"):
            self.assertEqual(sessions.logout_everywhere(keep_current=True), {"ok": True, "revoked": 0})
            rows.assert_called_once_with("owner@example.test")


if __name__ == "__main__":
    unittest.main()
