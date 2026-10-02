"""Offline failure-boundary tests; no Site writes or outbound calls."""
import unittest
from unittest.mock import MagicMock, patch

from pearnly_core.account import oidc_session, preferences, profile


class TestPreferences(unittest.TestCase):
    def setUp(self):
        self.native = MagicMock()
        self.native.flags.pearnly_locale_synced = False
        self.native.session.user = "preview@example.test"
        self.native.session.sid = "session-a"
        self.native.AuthenticationError = PermissionError
        self.native.PermissionError = PermissionError
        self.native.throw.side_effect = lambda message, *args: (_ for _ in ()).throw(PermissionError(message))
        self.native.conf.pearnly_oidc_issuer = "https://identity.invalid/realms/test"
        self.native.conf.pearnly_oidc_client_id = "test"
        self.native.conf.pearnly_oidc_client_secret = "test-only"
        self.native.cache.get_value.return_value = {
            "id_token": "verified-id", "refresh_token": "old-refresh", "subject": "subject-a",
        }
        # 登录会话（refresh token）在不淘汰的状态存储（pearnly_core.execution.state）；与 frappe.cache 同一个替身
        p = patch("pearnly_core.execution.state.store", return_value=self.native.cache)
        p.start()
        self.addCleanup(p.stop)
        for module in (preferences, oidc_session, profile):
            p = patch.object(module, "frappe", self.native)
            p.start()
            self.addCleanup(p.stop)
            p = patch.object(module, "_", lambda text: text)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(oidc_session, "requests")
        self.http = p.start()
        self.addCleanup(p.stop)
        p = patch.object(oidc_session, "remember")
        self.remember = p.start()
        self.addCleanup(p.stop)
        self.http.post.return_value = self.response(200, {"access_token": "user-bearer", "refresh_token": "rotated"})

    @staticmethod
    def response(status, body):
        response = MagicMock(status_code=status)
        response.json.return_value = body
        return response

    def test_guest_cannot_write_language(self):
        self.native.session.user = "Guest"
        with self.assertRaises(PermissionError):
            preferences.set_language("th")
        self.http.post.assert_not_called()
        self.native.get_doc.assert_not_called()

    def test_unknown_language_cannot_write(self):
        with self.assertRaises(PermissionError):
            preferences.set_language("invalid")
        self.http.post.assert_not_called()
        self.native.get_doc.assert_not_called()

    def test_expired_refresh_saves_choice_for_next_verified_login(self):
        self.http.post.return_value = self.response(401, {})
        self.assertEqual(preferences.set_language("th"), {"language": "th"})
        self.native.defaults.set_user_default.assert_called_with("pearnly_pending_locale", "th")
        self.assertEqual(self.native.get_doc.return_value.language, "th")

    def test_profile_for_another_subject_is_rejected(self):
        self.http.get.return_value = self.response(200, {"id": "subject-b"})
        with self.assertRaises(PermissionError):
            preferences.set_language("th")
        self.assertEqual(self.http.post.call_count, 1)  # refresh only
        self.native.get_doc.assert_not_called()
        self.remember.assert_called_once()  # rotation is preserved on failure

    def test_failed_readback_does_not_claim_local_success(self):
        self.http.get.side_effect = [
            self.response(200, {"id": "subject-a", "attributes": {"locale": ["en"]}}),
            self.response(200, {"attributes": {"locale": ["en"]}}),
        ]
        with self.assertRaises(PermissionError):
            preferences.set_language("th")
        self.native.get_doc.assert_not_called()

    def test_language_preserves_profile_and_updates_native_user(self):
        self.http.get.side_effect = [
            self.response(200, {"id": "subject-a", "firstName": "Preview", "attributes": {"other": ["keep"]}}),
            self.response(200, {"attributes": {"locale": ["zh-CN"]}}),
        ]
        self.assertEqual(preferences.set_language("zh"), {"language": "zh"})
        payload = self.http.post.call_args.kwargs["json"]
        self.assertEqual(payload, {"firstName": "Preview", "attributes": {"other": ["keep"], "locale": ["zh-CN"]}})
        self.native.get_doc.assert_called_once_with("User", "preview@example.test")
        self.assertEqual(self.native.get_doc.return_value.language, "zh")

    def profile(self):
        doc = MagicMock()
        doc.name = "preview@example.test"
        doc.language = "th"
        doc.is_new.return_value = False
        doc.has_value_changed.return_value = True
        return doc

    def test_native_profile_syncs_identity_and_cookie(self):
        with patch.object(oidc_session, "update_locale") as update:
            preferences.sync_profile_language(self.profile())
            update.assert_called_once_with("th")
        self.native.local.cookie_manager.set_cookie.assert_called_with("pearnly_locale", "th")

    def test_native_profile_failure_aborts_save(self):
        with patch.object(oidc_session, "update_locale", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                preferences.sync_profile_language(self.profile())
        self.native.local.cookie_manager.set_cookie.assert_not_called()

    def test_profile_does_not_change_another_identity(self):
        doc = self.profile()
        doc.name = "another@example.test"
        with patch.object(oidc_session, "update_locale") as update:
            preferences.sync_profile_language(doc)
            update.assert_not_called()

    def test_verified_login_does_not_repeat_identity_write(self):
        doc = self.native.get_doc.return_value
        doc.language = "en"
        doc.save.side_effect = lambda **kw: self.assertTrue(self.native.flags.pearnly_locale_synced)
        preferences.apply_login_locale("preview@example.test", "th")
        self.assertFalse(self.native.flags.pearnly_locale_synced)

    def test_cloud_profile_cannot_set_a_local_password(self):
        doc = self.profile()
        doc.get.return_value = "fixture-not-a-real-password"
        with self.assertRaises(PermissionError):
            profile.reject_local_password(doc)

    def test_profile_without_password_remains_editable(self):
        doc = self.profile()
        doc.get.return_value = None
        profile.reject_local_password(doc)
        self.native.throw.assert_not_called()

    def test_credentials_are_scoped_to_current_session(self):
        oidc_session.forget()
        self.native.cache.delete_value.assert_called_once_with("pearnly_oidc_session:session-a")
        self.native.session.sid = "session-b"
        oidc_session.logout_hint()
        self.native.cache.get_value.assert_called_with("pearnly_oidc_session:session-b")

    def test_unsupported_login_locale_does_not_overwrite_preference(self):
        preferences.apply_login_locale("preview@example.test", "unknown")
        self.native.get_doc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
