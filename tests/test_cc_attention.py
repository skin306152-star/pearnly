"""叫人的统一判据（``pearnly_core.assistant.attention``，总设计 15.2）：人正看着那段对话就不叫，不在才叫。

断言的是应有状态：正看着不叫、不在才记提醒；没续上（过了 75 秒）算不在；多台设备任一在看都不叫；看的是别的对话照叫；
隐藏时报一次「不在」立即生效；坏的设备号不收；缓存读不出照旧叫人；推送发前再判（记下提醒之后人回到了那段对话就不推）。

替身 frappe（内存缓存）、替身提醒，不连库。

    python3 tests/test_cc_attention.py
"""
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

CACHE, POSTED = {}, []


class Cache:
    broken = False

    def get_value(self, key):
        if self.broken:
            raise ConnectionError("redis down")
        return CACHE.get(key)

    def set_value(self, key, value, expires_in_sec=None):
        CACHE[key] = value


frappe = types.ModuleType("frappe")
frappe._ = lambda text: text
frappe.cache = Cache()
frappe.session = types.SimpleNamespace(user="u@example.com")


def _throw(message, exc=ValueError):
    raise exc(message)


frappe.throw = _throw
sys.modules["frappe"] = frappe
notices = types.ModuleType("pearnly_core.assistant.notices")
notices.post = lambda user, kind, text, dedup_key, **extra: (
    POSTED.append((user, kind, text, dedup_key, extra)) or {"name": f"N{len(POSTED)}", "created": True})
sys.modules["pearnly_core.assistant.notices"] = notices
import pearnly_core.assistant as assistant_package  # noqa: E402

assistant_package.notices = notices
from pearnly_core.assistant import attention  # noqa: E402

USER = "u@example.com"


class AttentionTests(unittest.TestCase):
    def setUp(self):
        CACHE.clear(), POSTED.clear()
        frappe.cache.broken = False

    def test_someone_looking_at_the_conversation_is_not_called(self):
        attention.seen("S1", True, "dev-a")
        self.assertTrue(attention.watching(USER, "S1"))
        self.assertEqual(attention.call(USER, "S1", "guide", "Waiting", "guide:RUN1:3"), {"called": False, "reason": "watching"})
        self.assertEqual(POSTED, [])

    def test_someone_not_looking_is_called_once_on_that_conversation(self):
        attention.seen("S2", True, "dev-a")
        found = attention.call(USER, "S1", "guide", "Waiting", "guide:RUN1:3", title="Collections")
        self.assertEqual(found, {"called": True, "notice": "N1"})
        self.assertEqual(POSTED[0][:4], (USER, "guide", "Waiting", "guide:RUN1:3"))
        self.assertEqual(POSTED[0][4]["conversation"], "S1")

    def test_a_device_that_stopped_renewing_counts_as_away(self):
        attention.seen("S1", True, "dev-a", now=1000)
        self.assertTrue(attention.watching(USER, "S1", now=1000 + attention.FRESH_SECONDS))
        self.assertFalse(attention.watching(USER, "S1", now=1001 + attention.FRESH_SECONDS))

    def test_any_device_looking_is_enough_and_hiding_takes_effect_at_once(self):
        attention.seen("S1", True, "phone", now=1000)
        attention.seen("S9", True, "desk", now=1000)
        self.assertTrue(attention.watching(USER, "S1", now=1001))
        attention.seen("S1", False, "phone", now=1002)
        self.assertFalse(attention.watching(USER, "S1", now=1003))
        self.assertTrue(attention.watching(USER, "S9", now=1003))

    def test_another_person_looking_does_not_stop_the_call(self):
        attention.seen("S1", True, "dev-a", user="other@example.com")
        self.assertFalse(attention.watching(USER, "S1"))

    def test_a_bad_device_id_or_a_guest_is_refused(self):
        self.assertRaises(ValueError, attention.seen, "S1", True, "../x")
        self.assertRaises(ValueError, attention.seen, "S1", True, "dev", user="Guest")

    def test_a_cache_that_cannot_be_read_calls_rather_than_misses(self):
        frappe.cache.broken = True
        self.assertFalse(attention.watching(USER, "S1"))
        self.assertTrue(attention.call(USER, "S1", "guide", "Waiting", "k")["called"])


class PushDecidesBeforeSending(unittest.TestCase):
    def test_send_notice_checks_attention_before_sending(self):
        source = (ROOT / "apps/pearnly_core/pearnly_core/assistant/push.py").read_text(encoding="utf-8")
        body = source[source.index("def send_notice"):]
        body = body[:body.index("\ndef ", 1)] if "\ndef " in body[1:] else body
        self.assertLess(body.index("attention.watching(row.user, row.session_id)"), body.index("return send("))


if __name__ == "__main__":
    unittest.main()
