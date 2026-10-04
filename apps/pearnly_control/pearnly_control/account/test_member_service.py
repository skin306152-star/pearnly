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


LINE_B = "U" + "b" * 32
BUSINESS = "https://auth.example.test/realms/pearnly-business"


class TestLineLinks(unittest.TestCase):
    """LINE 登录登记：只替本租户登记过的身份登记；每个 LINE 在每个池一个专用身份，按池计数释放。"""

    def setUp(self):
        self.native = MagicMock()
        self.native.conf = {"pearnly_billing_only": True, "pearnly_billing_scoped_tokens": True,
                            "pearnly_billing_token": "unit-test-root-only",
                            "pearnly_member_identity_ownership_key": "k" * 48}
        self.headers = {}
        self.native.get_request_header.side_effect = lambda name: self.headers.get(name)
        self.native.db.sql.return_value = [[1]]  # get_lock
        self.owners = {("tenant-a", "owner-subject"), ("tenant-b", "owner-subject")}
        self.members = {("tenant-a", "member-subject")}
        self.links = {}

        def matches(row, filters):
            return all(getattr(row, key, None) == value for key, value in filters.items())

        def exists(doctype, filters=None):
            if doctype == service.LINE_LINK:
                return any(matches(row, filters) for row in self.links.values())
            if isinstance(filters, dict):
                pool = self.owners if doctype == service.TENANT_IDENTITY else self.members
                return any(matches(SimpleNamespace(tenant=t, subject=s), filters) for t, s in pool)
            return False

        def get_value(doctype, name, fields=None, **kwargs):
            if doctype == "Pearnly Tenant":
                return "active"
            if doctype == service.LINE_LINK:
                row = self.links.get(name)
                return SimpleNamespace(**vars(row)) if row else None  # 与 Frappe 一样给副本

        def get_all(doctype, filters=None, fields=None, **kwargs):
            return [row for row in self.links.values() if matches(row, filters or {})]

        def delete(doctype, filters):
            for key in [k for k, row in self.links.items() if matches(row, filters)]:
                del self.links[key]

        def set_value(doctype, name, values, *args, **kwargs):
            for field, value in values.items():
                setattr(self.links[name], field, value)

        def insert(values):
            self.links[values["name"]] = SimpleNamespace(**values)

        self.native.db.exists.side_effect = exists
        self.native.db.get_value.side_effect = get_value
        self.native.get_all.side_effect = get_all
        self.native.db.delete.side_effect = delete
        self.native.db.set_value.side_effect = set_value
        for module in (service, member_api, member_provider):
            p = patch.object(module, "frappe", self.native)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(service, "_insert", side_effect=insert)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(service, "realm_prefix", side_effect=lambda realm: "prefix-of-" + realm)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(service, "MemberProvider")
        self.provider_class = p.start()
        self.addCleanup(p.stop)
        self.provider = self.provider_class.return_value
        self.provider.realm = "pearnly-business"

    def scope(self, product, tenant="tenant-a"):
        self.headers.update({"X-Pearnly-Billing-Product": product,
                             "X-Pearnly-Billing-Token": tenant_token("unit-test-root-only", tenant, product)})

    def link(self, subject, line=LINE, tenant="tenant-a", product="erp", channel=CHANNEL, issuer=""):
        self.scope(product, tenant)
        return service.execute(tenant, "link-line", None, None, subject=subject, line_user_id=line,
                               login_channel=channel, issuer=issuer)

    def unlink(self, subject, tenant="tenant-a", product="erp"):
        self.scope(product, tenant)
        return service.execute(tenant, "unlink-line", None, None, subject=subject, login_channel=CHANNEL)

    def owner_of(self, line):
        return service._digest("line-person-v1", "pearnly-business", ALIAS, line)

    def test_link_puts_the_line_on_its_person_not_on_the_tenant_account(self):
        for product, subject in (("erp", "owner-subject"), ("dms", "member-subject")):
            self.assertEqual(self.link(subject, product=product), {"subject": subject})
            self.provider_class.assert_called_with(product, issuer="")
            self.provider.ensure_line_person.assert_called_with(ALIAS, LINE, self.owner_of(LINE),
                                                                service._old_direct_link)
            # 租户账号自己不再挂 LINE（改造前可能挂着）。
            self.provider.unlink_line.assert_called_with(subject, ALIAS)
        self.assertEqual({row.realm for row in self.links.values()}, {"pearnly-business"})
        self.assertEqual(len(self.links), 2)
        self.provider.release_line_person.assert_not_called()

    def test_site_issuer_picks_the_pool(self):
        # testcowork：Cowork 产品却签到业务池，关联要写进业务池。
        self.link("owner-subject", product="cowork", issuer=BUSINESS)
        self.provider_class.assert_called_with("cowork", issuer=BUSINESS)

    def test_same_line_on_two_tenants_keeps_its_person_until_the_last_unbind(self):
        self.link("owner-subject", tenant="tenant-a")
        self.link("owner-subject", tenant="tenant-b")
        self.unlink("owner-subject", tenant="tenant-a")
        self.provider.release_line_person.assert_not_called()
        self.unlink("owner-subject", tenant="tenant-b")
        self.provider.release_line_person.assert_called_once_with(self.owner_of(LINE))
        self.provider_class.assert_called_with(prefix="prefix-of-pearnly-business")
        self.assertEqual(self.links, {})

    def test_rebinding_another_line_releases_the_old_person(self):
        self.link("owner-subject")
        self.link("owner-subject", line=LINE_B)
        self.provider.release_line_person.assert_called_once_with(self.owner_of(LINE))
        self.assertEqual([row.line_user_id for row in self.links.values()], [LINE_B])
        # 同一个 LINE 再登记一次：幂等，不释放。
        self.link("owner-subject", line=LINE_B)
        self.provider.release_line_person.assert_called_once()

    def test_only_platform_registered_accounts_count_as_old_direct_links(self):
        self.assertTrue(service._old_direct_link("owner-subject"))
        self.assertTrue(service._old_direct_link("member-subject"))
        self.assertFalse(service._old_direct_link("someone-else"))

    def test_unregistered_subject_bad_line_or_channel_rejected(self):
        for subject, line, channel in (("stranger", LINE, CHANNEL), ("owner-subject", "not-a-line-id", CHANNEL),
                                       ("owner-subject", "U" + "A" * 32, CHANNEL), ("owner-subject", LINE, "abc"),
                                       ("owner-subject", LINE, "")):
            with self.assertRaises(member_provider.MemberError):
                self.link(subject, line, channel=channel)
        with self.assertRaises(member_provider.MemberError):
            self.link("member-subject", tenant="tenant-b")
        self.provider.ensure_line_person.assert_not_called()
        self.assertEqual(self.links, {})

    def test_lock_timeout_is_unavailable(self):
        self.native.db.sql.return_value = [[0]]
        with self.assertRaises(member_provider.MemberError) as raised:
            self.link("owner-subject")
        self.assertEqual(raised.exception.code, "unavailable")

    def test_dms_scope_can_link_but_never_create_or_reset(self):
        self.scope("dms")
        for action in ("create", "reset-password"):
            with self.assertRaises(member_provider.MemberError):
                service.execute("tenant-a", action, "x@example.test", "secret", "User1", "a" * 32)
        self.provider.create.assert_not_called()
        self.provider.reset_password.assert_not_called()

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


