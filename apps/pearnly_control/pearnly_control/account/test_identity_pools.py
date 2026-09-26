"""Zero-IO tests: Cowork has its own account pool (realm), ERP / DMS stay in the business pool."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from pearnly_control import issues
from pearnly_control.account import identity_pools
from pearnly_core import keycloak_admin as kc


class Issue(dict):
    def __getattr__(self, name):
        return self.get(name)

    def set(self, key, value):
        self[key] = value


class TestPools(unittest.TestCase):
    def test_products_map_to_pools(self):
        self.assertEqual(identity_pools.pools_for(["dms", "erp"]), ["erp"])
        self.assertEqual(identity_pools.pools_for(["cowork"]), ["cowork"])
        self.assertEqual(identity_pools.pools_for(["cowork", "dms"]), ["erp", "cowork"])
        self.assertEqual(kc.pool_fields("cowork")["realm"], "cowork_realm")
        self.assertEqual(kc.pool_fields("dms")["admin_client_secret"], "admin_client_secret")

    def test_each_product_binds_the_subject_of_its_own_pool(self):
        issue = Issue(status="issued", identity_subject="biz", cowork_identity_subject="cw")
        self.assertEqual([identity_pools.subject_of(issue, p) for p in ("erp", "dms", "cowork")], ["biz", "biz", "cw"])
        self.assertEqual(identity_pools.subjects_of(issue), [("erp", "biz"), ("cowork", "cw")])
        self.assertTrue(identity_pools.issued(Issue(status="issued", cowork_identity_subject="cw")))
        self.assertFalse(identity_pools.issued(Issue(status="draft", identity_subject="biz")))


class TestClientForTarget(unittest.TestCase):
    def run_for(self, row, product):
        native = MagicMock()
        native.db.get_value.return_value = frappe._dict(row)
        native.conf = {}
        native.throw.side_effect = RuntimeError
        with patch.object(kc, "frappe", native), \
                patch("frappe.utils.password.get_decrypted_password", return_value="secret"):
            client, name = kc.client_for_target("T", product)
        return client, name, native.db.get_value.call_args

    def test_cowork_uses_its_own_realm_fields(self):
        row = {"target_name": "T", "base_url": "http://kc", "cowork_realm": "pearnly-cowork",
               "cowork_admin_client_id": "pearnly-admin"}
        client, name, call = self.run_for(row, "cowork")
        self.assertEqual((client.realm, name), ("pearnly-cowork", "T"))
        self.assertIn("cowork_realm", call.args[2])

    def test_business_products_keep_the_original_fields(self):
        row = {"target_name": "T", "base_url": "http://kc", "realm": "pearnly-business", "admin_client_id": "a"}
        self.assertEqual(self.run_for(row, "erp")[0].realm, "pearnly-business")
        self.assertEqual(self.run_for(row, None)[0].realm, "pearnly-business")

    def test_unconfigured_cowork_pool_is_refused_not_redirected(self):
        row = {"target_name": "T", "base_url": "http://kc", "cowork_realm": None, "cowork_admin_client_id": None}
        with self.assertRaises(RuntimeError):
            self.run_for(row, "cowork")


class TestIssue(unittest.TestCase):
    def setUp(self):
        self.doc = MagicMock()
        self.doc.apps = [SimpleNamespace(app=app, domain=app + ".example.com", domain_prefix=app) for app in ("erp", "cowork")]
        self.doc.account_email, self.doc.account_username = "a@example.com", "boss"
        self.doc.get_password.return_value = "initial"
        self.values = {}
        self.doc.set.side_effect = self.values.__setitem__
        self.clients = {"erp": MagicMock(), "cowork": MagicMock()}
        for product, client in self.clients.items():
            client.find_user_id_by_email.return_value = None
            client.create_user.return_value = product + "-subject"
        for target, value in (("require_cloud_target", lambda name, **kw: name),
                              ("_find_or_create_tenant", lambda name: "TEN-1"),
                              ("now", lambda: "2026-09-26 18:00:00")):
            p = patch.object(issues, target, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(issues.kc, "client_for_target", lambda target, product=None: (self.clients[product], target))
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(issues, "frappe")
        self.native = p.start()
        self.addCleanup(p.stop)

    def test_one_account_per_pool_with_the_same_credentials(self):
        with patch("pearnly_control.infra.is_cloud", return_value=True):
            issues._activate_locked(self.doc, "ops@example.com", target_name="T")
        self.assertEqual(self.values, {"identity_subject": "erp-subject", "cowork_identity_subject": "cowork-subject"})
        for client in self.clients.values():
            client.create_user.assert_called_once_with("a@example.com", username="boss", first_name="boss",
                                                       password="initial")

    def test_existing_account_in_either_pool_blocks_both(self):
        self.clients["cowork"].find_user_id_by_email.return_value = "someone"
        self.native.throw.side_effect = RuntimeError
        with self.assertRaises(RuntimeError):
            issues._activate_locked(self.doc, "ops@example.com", target_name="T")
        self.clients["erp"].create_user.assert_not_called()

    def test_failure_in_the_second_pool_removes_the_first_account(self):
        self.clients["cowork"].create_user.side_effect = RuntimeError("down")
        with self.assertRaises(RuntimeError):
            issues._activate_locked(self.doc, "ops@example.com", target_name="T")
        self.clients["erp"].delete_user.assert_called_once_with("erp-subject")
        self.clients["cowork"].delete_user.assert_not_called()


if __name__ == "__main__":
    unittest.main()
