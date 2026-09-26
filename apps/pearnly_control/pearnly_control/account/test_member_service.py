"""Zero-IO account boundary tests. HTTP and Frappe persistence are replaced."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pearnly_control.account import member_api, member_provider, member_service as service
from pearnly_control.billing_auth import tenant_token


class TestMembers(unittest.TestCase):
    def setUp(self):
        self.native = MagicMock()
        self.native.conf = {
            "pearnly_billing_only": True, "pearnly_billing_scoped_tokens": True,
            "pearnly_billing_token": "unit-test-root-only",
            "pearnly_member_identity_ownership_key": "x" * 48,
        }
        self.headers = {"X-Pearnly-Billing-Product": "erp",
            "X-Pearnly-Billing-Token": tenant_token("unit-test-root-only", "tenant-a", "erp")}
        self.native.get_request_header.side_effect = self.headers.get
        self.native.db.get_value.return_value = "active"
        for module in (service, member_api, member_provider):
            p = patch.object(module, "frappe", self.native)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(service, "MemberProvider")
        self.provider = p.start().return_value
        self.addCleanup(p.stop)
        self.provider.create.return_value = "member-subject"
        self.ledger = {}

    def install_db(self, member=None):
        self.member = member

        def value(doctype, key, fields=None, **kwargs):
            if doctype == "Pearnly Tenant":
                return "active"
            if doctype == service.IDENTITY:
                return getattr(self.member, fields) if self.member and isinstance(fields, str) else self.member
            if doctype == service.OPERATION:
                row = self.ledger.get(key) if isinstance(key, str) else next((row for row in self.ledger.values()
                    if row.status == key.get("status") and row.member == key.get("member")), None)
                return getattr(row, fields) if row and isinstance(fields, str) else row

        def insert(values):
            row = SimpleNamespace(**values)
            if values["doctype"] == service.OPERATION:
                self.ledger[row.name] = row
            else:
                self.member = row
            return row

        def set_value(doctype, name, field, value, **kwargs):
            setattr(self.member if doctype == service.IDENTITY else self.ledger[name], field, value)

        self.native.db.get_value.side_effect = value
        self.native.db.set_value.side_effect = set_value
        self.native.get_all.side_effect = lambda doctype, filters, **kwargs: [
            key for key, row in self.ledger.items() if all(getattr(row, field) == val for field, val in filters.items())]
        p = patch.object(service, "_insert", side_effect=insert)
        p.start()
        self.addCleanup(p.stop)

    def member_row(self):
        return SimpleNamespace(tenant="tenant-a", product="erp", email="employee@example.test",
            username="Employee42", subject="member-subject", create_request_hash="wrong-test-fingerprint")

    def test_scoped_active_erp_auth(self):
        self.assertEqual(service.authorize("tenant-a"), ("tenant-a", "erp"))
        self.native.db.get_value.assert_called_with("Pearnly Tenant", "tenant-a", "status", for_update=True)

    def test_wrong_scope_cross_tenant_root_secret_and_inactive_denied(self):
        cases = [
            ("tenant-b", "erp", self.headers["X-Pearnly-Billing-Token"]),
            ("tenant-a", "dms", tenant_token("unit-test-root-only", "tenant-a", "dms")),
            ("tenant-a", "erp", "unit-test-root-only"),
        ]
        for tenant, product, token in cases:
            self.headers.update({"X-Pearnly-Billing-Product": product, "X-Pearnly-Billing-Token": token})
            with self.assertRaises(member_provider.MemberError):
                service.authorize(tenant)
        self.headers.update({"X-Pearnly-Billing-Product": "erp",
            "X-Pearnly-Billing-Token": tenant_token("unit-test-root-only", "tenant-a", "erp")})
        self.native.db.get_value.return_value = "suspended"
        with self.assertRaises(member_provider.MemberError):
            service.authorize("tenant-a")

    def test_non_private_or_unscoped_rejected(self):
        for field in ("pearnly_billing_only", "pearnly_billing_scoped_tokens"):
            self.native.conf[field] = False
            with self.assertRaises(member_provider.MemberError):
                service.authorize("tenant-a")
            self.native.conf[field] = True

    def test_create_recovery_preserves_username_and_never_resets_password(self):
        self.install_db()
        result = service.create("tenant-a", "employee@example.test", "Employee42", "secret-a")
        self.assertEqual(result["subject"], "member-subject")
        self.assertEqual(self.provider.create.call_args.kwargs["username"], "Employee42")
        replay = service.create("tenant-a", "employee@example.test", "Employee42", "secret-a")
        self.assertEqual(replay, result)
        self.provider.create.assert_called_once()
        self.provider.reset_password.assert_not_called()

    def test_create_timeout_cannot_silently_change_password(self):
        self.install_db()
        self.provider.create.side_effect = member_provider.MemberError("unavailable")
        with self.assertRaises(member_provider.MemberError):
            service.create("tenant-a", "employee@example.test", "Employee42", "secret-a")
        self.assertFalse(self.member.subject)
        self.native.db.commit.assert_called()
        with self.assertRaisesRegex(member_provider.MemberError, "conflict"):
            service.create("tenant-a", "employee@example.test", "Employee42", "secret-b")
        self.provider.create.side_effect = None
        result = service.create("tenant-a", "employee@example.test", "Employee42", "secret-a")
        self.assertEqual(result["subject"], "member-subject")

    def test_tampered_mapping_and_username_conflict_before_external_write(self):
        self.install_db(self.member_row())
        with self.assertRaises(member_provider.MemberError):
            service.create("tenant-a", "employee@example.test", "SomeoneElse", "secret")
        self.member.tenant = "tenant-b"
        with self.assertRaises(member_provider.MemberError):
            service.create("tenant-a", "employee@example.test", "Employee42", "secret")
        self.provider.create.assert_not_called()

    def test_existing_other_owner_and_forged_subject_rejected(self):
        row = {"id": "owner-subject", "email": "employee@example.test", "username": "Employee42"}
        with self.assertRaises(member_provider.MemberError):
            member_provider.MemberProvider.verify(row, email=row["email"], username=row["username"], owner="signed")
        row["attributes"] = {"pearnly_member_owner": ["signed"]}
        with self.assertRaises(member_provider.MemberError):
            member_provider.MemberProvider.verify(row, email=row["email"], username=row["username"], owner="signed", subject="forged")
        self.assertEqual(member_provider.MemberProvider.verify(row,
            email=row["email"], username=row["username"], owner="signed"), "owner-subject")

    def test_reset_intent_before_external_write_and_duplicate_noop(self):
        self.install_db(self.member_row())
        self.provider.reset_password.side_effect = lambda *args, **kwargs: self.native.db.commit.assert_called()
        arguments = ("tenant-a", "employee@example.test", "secret", "a" * 32)
        service.reset_password(*arguments)
        service.reset_password(*arguments)
        self.provider.reset_password.assert_called_once()
        self.assertEqual(next(iter(self.ledger.values())).status, "done")
        self.assertNotIn("secret", str(vars(next(iter(self.ledger.values())))))

    def test_lost_reset_response_blocks_new_password_and_recovers_same(self):
        self.install_db(self.member_row())
        self.provider.reset_password.side_effect = member_provider.MemberError("unavailable")
        with self.assertRaises(member_provider.MemberError):
            service.reset_password("tenant-a", "employee@example.test", "secret", "a" * 32)
        with self.assertRaisesRegex(member_provider.MemberError, "reset_pending"):
            service.reset_password("tenant-a", "employee@example.test", "new-secret", "b" * 32)
        self.provider.reset_password.side_effect = None
        service.reset_password("tenant-a", "employee@example.test", "secret", "c" * 32)
        self.assertTrue(all(row.status == "done" for row in self.ledger.values()))
        self.provider.reset_password.assert_called_with("member-subject", "secret",
            email="employee@example.test", username="Employee42",
            owner=service._digest("member-owner-v1", "tenant-a", "erp", "employee@example.test", "employee42"))

    def test_request_id_cannot_be_reused_with_other_password(self):
        self.install_db(self.member_row())
        service.reset_password("tenant-a", "employee@example.test", "secret", "a" * 32)
        with self.assertRaisesRegex(member_provider.MemberError, "conflict"):
            service.reset_password("tenant-a", "employee@example.test", "different", "a" * 32)

    def test_unknown_email_cannot_reset_owner_identity(self):
        self.install_db()
        with self.assertRaisesRegex(member_provider.MemberError, "not_permitted"):
            service.reset_password("tenant-a", "owner@example.test", "secret", "a" * 32)
        self.provider.reset_password.assert_not_called()

    def test_rpc_does_not_expose_exception_or_credentials(self):
        with patch.object(service, "execute", side_effect=RuntimeError("PRIVATE password details")):
            self.assertEqual(member_api.execute(password="secret"), {"ok": False, "error": "unavailable"})
        self.native.log_error.assert_not_called()
        self.native.db.rollback.assert_called_once()


LINE = "U" + "a" * 32


CHANNEL = "2010411313"
ALIAS = "line-2010411313"


class TestLineLinks(unittest.TestCase):
    """LINE 登录关联：只替本租户登记过的身份写 Keycloak；入口按 LINE Login 频道共用，按站点记账。"""

    def setUp(self):
        self.native = MagicMock()
        self.native.conf = {"pearnly_billing_only": True, "pearnly_billing_scoped_tokens": True,
                            "pearnly_billing_token": "unit-test-root-only"}
        self.headers = {}
        self.native.get_request_header.side_effect = lambda name: self.headers.get(name)
        self.native.db.get_value.return_value = "active"
        self.owners = {("tenant-a", "owner-subject"), ("tenant-b", "owner-subject")}
        self.members = {("tenant-a", "member-subject")}
        self.links = {}

        def exists(doctype, filters=None):
            if doctype == service.LINE_LINK:
                return filters in self.links
            if isinstance(filters, dict):
                pool = self.owners if doctype == service.TENANT_IDENTITY else self.members
                return (filters["tenant"], filters["subject"]) in pool
            return False

        def matches(row, filters):
            return all(getattr(row, key) == value for key, value in filters.items())

        def get_all(doctype, filters=None, fields=None, **kwargs):
            return [row for row in self.links.values() if matches(row, filters or {})]

        def delete(doctype, filters):
            for key in [k for k, row in self.links.items() if matches(row, filters)]:
                del self.links[key]

        def insert(values):
            self.links[values["name"]] = SimpleNamespace(**values)

        self.native.db.exists.side_effect = exists
        self.native.get_all.side_effect = get_all
        self.native.db.delete.side_effect = delete
        self.native.db.set_value.side_effect = lambda doctype, name, field, value: setattr(
            self.links[name], field, value)
        for module in (service, member_api, member_provider):
            p = patch.object(module, "frappe", self.native)
            p.start()
            self.addCleanup(p.stop)
        for target, value in (("MemberProvider", None), ("_insert", insert)):
            p = patch.object(service, target, side_effect=value) if value else patch.object(service, target)
            mocked = p.start()
            self.addCleanup(p.stop)
            if target == "MemberProvider":
                self.provider_class, self.provider = mocked, mocked.return_value

    def scope(self, product, tenant="tenant-a"):
        self.headers.update({"X-Pearnly-Billing-Product": product,
                             "X-Pearnly-Billing-Token": tenant_token("unit-test-root-only", tenant, product)})

    def link(self, subject, line=LINE, tenant="tenant-a", product="erp", channel=CHANNEL):
        self.scope(product, tenant)
        return service.execute(tenant, "link-line", None, None, subject=subject, line_user_id=line,
                               login_channel=channel)

    def unlink(self, subject, tenant="tenant-a", product="erp"):
        self.scope(product, tenant)
        return service.execute(tenant, "unlink-line", None, None, subject=subject, login_channel=CHANNEL)

    def test_owner_and_member_link_to_the_shared_channel_entry(self):
        for product, subject in (("erp", "owner-subject"), ("dms", "member-subject")):
            self.assertEqual(self.link(subject, product=product), {"subject": subject})
            self.provider_class.assert_called_with(product)
            self.provider.link_line.assert_called_with(subject, ALIAS, LINE)
        self.assertEqual(len(self.links), 2)

    def test_unregistered_subject_bad_line_or_channel_rejected(self):
        for subject, line, channel in (("stranger", LINE, CHANNEL), ("owner-subject", "not-a-line-id", CHANNEL),
                                       ("owner-subject", "U" + "A" * 32, CHANNEL), ("owner-subject", LINE, "abc"),
                                       ("owner-subject", LINE, "")):
            with self.assertRaises(member_provider.MemberError):
                self.link(subject, line, channel=channel)
        with self.assertRaises(member_provider.MemberError):
            self.link("member-subject", tenant="tenant-b")
        self.provider.link_line.assert_not_called()
        self.assertEqual(self.links, {})

    def test_dms_scope_can_link_but_never_create_or_reset(self):
        self.scope("dms")
        for action in ("create", "reset-password"):
            with self.assertRaises(member_provider.MemberError):
                service.execute("tenant-a", action, "x@example.test", "secret", "User1", "a" * 32)
        self.provider.create.assert_not_called()
        self.provider.reset_password.assert_not_called()

    def test_unlinking_one_site_keeps_the_link_while_another_site_still_binds(self):
        self.link("owner-subject", tenant="tenant-a")
        self.link("owner-subject", tenant="tenant-b")
        self.unlink("owner-subject", tenant="tenant-a")
        self.provider.unlink_line.assert_not_called()
        self.provider.link_line.assert_called_with("owner-subject", ALIAS, LINE)
        self.unlink("owner-subject", tenant="tenant-b")
        self.provider.unlink_line.assert_called_once_with("owner-subject", ALIAS)
        self.assertEqual(self.links, {})

    def test_line_moved_between_own_identities_is_relinked_but_not_from_elsewhere(self):
        conflict = member_provider.MemberError("conflict")
        self.link("member-subject")
        self.provider.link_line.side_effect = [conflict, None]
        self.provider.line_holders.return_value = ["member-subject", "owner-subject"]
        self.link("owner-subject")
        self.provider.unlink_line.assert_called_once_with("member-subject", ALIAS)
        self.assertEqual({row.subject for row in self.links.values()}, {"owner-subject"})
        # 持有人不是本租户的，或它在别的站点也用着这个 LINE：不抢。
        for holders in (["other-tenant-subject"], ["owner-subject"]):
            self.provider.link_line.side_effect = [conflict]
            self.provider.line_holders.return_value = holders
            with self.assertRaises(member_provider.MemberError):
                self.link("member-subject" if holders == ["owner-subject"] else "owner-subject",
                          tenant="tenant-a", product="dms")
        self.provider.unlink_line.assert_called_once()

    def test_unlink_is_scoped_too(self):
        self.assertEqual(self.unlink("member-subject"), {"subject": "member-subject"})
        self.provider.unlink_line.assert_called_once_with("member-subject", ALIAS)
        with self.assertRaises(member_provider.MemberError):
            self.unlink("stranger")

    def test_register_owner_is_idempotent_and_validated(self):
        with patch.object(service, "_insert") as insert:
            self.native.db.exists.side_effect = [False, True]
            service.register_owner("tenant-a", "owner-subject")
            service.register_owner("tenant-a", "owner-subject")
            insert.assert_called_once()
            self.assertEqual(insert.call_args[0][0]["role"], "owner")
            with self.assertRaises(member_provider.MemberError):
                service.register_owner("tenant-a", "../admin")


class TestProviderLineLinks(unittest.TestCase):
    def setUp(self):
        self.provider = member_provider.MemberProvider.__new__(member_provider.MemberProvider)
        self.calls = []
        self.responses = {}

        def request(method, path, **kwargs):
            self.calls.append((method, path, kwargs.get("json")))
            return SimpleNamespace(**self.responses.get(method, {"status_code": 204}),
                                   json=lambda: self.responses.get(method, {}).get("body"))

        self.provider._request = request

    def test_same_link_is_a_noop_and_other_link_is_replaced(self):
        self.provider.line_holders = lambda alias, line: []
        self.responses["GET"] = {"status_code": 200, "body": [
            {"identityProvider": "line-erp-t", "userId": LINE}]}
        self.provider.link_line("s-1", "line-erp-t", LINE)
        self.assertEqual([c[0] for c in self.calls], ["GET"])
        self.calls.clear()
        self.provider.link_line("s-1", "line-erp-t", "U" + "b" * 32)
        self.assertEqual([c[0] for c in self.calls], ["GET", "DELETE", "POST"])
        self.assertEqual(self.calls[-1][2]["userId"], "U" + "b" * 32)

    def test_line_held_by_another_account_is_a_conflict_before_any_write(self):
        # Keycloak itself accepts a duplicate link (verified on 26.7.4), so the check is ours.
        self.responses["GET"] = {"status_code": 200, "body": [{"id": "s-2"}]}
        with self.assertRaises(member_provider.MemberError) as raised:
            self.provider.link_line("s-1", "line-erp-t", LINE)
        self.assertEqual(raised.exception.code, "conflict")
        self.assertEqual([(c[0], c[1]) for c in self.calls], [("GET", "/users")])

    def test_missing_user_and_unlink_absent(self):
        self.provider.line_holders = lambda alias, line: []
        self.responses["GET"] = {"status_code": 404}
        with self.assertRaises(member_provider.MemberError):
            self.provider.link_line("s-1", "line-erp-t", LINE)
        self.responses["DELETE"] = {"status_code": 404}
        self.provider.unlink_line("s-1", "line-erp-t")


if __name__ == "__main__":
    unittest.main()