class FakeKeycloak:
    """够用的内存 Keycloak：用户、联合身份（LINE 关联）。"""

    def __init__(self):
        self.users, self.links, self.calls = {}, {}, []

    def __call__(self, method, path, params=None, json=None, **kwargs):
        self.calls.append((method, path))
        parts = path.strip("/").split("/")

        def reply(status, body=None):
            return SimpleNamespace(status_code=status, json=lambda: body)

        if path == "/users" and method == "GET":
            if "idpAlias" in params:
                return reply(200, [{"id": uid} for uid, links in self.links.items()
                                   if links.get(params["idpAlias"]) == params["idpUserId"]])
            field, value = next((k, v) for k, v in params.items() if k != "exact")
            return reply(200, [u for u in self.users.values() if u.get(field) == value])
        if path == "/users" and method == "POST":
            if any(u["username"] == json["username"] for u in self.users.values()):
                return reply(409)
            uid = "kc-%d" % (len(self.users) + 1)
            self.users[uid] = {"id": uid, **json}
            self.links[uid] = {}
            return reply(201)
        uid = parts[1]
        if uid not in self.users:
            return reply(404)
        if len(parts) == 2 and method == "DELETE":
            del self.users[uid], self.links[uid]
            return reply(204)
        if parts[2:] == ["federated-identity"] and method == "GET":
            return reply(200, [{"identityProvider": a, "userId": l} for a, l in self.links[uid].items()])
        alias = parts[3]
        if method == "POST":
            if alias in self.links[uid]:
                return reply(409)
            self.links[uid][alias] = json["userId"]
            return reply(204)
        if method == "DELETE":
            return reply(204 if self.links[uid].pop(alias, None) else 404)


