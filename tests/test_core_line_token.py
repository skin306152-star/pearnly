"""The shared LINE layer issues its own short-lived channel token from the Channel ID and secret (2026-10-05).

Run with Frappe installed. No Site, Redis or network: configuration, cache and HTTP are replaced.
"""
import unittest
from unittest.mock import Mock, patch

import requests

from pearnly_core import line


class Cache:
    def __init__(self):
        self.values, self.ttl = {}, {}

    def get_value(self, key):
        return self.values.get(key)

    def set_value(self, key, value, expires_in_sec=None):
        self.values[key], self.ttl[key] = value, expires_in_sec


def answer(status, body):
    response = Mock(status_code=status)
    response.json.return_value = body
    return response


class AccessToken(unittest.TestCase):
    def setUp(self):
        self.conf = {"pearnly_line_channel_id": "2011793651", "pearnly_line_channel_secret": "secret-a",
                     "pearnly_line_channel_access_token": ""}
        self.cache = Cache()
        self.enterContext(patch.object(line.frappe, "conf", self.conf))
        self.enterContext(patch.object(line.frappe, "cache", self.cache, create=True))
        self.post = self.enterContext(patch.object(line.requests, "post",
                                                   return_value=answer(200, {"access_token": "short-1"})))

    def test_issues_once_and_shares_it_through_the_site_cache(self):
        self.assertTrue(line.is_configured())
        self.assertEqual(line.access_token(), "short-1")
        self.assertEqual(line.access_token(), "short-1")
        self.post.assert_called_once_with(line._TOKEN_API, data={"grant_type": "client_credentials",
                                                                 "client_id": "2011793651",
                                                                 "client_secret": "secret-a"}, timeout=line.TIMEOUT)
        # LINE 给 15 分钟，缓存只留 10 分钟。
        self.assertEqual(set(self.cache.ttl.values()), {600})
        self.assertNotIn("secret-a", "".join(self.cache.values))

    def test_a_new_secret_never_reuses_the_old_token(self):
        line.access_token()
        self.conf["pearnly_line_channel_secret"] = "secret-b"
        self.post.return_value = answer(200, {"access_token": "short-2"})
        self.assertEqual(line.access_token(), "short-2")

    def test_stored_long_lived_token_wins_and_nothing_is_issued(self):
        self.conf["pearnly_line_channel_access_token"] = "long-lived"
        self.assertEqual(line._headers()["Authorization"], "Bearer long-lived")
        self.post.assert_not_called()

    def test_a_stored_token_alone_can_send_but_cannot_verify_webhooks(self):
        # 菜单发布、loading 动画只要令牌；收消息验签还要密钥（行为与 10-05 之前一致）。
        self.conf.update(pearnly_line_channel_access_token="long-lived", pearnly_line_channel_secret="",
                         pearnly_line_channel_id="")
        self.assertTrue(line.can_send())
        self.assertFalse(line.is_configured())

    def test_without_a_channel_there_is_nothing_to_send_with(self):
        for values in ({"pearnly_line_channel_secret": ""},
                       {"pearnly_line_channel_id": "", "pearnly_line_channel_access_token": ""}):
            with self.subTest(values=values), patch.dict(self.conf, values):
                self.assertFalse(line.is_configured())
                self.assertFalse(line.can_send())
                self.assertEqual(line.access_token(), "")
        self.post.assert_not_called()

    def test_refused_issue_is_a_request_error_without_the_secret(self):
        self.post.return_value = answer(400, {"error": "invalid_client"})
        with self.assertRaises(requests.RequestException) as caught:
            line.access_token()
        self.assertNotIn("secret-a", str(caught.exception))

    def test_push_counts_a_failed_issue_as_a_failed_push(self):
        self.post.side_effect = requests.ConnectionError("down")
        with patch.object(line, "_count") as count, patch.object(line, "mirroring", return_value=False):
            self.assertFalse(line.push("U" + "a" * 32, [{"type": "text", "text": "hi"}]))
        count.assert_called_once_with("push_failed")

    def test_background_senders_degrade_instead_of_raising(self):
        self.post.side_effect = requests.ConnectionError("down")
        self.assertFalse(line.reply_sender("reply-token")([{"type": "text", "text": "hi"}]))
        self.assertFalse(line.start_loading_background("U" + "a" * 32)())


def unsent(reason_type=None):
    """requests 在连上之前失败时的真实形状：ConnectionError(MaxRetryError(reason=NewConnectionError…))。"""
    from urllib3.exceptions import MaxRetryError, NameResolutionError
    reason = (reason_type or NameResolutionError)("api.line.me", None, OSError(-2, "Name or service not known"))
    return requests.exceptions.ConnectionError(MaxRetryError(None, "/v2/bot/message/push", reason=reason))


class ConnectRetry(unittest.TestCase):
    """还没连上 LINE 就失败（解析不到域名）才重试；可能已送达的失败不重试，免得用户收到两条（2026-10-11）。"""

    def setUp(self):
        self.conf = {"pearnly_line_channel_access_token": "long-lived", "pearnly_line_channel_secret": "s"}
        self.enterContext(patch.object(line.frappe, "conf", self.conf))
        self.enterContext(patch.object(line.frappe, "cache", Cache(), create=True))
        self.enterContext(patch.object(line, "mirroring", return_value=False))
        self.enterContext(patch.object(line, "_count"))
        self.sleep = self.enterContext(patch.object(line.time, "sleep"))

    def test_a_lookup_failure_is_retried_and_the_push_lands(self):
        post = self.enterContext(patch.object(line.requests, "post",
                                              side_effect=[unsent(), answer(200, {})]))
        self.assertTrue(line.push("U1", [{"type": "text", "text": "hi"}]))
        self.assertEqual(post.call_count, 2)
        self.sleep.assert_called_once_with(line.CONNECT_RETRY_DELAYS[0])

    def test_a_reply_that_may_have_reached_line_is_not_resent(self):
        post = self.enterContext(patch.object(line.requests, "post",
                                              side_effect=requests.exceptions.ReadTimeout("read")))
        ok, status, _why = line._post_reply("token", [{"type": "text", "text": "hi"}])
        self.assertEqual((ok, status), (False, 0))
        self.assertEqual(post.call_count, 1)
        self.sleep.assert_not_called()

    def test_retries_are_bounded(self):
        post = self.enterContext(patch.object(line.requests, "post", side_effect=unsent()))
        self.assertFalse(line.push("U1", [{"type": "text", "text": "hi"}]))
        self.assertEqual(post.call_count, len(line.CONNECT_RETRY_DELAYS) + 1)

    def test_api_requests_retry_lookups_and_keep_the_token_out_of_the_error(self):
        request = self.enterContext(patch.object(line.requests, "request",
                                                 side_effect=[unsent(), answer(200, {})]))
        self.assertEqual(line.request("GET", "https://api.line.me/v2/bot/info").status_code, 200)
        self.assertEqual(request.call_count, 2)
        request.side_effect = unsent()
        with self.assertRaises(requests.RequestException) as caught:
            line.request("GET", "https://api.line.me/v2/bot/info")
        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn("long-lived", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
