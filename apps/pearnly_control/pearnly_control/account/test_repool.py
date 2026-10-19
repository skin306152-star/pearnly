"""Zero-IO tests: an old Cowork site (opened on the business pool before the 2026-09-26 split) moves to the Cowork pool
and back (`account.repool`). Frappe, the identity service and the billing service are replaced."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pearnly_control.account import repool

SITE = "testcowork.pearnly.com"
BUSINESS = "https://auth.pearnly.com/realms/pearnly-business"
COWORK = "https://auth.pearnly.com/realms/pearnly-cowork"


class Doc(SimpleNamespace):
    def get(self, key, default=None):
        return getattr(self, key, default)

    def set(self, key, value):
        setattr(self, key, value)


class Request(Doc):
    def append(self, _table, row):
        self.steps.append(Doc(**row))

    def save(self, **_kwargs):
        self.saved = True


class Client:
    def __init__(self, users=None):
        self.users = dict(users or {})
        self.created, self.deleted = [], []

    def find_user_id_by_email(self, email):
        return next((uid for uid, user in self.users.items() if user["email"] == email), None)

    def create_user(self, email, username=None, first_name="", password=None):
        uid = "kc-%d" % (len(self.users) + 1)
        self.users[uid] = {"email": email, "username": username, "password": password}
        self.created.append(uid)
        return uid

    def delete_user(self, subject):
        self.users.pop(subject, None)
        self.deleted.append(subject)
        return True


def step(key, action, status="done", payload=None):
    return Doc(step_key=key, deployment="DEP-1", action=action, status=status,
               payload=json.dumps(payload or {}), output="old", finished_at="t")


class TestRepool(unittest.TestCase):
    def setUp(self):
        self.issue = Doc(name="ISS-1", target="cloud", tenant="T-1", tenant_name="Test Cowork",
                         account_email="owner@example.test", account_username="testcowork",
                         identity_subject="biz-owner", cowork_identity_subject=None,
                         get_password=lambda field, raise_exception=False: "initial-secret")
        self.dep = Doc(name="DEP-1", issue="ISS-1", product="cowork", status="active", site_name=SITE,
                       bench_group="cowork", tenant="T-1", oidc_client_id=SITE, reload=lambda: None)
        self.req = Request(name="REQ-1", status="done", error="", finished_at="t", steps=[
            step("DEP-1:new_site", "new_site"), step("DEP-1:set_oidc", "set_oidc"),
            step("DEP-1:bind_user", "bind_user"), step("DEP-1:mark_active", "mark_active")])
        self.pools = {"erp": Client({"biz-owner": {"email": "owner@example.test"}}), "cowork": Client()}
        self.native = MagicMock()
        docs = {("Pearnly Deployment", "DEP-1"): self.dep, ("Pearnly Issue", "ISS-1"): self.issue,
                ("Pearnly Provisioning Request", "REQ-1"): self.req}
        self.native.get_doc.side_effect = lambda doctype, name, **kwargs: docs[(doctype, name)]
        self.tenant_status = "active"

        def get_value(doctype, filters, field=None, **kwargs):
            if doctype == "Pearnly Tenant":
                return self.tenant_status
            if doctype == "Pearnly Provisioning Request":
                return "REQ-1"
            raise AssertionError(doctype)

        self.native.db.get_value.side_effect = get_value
        self.native.db.set_value.side_effect = lambda doctype, name, field, value: setattr(self.issue, field, value)
        self.billing = MagicMock()
        self.billing.move_members.return_value = {"realm": "pearnly-cowork", "line_links_released": 1, "members": [
            {"email": "staff@example.test", "username": "Staff1", "subject": "cw-staff", "action": "created"},
            {"email": "late@example.test", "username": "Late1", "subject": None, "action": "incomplete"}]}
        self.oidc = MagicMock()
        patches = [
            patch.object(repool, "frappe", self.native),
            patch.object(repool, "billing_remote", self.billing),
            patch.object(repool.infra, "is_cloud", lambda target: target == "cloud"),
            patch.object(repool.infra, "deployment_base_url", lambda dep: "https://" + dep.site_name),
            patch.object(repool.provisioning, "_require_operator", lambda: "ops@example.test"),
            patch.object(repool.provisioning, "_ensure_oidc_client", self.oidc),
            patch.object(repool.provisioning, "_issuer_url",
                         lambda target, product: COWORK if product == "cowork" else BUSINESS),
            patch.object(repool.provisioning, "oidc_secret_of", lambda name: "client-secret"),
            patch.object(repool.provisioning, "target_credentials", lambda target, product: {"native_account": True}),
            patch("pearnly_core.keycloak_admin.client_for_target",
                  lambda target, product: (self.pools[product], target)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def steps(self):
        return {s.step_key: (s.action, s.status, json.loads(s.payload)) for s in self.req.steps}

    def test_moves_an_old_site_to_the_cowork_pool(self):
        result = repool.move("DEP-1", "cowork", confirm=SITE)
        owner = self.pools["cowork"].created[0]
        self.assertEqual(self.pools["cowork"].users[owner],
                         {"email": "owner@example.test", "username": "testcowork", "password": "initial-secret"})
        self.assertEqual(self.issue.cowork_identity_subject, owner)
        self.oidc.assert_called_once_with(self.dep, "cloud", "cowork")
        self.billing.move_members.assert_called_once_with("T-1", "cowork", COWORK)
        self.billing.provision_scope.assert_called_once_with(
            "T-1", "cowork", "Test Cowork", owner_subject=owner, site=SITE, issuer=COWORK,
            owner_username="testcowork", owner_email="owner@example.test")
        steps = self.steps()
        self.assertEqual(steps["DEP-1:set_oidc"][:2], ("set_oidc", "pending"))
        self.assertEqual(steps["DEP-1:set_oidc"][2]["issuer"], COWORK)
        self.assertEqual(steps["DEP-1:set_oidc"][2]["client_secret"], "client-secret")
        self.assertEqual(steps["DEP-1:set_oidc"][2]["moved_from"], "business")
        self.assertEqual(steps["DEP-1:bind_user"][2]["subject"], owner)
        self.assertEqual(steps["DEP-1:new_site"][1], "done")  # 其余步骤不动
        member = [key for key in steps if ":bind_member:" in key]
        self.assertEqual(len(member), 1)  # 没建出来的成员不排
        self.assertEqual(steps[member[0]], ("bind_user", "pending", {
            "site": SITE, "group": "cowork", "email": "staff@example.test", "full_name": "Staff1",
            "subject": "cw-staff", "deployment": "DEP-1", "member": True}))
        self.assertEqual([s.step_key for s in self.req.steps][-1], member[0])  # 追加在末尾，按顺序跑
        self.assertEqual(self.req.status, "pending")
        self.assertEqual(result["queued"], ["DEP-1:set_oidc", "DEP-1:bind_user", member[0]])
        self.assertIsNone(result["removed_cowork_owner"])

    def test_rerun_reuses_the_recorded_owner_and_the_same_steps(self):
        repool.move("DEP-1", "cowork", confirm=SITE)
        owner = self.issue.cowork_identity_subject
        repool.move("DEP-1", "cowork", confirm=SITE)
        self.assertEqual(self.pools["cowork"].created, [owner])
        self.assertEqual(len(self.req.steps), 5)

    def test_an_unrecorded_account_with_the_owner_email_stops_everything(self):
        self.pools["cowork"].users["someone"] = {"email": "owner@example.test"}
        with self.assertRaises(repool.RepoolError):
            repool.move("DEP-1", "cowork", confirm=SITE)
        self.oidc.assert_not_called()
        self.billing.move_members.assert_not_called()
        self.billing.provision_scope.assert_not_called()
        self.assertEqual({s.status for s in self.req.steps}, {"done"})

    def test_a_site_opened_on_the_cowork_pool_is_never_moved_or_rolled_back(self):
        # 拆池后开的 ERP + Cowork 单：两个池各有原生的号，Cowork 站点本来就签在 Cowork 池
        self.issue.cowork_identity_subject = "native-cowork"
        self.pools["cowork"].users["native-cowork"] = {"email": "owner@example.test"}
        for pool in ("cowork", "business"):
            with self.assertRaises(repool.RepoolError):
                repool.move("DEP-1", pool, confirm=SITE)
        self.assertEqual(self.pools["cowork"].deleted, [])
        self.assertEqual(self.issue.cowork_identity_subject, "native-cowork")
        self.oidc.assert_not_called()
        self.billing.move_members.assert_not_called()
        report = repool.plan("DEP-1", "business")
        self.assertFalse(report["allowed"])

    def test_owner_without_a_password(self):
        repool.move("DEP-1", "cowork", confirm=SITE, owner_password="none")
        owner = self.pools["cowork"].created[0]
        self.assertIsNone(self.pools["cowork"].users[owner]["password"])
        with self.assertRaises(repool.RepoolError):
            self.issue.cowork_identity_subject = None
            self.pools["cowork"].users.clear()
            repool.move("DEP-1", "cowork", confirm=SITE, owner_password="typed-by-someone")

    def test_rollback_returns_to_the_business_pool_and_removes_the_cowork_owner(self):
        repool.move("DEP-1", "cowork", confirm=SITE)
        cowork_owner = self.issue.cowork_identity_subject
        self.billing.reset_mock()
        result = repool.move("DEP-1", "business", confirm=SITE)
        self.assertEqual(self.steps()["DEP-1:set_oidc"][2]["issuer"], BUSINESS)
        self.assertEqual(self.steps()["DEP-1:bind_user"][2]["subject"], "biz-owner")
        self.billing.move_members.assert_called_once_with("T-1", "cowork", BUSINESS)
        self.assertEqual(self.billing.provision_scope.call_args.kwargs["owner_subject"], "biz-owner")
        self.assertEqual(self.pools["cowork"].deleted, [cowork_owner])
        self.assertIsNone(self.issue.cowork_identity_subject)
        self.assertEqual(result["removed_cowork_owner"], cowork_owner)
        self.assertEqual(self.pools["erp"].users, {"biz-owner": {"email": "owner@example.test"}})

    def test_guards(self):
        with self.assertRaises(repool.RepoolError):
            repool.move("DEP-1", "cowork", confirm="other.pearnly.com")
        with self.assertRaises(repool.RepoolError):
            repool.move("DEP-1", "master", confirm=SITE)
        self.req.steps[1].status = "running"
        with self.assertRaises(repool.RepoolError):
            repool.move("DEP-1", "cowork", confirm=SITE)
        self.req.steps[1].status = "done"
        self.dep.product = "erp"
        with self.assertRaises(repool.RepoolError):
            repool.move("DEP-1", "cowork", confirm=SITE)
        self.dep.product = "cowork"
        self.tenant_status = "deleting"
        with self.assertRaises(repool.RepoolError):
            repool.plan("DEP-1")
        self.oidc.assert_not_called()

    def test_billing_refusal_stops_before_any_site_step(self):
        self.billing.move_members.return_value = {"error": "conflict"}
        with self.assertRaises(repool.RepoolError):
            repool.move("DEP-1", "cowork", confirm=SITE)
        self.billing.provision_scope.assert_not_called()
        self.assertEqual({s.status for s in self.req.steps}, {"done"})

    def test_plan_reads_only(self):
        self.billing.move_members.return_value = {"realm": "pearnly-cowork", "line_links_released": 1,
                                                  "members": [{"email": "staff@example.test", "action": "create"}]}
        report = repool.plan("DEP-1")
        self.assertEqual((report["current_pool"], report["target_pool"], report["already_there"]),
                         ("business", "cowork", False))
        self.assertEqual(report["owner"]["in_target_pool"], None)
        self.assertFalse(report["owner"]["blocked"])
        self.assertTrue(report["owner"]["initial_password_kept"])
        self.billing.move_members.assert_called_once_with("T-1", "cowork", COWORK, dry_run=True)
        self.assertEqual(report["steps"], ["set_oidc", "bind_user", "bind_member:staff@example.test"])
        self.assertTrue(report["allowed"])
        self.assertFalse(repool.plan("DEP-1", "business")["allowed"])  # 还没搬过，不能回退
        self.billing.provision_scope.assert_not_called()
        self.oidc.assert_not_called()
        self.assertEqual(self.pools["cowork"].created, [])
        self.assertEqual({s.status for s in self.req.steps}, {"done"})


if __name__ == "__main__":
    unittest.main()
