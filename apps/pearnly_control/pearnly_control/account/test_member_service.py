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


if __name__ == "__main__":
    unittest.main()
