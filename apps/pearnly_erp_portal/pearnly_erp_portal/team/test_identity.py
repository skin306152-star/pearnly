"""ERP caller integration without network or Site writes: private transport/policy."""
import unittest
from unittest.mock import MagicMock, patch

import requests

from pearnly_core.account import member_client, profile
from pearnly_erp_portal.team import login_settings, service
from pearnly_erp_portal.patches import v1_9_username_login


class TestMemberClient(unittest.TestCase):
    def setUp(self):
        self.native = MagicMock()
        self.native.PermissionError = PermissionError
        self.native.throw.side_effect = lambda message, *args: (_ for _ in ()).throw(PermissionError(message))
        self.native.conf = {"pearnly_tenant": "tenant-a", "pearnly_product": "erp",
            "pearnly_billing_api": "http://private.invalid", "pearnly_billing_host_header": "billing.internal",
            "pearnly_billing_token": "scoped-test-only", "pearnly_oidc_issuer": "https://identity.invalid/realms/lab",
            "pearnly_native_account": True}
        for module in (member_client, service, profile, v1_9_username_login, login_settings):
            p = patch.object(module, "frappe", self.native)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(member_client, "_", lambda text: text)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(member_client.requests, "post")
        self.post = p.start()
        self.addCleanup(p.stop)

    def response(self, message):
        result = MagicMock(status_code=200)
        result.json.return_value = {"message": message}
        return result

    def test_private_create_forwards_username_not_admin_secret(self):
        self.post.return_value = self.response({"ok": True, "email": "member@example.test", "subject": "sub-a"})
        self.assertEqual(member_client.create("member@example.test", "Username1", "fixture"), "sub-a")
        self.assertEqual(self.post.call_args.kwargs["json"]["username"], "Username1")
        self.assertEqual(self.post.call_args.kwargs["headers"]["X-Pearnly-Billing-Product"], "erp")
        self.assertNotIn("admin", str(self.post.call_args))
        # 控制面按本站实际签到的账号池建号（testcowork 是 Cowork 产品却签到业务池，2026-10-18）。
        self.assertEqual(self.post.call_args.kwargs["json"]["issuer"], "https://identity.invalid/realms/lab")

    def test_timeout_retry_has_stable_id(self):
        self.post.side_effect = [requests.Timeout(), self.response({"ok": True, "email": "member@example.test"})]
        member_client.reset_password("member@example.test", "fixture")
        self.assertEqual(self.post.call_args_list[0].kwargs["json"]["request_id"],
                         self.post.call_args_list[1].kwargs["json"]["request_id"])
        self.assertEqual({call.kwargs["json"]["issuer"] for call in self.post.call_args_list},
                         {"https://identity.invalid/realms/lab"})

    def test_other_product_and_bad_response_denied(self):
        self.native.conf["pearnly_product"] = "dms"
        with self.assertRaises(PermissionError):
            member_client.create("member@example.test", "Username1", "fixture")
        self.post.assert_not_called()
        self.native.conf["pearnly_product"] = "erp"
        self.post.return_value = self.response({"ok": True, "email": "another@example.test", "subject": "sub-a"})
        with self.assertRaises(PermissionError):
            member_client.create("member@example.test", "Username1", "fixture")

    def test_native_account_never_writes_local_password(self):
        self.assertFalse(service._local_password_allowed())
        self.native.conf.pop("pearnly_oidc_issuer")
        self.assertFalse(service._local_password_allowed())
        self.native.conf["pearnly_native_account"] = False
        self.assertTrue(service._local_password_allowed())

    def test_oidc_migration_does_not_rename_accounts(self):
        v1_9_username_login.execute()
        self.native.db.set_value.assert_not_called()
        self.native.db.set_single_value.assert_not_called()

    def test_verified_long_username_is_projected(self):
        doc = self.native.get_doc.return_value
        doc.first_name = "old"
        profile.apply_login_username("member@example.test", {"preferred_username": "a" * 80})
        self.assertEqual(doc.username, "a" * 80)
        doc.save.assert_called_once_with(ignore_permissions=True)


if __name__ == "__main__":
    unittest.main()
