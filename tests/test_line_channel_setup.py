"""LINE channel credentials from the issue to the tenant site (2026-09-29, ISS-2026-0294 / PR-2026-0296).

Run with Frappe installed. No Site, database, credentials, network or tenant writes:
documents are plain objects and every Frappe/database collaborator is replaced.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from pearnly_control import provisioning
from pearnly_control.pearnly_control.doctype.pearnly_issue.pearnly_issue import PearnlyIssue
from pearnly_control.pearnly_control.doctype.pearnly_line_channel.pearnly_line_channel import PearnlyLINEChannel

HALF = "Row erp: a LINE channel needs both the channel secret and the access token"


class Rejected(Exception):
    pass


def _row(secret="", token="", **extra):
    """开户单产品行：密码字段未存时是明文，存过是一串 *（真值在 __Auth，由 get_password 取）。"""
    stored = {"line_channel_secret": secret, "line_channel_access_token": token}
    row = SimpleNamespace(name="ROW", app="erp", line_channel_id=" 2011793822 ", line_liff_id="",
                          line_channel_secret=secret and "*" * len(secret),
                          line_channel_access_token=token and "*" * len(token), **extra)
    row.get_password = lambda field, raise_exception=True: stored[field]
    return row


class Base(unittest.TestCase):
    def setUp(self):
        self.throw = Mock(side_effect=Rejected)
        for attribute, value in {"throw": self.throw, "_": lambda x: x, "db": Mock(),
                                 "session": SimpleNamespace(user="Administrator"), "only_for": Mock()}.items():
            p = patch.object(frappe, attribute, value, create=True)
            p.start(); self.addCleanup(p.stop)
        for module in ("pearnly_control.provisioning",
                       "pearnly_control.pearnly_control.doctype.pearnly_issue.pearnly_issue",
                       "pearnly_control.pearnly_control.doctype.pearnly_line_channel.pearnly_line_channel"):
            p = patch(module + "._", lambda x: x); p.start(); self.addCleanup(p.stop)

    def rejected_with(self, message):
        self.assertEqual(self.throw.call_args[0][0], message)


class IssueRow(Base):
    """保存开户单时就拦住只填一半的 LINE 凭据（发号之前、生成开通步骤之前）。"""

    def check(self, status, secret, token):
        row = SimpleNamespace(app="erp", line_channel_id=" 2011793822 ",
                              line_channel_secret=secret, line_channel_access_token=token)
        PearnlyIssue._validate_line_credentials(SimpleNamespace(status=status), row)
        return row

    def test_draft_with_only_the_secret_is_rejected_with_the_row(self):
        with self.assertRaises(Rejected):
            self.check("draft", "0123456789abcdef0123456789abcdef", "")
        self.rejected_with(HALF)
        with self.assertRaises(Rejected):
            self.check("draft", "", "token")

    def test_complete_or_empty_rows_pass_and_pasted_whitespace_is_removed(self):
        row = self.check("draft", " 0123456789abcdef0123456789abcdef", "token \n")
        self.assertEqual((row.line_channel_id, row.line_channel_secret, row.line_channel_access_token),
                         ("2011793822", "0123456789abcdef0123456789abcdef", "token"))
        self.check("draft", "", "")
        # 存过的是占位，不是空格
        self.assertEqual(self.check("draft", "*" * 33, "*" * 172).line_channel_secret, "*" * 33)

    def test_issued_history_is_not_blocked(self):
        self.check("issued", "*" * 32, "")
        self.throw.assert_not_called()


class Channel(Base):
    """手建的渠道：凭据齐了就生效，不再停在默认的 pending。"""

    def validate(self, status, secret, token, flags=None):
        doc = SimpleNamespace(deployment="DEP-1", status=status, channel_id=" 2011793822", liff_id="",
                              channel_secret=secret, channel_access_token=token, flags=flags or {})
        frappe.db.get_value.return_value = frappe._dict(product="erp", site_name="mrerp.pearnly.com", port=0,
                                                        url="https://mrerp.pearnly.com")
        with patch("pearnly_control.infra.deployment_base_url", return_value="https://mrerp.pearnly.com"):
            PearnlyLINEChannel.validate(doc)
        return doc

    def test_complete_credentials_make_a_pending_channel_active(self):
        doc = self.validate("pending", " 0123456789abcdef0123456789abcdef", "token")
        self.assertEqual((doc.status, doc.channel_secret, doc.channel_id),
                         ("active", "0123456789abcdef0123456789abcdef", "2011793822"))
        # 之后再改别的字段：库里已存的占位也算齐
        self.assertEqual(self.validate("pending", "*" * 32, "*" * 172).status, "active")

    def test_incomplete_or_suspended_channels_keep_their_status(self):
        self.assertEqual(self.validate("pending", "secret", "").status, "pending")
        self.assertEqual(self.validate("pending", "", "").status, "pending")
        self.assertEqual(self.validate("suspended", "secret", "token").status, "suspended")

    def test_channel_created_from_the_issue_stays_active_and_is_not_queued_on_save(self):
        doc = self.validate("active", "secret", "token", flags={"pearnly_from_issue": True})
        self.assertEqual(doc.status, "active")
        doc.flags = frappe._dict(pearnly_from_issue=True)
        with patch.object(provisioning, "queue_cloud_line_config") as queue:
            PearnlyLINEChannel.on_update(doc)
        queue.assert_not_called()


class Plan(Base):
    def test_half_filled_row_is_rejected_before_any_request_or_deployment_exists(self):
        issue = SimpleNamespace(name="ISS-1", target="cloud", status="issued",
                                apps=[_row(secret="0123456789abcdef0123456789abcdef")])
        frappe.db.get_value.return_value = None
        with patch.object(frappe, "get_doc", return_value=issue) as get_doc, \
             patch("pearnly_control.onboarding.targets.require_cloud_target", return_value="cloud"), \
             patch.object(provisioning, "_ensure_deployment") as deployment:
            with self.assertRaises(Rejected):
                provisioning.build_plan("ISS-1")
        self.rejected_with(HALF)
        deployment.assert_not_called()
        self.assertEqual(get_doc.call_count, 1, "no provisioning request is created")

    def test_credentials_are_read_trimmed_from_the_issue_row(self):
        self.assertEqual(provisioning._line_credentials_of(_row(secret=" secret", token="token ")), ("secret", "token"))
        self.assertEqual(provisioning._line_credentials_of(_row()), ("", ""))

    def test_rerun_plan_adds_the_missing_channel_and_requeues_its_skipped_line_step(self):
        """PR-2026-0296：首次规划半路失败，再点一次时 set_line 被记成 skipped、渠道从没建。"""
        step = SimpleNamespace(step_key="DEP-1:set_line", status="skipped", payload="{}")
        req = SimpleNamespace(issue="ISS-1", steps=[step])
        issue = SimpleNamespace(apps=[_row(secret="secret", token="token", domain="mrerp.pearnly.com")])
        dep = SimpleNamespace(name="DEP-1")
        docs = {provisioning.ISSUE: issue, provisioning.DEPLOYMENT: dep, provisioning.LINE_CHANNEL: "CHANNEL"}
        frappe.db.get_value.return_value = "DEP-1"
        frappe.db.exists.return_value = False
        with patch.object(frappe, "get_doc", side_effect=lambda doctype, name: docs[doctype]), \
             patch.object(provisioning, "_ensure_line_channel", return_value="LINE-DEP-1") as ensure, \
             patch.object(provisioning, "_steps_for", return_value=[{"step_key": "DEP-1:set_line", "payload": "{}"}]), \
             patch.object(provisioning, "queue_cloud_line_config") as queue:
            self.assertEqual(provisioning._sync_steps(req), 1)
        ensure.assert_called_once_with(issue, issue.apps[0], dep, ("secret", "token"))
        queue.assert_called_once_with("CHANNEL")

    def test_rerun_plan_leaves_an_existing_channel_alone(self):
        req = SimpleNamespace(issue="ISS-1", steps=[])
        issue = SimpleNamespace(apps=[_row(secret="old", token="old", domain="mrerp.pearnly.com")])
        frappe.db.get_value.return_value = "DEP-1"
        frappe.db.exists.return_value = True
        with patch.object(frappe, "get_doc", side_effect=lambda doctype, name: issue if doctype == provisioning.ISSUE
                          else SimpleNamespace(name="DEP-1")), \
             patch.object(provisioning, "_ensure_line_channel") as ensure, \
             patch.object(provisioning, "_steps_for", return_value=[]), \
             patch.object(provisioning, "queue_cloud_line_config") as queue:
            provisioning._sync_steps(req)
        ensure.assert_not_called(); queue.assert_not_called()



class LineLoginSecret(unittest.TestCase):
    """全平台一个 LINE Login 频道：ERP 与 Cowork 的租户都拿到密钥（Cowork 2026-09-30 接入），DMS 不接。"""

    def secret_for(self, product, liff="2010411313-abc"):
        values = {("Pearnly Target", "T1", "line_login_channel_id"): "2010411313"}
        db = Mock(get_value=lambda dt, name, field: values.get((dt, name, field)))
        with patch.object(frappe, "db", db, create=True), \
                patch("frappe.utils.password.get_decrypted_password", return_value="login-secret"):
            return provisioning.line_login_secret("T1", product, liff)

    def test_erp_and_cowork_get_the_platform_login_secret(self):
        self.assertEqual(self.secret_for("erp"), "login-secret")
        self.assertEqual(self.secret_for("cowork"), "login-secret")

    def test_dms_and_foreign_liff_get_nothing(self):
        self.assertEqual(self.secret_for("dms"), "")
        self.assertEqual(self.secret_for("cowork", liff="1999999999-abc"), "")


if __name__ == "__main__":
    unittest.main()