class TestProviderLinePerson(unittest.TestCase):
    OWNER = "f" * 64

    def setUp(self):
        self.provider = member_provider.MemberProvider.__new__(member_provider.MemberProvider)
        self.kc = FakeKeycloak()
        self.provider._request = self.kc
        self.tenant_account = "kc-tenant"
        self.kc.users[self.tenant_account] = {"id": self.tenant_account, "username": "owner1",
                                              "email": "owner@example.test"}
        self.kc.links[self.tenant_account] = {}

    def person(self):
        return next(u for u in self.kc.users.values() if u["username"].startswith("pearnly-line-"))

    def test_person_is_created_without_password_and_holds_the_line(self):
        subject = self.provider.ensure_line_person("line-1", LINE, self.OWNER, lambda holder: False)
        person = self.person()
        self.assertEqual(subject, person["id"])
        self.assertEqual(person["username"], "pearnly-line-" + "f" * 32)
        self.assertTrue(person["email"].endswith("@line.invalid") and person["emailVerified"])
        self.assertNotIn("credentials", person)
        self.assertEqual(person["attributes"][member_provider.OWNER_ATTRIBUTE], [self.OWNER])
        self.assertEqual(self.kc.links[subject], {"line-1": LINE})
        # 再来一次：不重复建、不重复挂。
        self.kc.calls.clear()
        self.assertEqual(self.provider.ensure_line_person("line-1", LINE, self.OWNER, lambda h: False), subject)
        self.assertNotIn(("POST", "/users"), self.kc.calls)
        self.assertEqual(len(self.kc.users), 2)

    def test_old_direct_link_is_moved_only_when_removable(self):
        self.kc.links[self.tenant_account]["line-1"] = LINE
        with self.assertRaises(member_provider.MemberError) as raised:
            self.provider.ensure_line_person("line-1", LINE, self.OWNER, lambda holder: False)
        self.assertEqual(raised.exception.code, "conflict")
        self.assertEqual(self.kc.links[self.tenant_account], {"line-1": LINE})
        subject = self.provider.ensure_line_person("line-1", LINE, self.OWNER,
                                                   lambda holder: holder == self.tenant_account)
        self.assertEqual(self.kc.links[self.tenant_account], {})
        self.assertEqual(self.kc.links[subject], {"line-1": LINE})

    def test_a_same_named_account_with_another_owner_is_never_adopted(self):
        self.kc.users["kc-x"] = {"id": "kc-x", "username": "pearnly-line-" + "f" * 32,
                                 "email": "pearnly-line-" + "f" * 32 + "@line.invalid",
                                 "attributes": {member_provider.OWNER_ATTRIBUTE: ["someone-else"]}}
        with self.assertRaises(member_provider.MemberError) as raised:
            self.provider.ensure_line_person("line-1", LINE, self.OWNER, lambda h: True)
        self.assertEqual(raised.exception.code, "conflict")
        self.assertEqual(self.kc.links["kc-x"] if "kc-x" in self.kc.links else {}, {})

    def test_release_deletes_the_person_and_is_idempotent(self):
        subject = self.provider.ensure_line_person("line-1", LINE, self.OWNER, lambda h: False)
        self.provider.release_line_person(self.OWNER)
        self.assertNotIn(subject, self.kc.users)
        self.provider.release_line_person(self.OWNER)
        self.assertIn(self.tenant_account, self.kc.users)

    def test_unlink_absent_is_fine(self):
        self.provider.unlink_line(self.tenant_account, "line-1")
        self.provider.unlink_line("kc-missing", "line-1")


class TestPoolPrefix(unittest.TestCase):
    def setUp(self):
        self.native = MagicMock()
        # 生产：控制面配内部地址，站点发来公开地址（2026-10-04 部署后查实，按完整地址比对全部被拒）。
        self.native.conf = {"pearnly_member_identity_issuer": "http://keycloak:8080/realms/pearnly-business",
                            "pearnly_member_identity_cowork_issuer": "http://keycloak:8080/realms/pearnly-cowork"}
        p = patch.object(member_provider, "frappe", self.native)
        p.start()
        self.addCleanup(p.stop)

    def test_issuer_wins_over_product_and_unknown_issuer_is_refused(self):
        self.assertEqual(member_provider.pool_prefix("cowork"), "pearnly_member_identity_cowork")
        self.assertEqual(member_provider.pool_prefix("cowork", BUSINESS + "/"), "pearnly_member_identity")
        self.assertEqual(member_provider.pool_prefix("erp", "https://auth.example.test/realms/pearnly-cowork"),
                         "pearnly_member_identity_cowork")
        for unknown in ("https://auth.example.test/realms/master", "https://auth.example.test/", "garbage"):
            with self.assertRaises(member_provider.MemberError):
                member_provider.pool_prefix("erp", unknown)
        self.assertEqual(member_provider.realm_prefix("pearnly-cowork"), "pearnly_member_identity_cowork")
        with self.assertRaises(member_provider.MemberError):
            member_provider.realm_prefix("master")


if __name__ == "__main__":
    unittest.main()
