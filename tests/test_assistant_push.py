#!/usr/bin/env python3
"""手机 / 电脑浏览器网页推送（ADR-019 送达通道 2）的离线检查：

- 加密与 RFC 8291 附录 A 逐字节相同；VAPID 签名的待签部分与 RFC 8292 示例逐字节相同，RFC 示例签名能验过，
  自己签的头格式、aud、exp 正确且能用自己的公钥验过。
- 订阅只认白名单里的推送服务（https、默认端口、不带账号），设备公钥与密钥长度正确。
- 推送服务：201 记 last_ok；404 / 410 删订阅；403 / 500 保留只记原因；不跟随跳转；连不上再试一次；
  日志与订阅行不出现推送地址。订阅换人覆盖、每人最多 10 台、只能退自己的；密钥每站只生成一次并提交，
  私钥解不开就换一对并删掉全部订阅。

要 cryptography：它在生产镜像里（Frappe 依赖），开发闸门的系统 Python 没有，所以本文件只在生产候选镜像的离线通道跑
（`scripts/ci-candidate-checks.sh`）。没有 cryptography 时直接报错退出，不静默跳过。Frappe 与 requests 用最小替身。
"""
import ast
import base64
import hashlib
import hmac
import importlib.util
import json
import os
import pathlib
import re
import struct
import sys
import types
import unittest

try:
    import cryptography
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    sys.exit("tests/test_assistant_push.py needs cryptography: run it in the production-candidate offline lane "
             "(scripts/ci-candidate-checks.sh), not with a bare system Python")

print(f"cryptography {cryptography.__version__}")

ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE = ROOT / "apps/pearnly_core/pearnly_core"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


contracts = _load("pearnly_core.assistant.contracts", CORE / "assistant/contracts.py")
webpush = _load("pearnly_core.assistant.webpush", CORE / "assistant/webpush.py")


def b64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def point(key) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def receive(body: bytes, ua_key, auth: bytes) -> bytes:
    """浏览器一侧的解密（按 RFC 8291 另写一遍，不用被测代码）。"""
    salt, size, id_len = body[:16], struct.unpack("!I", body[16:20])[0], body[20]
    assert (size, id_len) == (4096, 65), (size, id_len)
    as_public = body[21:21 + id_len]
    ecdh = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    mac = lambda key, data: hmac.new(key, data, hashlib.sha256).digest()  # noqa: E731
    ikm = mac(mac(auth, ecdh), b"WebPush: info\x00" + point(ua_key) + as_public + b"\x01")
    prk = mac(salt, ikm)
    cek, nonce = mac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16], mac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    record = AESGCM(cek).decrypt(nonce, body[21 + id_len:], None)
    assert record.endswith(b"\x02")
    return record[:-1]


# RFC 8291 附录 A（2026-10-12 用 cryptography 50.0.2 从输入逐项重算，与 RFC 一致）
RFC = {
    "plaintext": b"When I grow up, I want to be a watermelon",
    "as_private": "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw",
    "as_public": "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8",
    "ua_private": "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94",
    "ua_public": "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
    "auth": "BTBZMqHH6r4Tts7J_aSIgg",
    "salt": "DGv6ra1nlYgDCS1FRnbzlw",
    "ecdh": "kyrL1jIIOHEzg3sM2ZWRHDRB62YACZhhSlknJ672kSs",
    "key_info": "V2ViUHVzaDogaW5mbwAEJXGyvs3942BVGq8e0PTNNmwRzr5VX4m8t7GGpTM5FzFo7OLr4BhZe9MEebhuPI-OztV3ylkYfpJGmQ22ggCLDgT-"
                "M_SrDepxkU21WCP3O1SUj0EwbZIHMtu5pZpTKGSCIA5Zent7wmC6HCJ5mFgJkuk5cwAvMBKiiujwa7t45ewP",
    "prk_key": "Snr3JMxaHVDXHWJn5wdC52WjpCtd2EIEGBykDcZW32k",
    "ikm": "S4lYMb_L0FxCeq0WhDx813KgSYqU26kOyzWUdsXYyrg",
    "prk": "09_eUZGrsvxChDCGRCdkLiDXrReGOEVeSCdCcPBSJSc",
    "cek_info": "Q29udGVudC1FbmNvZGluZzogYWVzMTI4Z2NtAA",
    "cek": "oIhVW04MRdy2XN9CiKLxTg",
    "nonce_info": "Q29udGVudC1FbmNvZGluZzogbm9uY2UA",
    "nonce": "4h_95klXJ5E_qnoN",
    "header": "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8",
    "padded": "V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24C",
    "ciphertext": "8pfeW0KbunFT06SuDKoJH9Ql87S1QUrdirN6GcG7sFz1y1sqLgVi1VhjVkHsUoEsbI_0LpXMuGvnzQ",
    "message": "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_"
               "yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN",
}
# RFC 8292 §2.4 / §3.1 示例
VAPID_CLAIMS = {"aud": "https://push.example.net", "exp": 1453523768, "sub": "mailto:push@example.com"}
VAPID_INPUT = ("eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzI1NiJ9.eyJhdWQiOiJodHRwczovL3B1c2guZXhhbXBsZS5uZXQiLCJleHAiOjE0NTM1MjM3NjgsInN1"
               "YiI6Im1haWx0bzpwdXNoQGV4YW1wbGUuY29tIn0")
VAPID_SIGNATURE = "i3CYb7t4xfxCDquptFOepC9GAu_HLGkMlMuCGSK2rpiUfnK9ojFwDXb1JrErtmysazNjjvW2L9OkSSHzvoD1oA"
VAPID_KEY = "BA1Hxzyi1RUM1b5wjxsn7nGxAszw2u61m164i3MrAIxHF6YK5h4SDYic-dRuU_RCPCfA5aq9ojSwk5Y2EmClBPs"

FCM = "https://fcm.googleapis.com/fcm/send/dX7n3Qk-token-abc:APA91bSecretPart"


def device():
    """一台假设备：私钥、订阅里的 p256dh 与 auth。"""
    key = ec.generate_private_key(ec.SECP256R1())
    secret = os.urandom(16)
    return key, webpush.b64e(point(key)), webpush.b64e(secret), secret


class CryptographyTests(unittest.TestCase):
    def test_the_image_ships_a_recent_cryptography(self):
        self.assertGreaterEqual(int(cryptography.__version__.split(".")[0]), 42)


class Rfc8291Tests(unittest.TestCase):
    def test_every_intermediate_value_matches_appendix_a(self):
        keys = webpush.derive(b64(RFC["ua_public"]), b64(RFC["auth"]), b64(RFC["as_private"]), b64(RFC["salt"]))
        for name in ("ecdh", "key_info", "prk_key", "ikm", "prk", "cek", "nonce"):
            self.assertEqual(keys[name], b64(RFC[name]), name)
        self.assertEqual(keys["as_public"], b64(RFC["as_public"]))
        self.assertEqual(b"Content-Encoding: aes128gcm\x00", b64(RFC["cek_info"]))
        self.assertEqual(b"Content-Encoding: nonce\x00", b64(RFC["nonce_info"]))
        self.assertEqual(RFC["plaintext"] + b"\x02", b64(RFC["padded"]))

    def test_the_whole_message_is_byte_for_byte_the_rfc_example(self):
        sealed = webpush.encrypt(RFC["plaintext"], b64(RFC["ua_public"]), b64(RFC["auth"]),
                                 salt=b64(RFC["salt"]), sender_private=b64(RFC["as_private"]))
        self.assertEqual(len(sealed), 144)
        self.assertEqual(sealed[:86], b64(RFC["header"]))
        self.assertEqual(sealed[86:], b64(RFC["ciphertext"]))
        self.assertEqual(sealed, b64(RFC["message"]))

    def test_public_keys_follow_from_the_private_ones(self):
        self.assertEqual(webpush.public_of(RFC["as_private"]), RFC["as_public"])
        self.assertEqual(webpush.public_of(RFC["ua_private"]), RFC["ua_public"])
        self.assertTrue(webpush.matches(RFC["as_private"], RFC["as_public"]))
        self.assertFalse(webpush.matches(RFC["as_private"], RFC["ua_public"]))
        self.assertFalse(webpush.matches("", RFC["as_public"]))
        self.assertFalse(webpush.matches("not a key", RFC["as_public"]))

    def test_each_message_uses_fresh_randomness_and_the_device_can_read_it(self):
        ua_key, p256dh, _, secret = device()
        first = webpush.encrypt(b"hello", b64(p256dh), secret)
        second = webpush.encrypt(b"hello", b64(p256dh), secret)
        self.assertNotEqual(first[:16], second[:16])  # 盐
        self.assertNotEqual(first[21:86], second[21:86])  # 发送方临时公钥
        self.assertEqual(receive(first, ua_key, secret), b"hello")
        self.assertEqual(receive(second, ua_key, secret), b"hello")

    def test_one_record_only(self):
        _, p256dh, _, secret = device()
        webpush.encrypt(b"x" * (4096 - 17), b64(p256dh), secret)
        with self.assertRaises(ValueError):
            webpush.encrypt(b"x" * (4096 - 16), b64(p256dh), secret)
        with self.assertRaises(ValueError):
            webpush.encrypt(b"x", b64(p256dh), secret[:15])

    def test_new_keys_are_a_matching_pair_in_browser_format(self):
        private_key, public = webpush.new_keys()
        self.assertEqual(len(b64(private_key)), 32)
        self.assertEqual(len(b64(public)), 65)
        self.assertEqual(b64(public)[0], 4)
        self.assertNotIn("=", private_key + public)
        self.assertTrue(webpush.matches(private_key, public))
        self.assertNotEqual(webpush.new_keys(), (private_key, public))


class Rfc8292Tests(unittest.TestCase):
    def test_signing_input_is_byte_for_byte_the_rfc_example(self):
        self.assertEqual(webpush.signing_input(VAPID_CLAIMS), VAPID_INPUT)

    def test_the_rfc_example_signature_verifies(self):
        self.assertTrue(webpush.verify(VAPID_INPUT + "." + VAPID_SIGNATURE, VAPID_KEY))
        self.assertFalse(webpush.verify(VAPID_INPUT.replace("eyJhdWQi", "eyJhdWRi") + "." + VAPID_SIGNATURE, VAPID_KEY))
        self.assertFalse(webpush.verify(VAPID_INPUT + "." + VAPID_SIGNATURE, RFC["as_public"]))
        self.assertFalse(webpush.verify(VAPID_INPUT + ".short", VAPID_KEY))

    def test_our_header_names_the_push_service_expires_in_an_hour_and_verifies(self):
        private_key, public = webpush.new_keys()
        header = webpush.vapid_authorization(FCM, private_key, public, "https://pearnly.com", now=1_800_000_000)
        match = re.fullmatch(r"vapid t=([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.([A-Za-z0-9_-]+)), k=([A-Za-z0-9_-]+)", header)
        self.assertIsNotNone(match, header)
        token, signature, key = match.groups()
        self.assertEqual(key, public)
        self.assertEqual(len(b64(signature)), 64)
        head, claims = (json.loads(b64(part)) for part in token.split(".")[:2])
        self.assertEqual(head, {"typ": "JWT", "alg": "ES256"})
        self.assertEqual(claims, {"aud": "https://fcm.googleapis.com", "exp": 1_800_003_600, "sub": "https://pearnly.com"})
        self.assertTrue(webpush.verify(token, public))
        self.assertFalse(webpush.verify(token, webpush.new_keys()[1]))
        self.assertEqual(webpush.audience("https://web.push.apple.com:443/QGx"), "https://web.push.apple.com")


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        _, self.p256dh, self.auth, _ = device()

    def test_known_push_services_are_accepted(self):
        for endpoint in (FCM, "https://web.push.apple.com/QGxyz-abc", "https://updates.push.services.mozilla.com/wpush/v2/gAAA",
                         "https://wns2-par02p.notify.windows.com/w/?token=BQYAAA%2b", "https://api.push.apple.com/3/device/x",
                         "https://android.googleapis.com/gcm/send/abc", "https://FCM.googleapis.com:443/fcm/send/abc"):
            sub = webpush.subscription(endpoint, self.p256dh, self.auth)
            self.assertEqual(sub["endpoint"], endpoint)
            self.assertEqual(sub["key"], hashlib.sha256(endpoint.encode()).hexdigest())
            self.assertEqual((sub["p256dh"], sub["auth"]), (self.p256dh, self.auth))

    def test_keys_in_standard_base64_with_padding_are_normalised(self):
        padded = base64.b64encode(b64(self.p256dh)).decode()
        sub = webpush.subscription(FCM, padded, base64.b64encode(b64(self.auth)).decode())
        self.assertEqual((sub["p256dh"], sub["auth"]), (self.p256dh, self.auth))

    def test_anything_else_is_refused(self):
        for endpoint in ("http://fcm.googleapis.com/fcm/send/a", "https://billing-web:8000/api", "https://169.254.169.254/latest",
                         "https://localhost/push", "https://user:pw@fcm.googleapis.com/fcm/send/a",
                         "https://fcm.googleapis.com@evil.example/a", "https://fcm.googleapis.com:8443/fcm/send/a",
                         "https://fcm.googleapis.com.evil.example/a", "https://evilfcm.googleapis.com/a",
                         "https://push.apple.com/a", "https://.push.apple.com/a", "https://[::1]/a",
                         "https://fcm.googleapis.com\\@evil.example/a", "https://fcm.googleapis.com/a b",
                         "https://fcm.googleapis.com:abc/a", "ftp://fcm.googleapis.com/a", "", None,
                         "https://fcm.googleapis.com/" + "a" * 1000):
            with self.assertRaises(webpush.InvalidSubscription, msg=endpoint):
                webpush.subscription(endpoint, self.p256dh, self.auth)
        compressed = webpush.b64e(b"\x02" + b64(self.p256dh)[1:33])
        not_on_curve = webpush.b64e(b"\x04" + b"\x01" * 64)
        for p256dh in ("", "abc", compressed, not_on_curve, webpush.b64e(b64(self.p256dh)[:64]), "!!notbase64"):
            with self.assertRaises(webpush.InvalidSubscription, msg=p256dh):
                webpush.subscription(FCM, p256dh, self.auth)
        for auth in ("", webpush.b64e(os.urandom(15)), webpush.b64e(os.urandom(17)), "a"):
            with self.assertRaises(webpush.InvalidSubscription, msg=auth):
                webpush.subscription(FCM, self.p256dh, auth)


class PayloadTests(unittest.TestCase):
    def test_payload_is_compact_json_for_the_service_worker(self):
        data = webpush.push_payload("Tasks", "3 new files", "/assistant?c=s_1", "notice:pnotice_1")
        self.assertEqual(data, '{"title":"Tasks","body":"3 new files","url":"/assistant?c=s_1","tag":"notice:pnotice_1"}'
                         .encode())
        for url in ("/app/home", "https://evil.example/assistant", "//evil.example", "/assistantx", "", None):
            with self.assertRaises(ValueError, msg=url):
                webpush.push_payload("t", "b", url, "x")

    def test_long_thai_text_is_cut_to_the_limit(self):
        body = "แจ้งเตือนไฟล์ใหม่ในโฟลเดอร์ " * 300  # 每字 3 字节，远超上限
        data = webpush.push_payload("งาน", body, "/assistant?view=tasks", "notice:pnotice_9")
        self.assertLessEqual(len(data), webpush.PAYLOAD_MAX)
        kept = json.loads(data)["body"]
        self.assertTrue(kept.endswith("…"))
        self.assertTrue(body.startswith(kept[:-1]))
        longer = webpush._compact({"title": "งาน", "body": body[:len(kept)] + "…", "url": "/assistant?view=tasks",
                                   "tag": "notice:pnotice_9"})
        self.assertGreater(len(longer), webpush.PAYLOAD_MAX)  # 已是能放下的最长前缀
        self.assertEqual(json.loads(webpush.push_payload("t", "short", "/assistant", "x"))["body"], "short")

    def test_deep_links_open_the_conversation_or_the_task_page(self):
        self.assertEqual(contracts.deep_link("sesn_01AbC:x.y"), "/assistant?c=sesn_01AbC%3Ax.y")
        for value in ("", None, "a", "../etc", "s s s s", "x" * 200):
            self.assertEqual(contracts.deep_link(value), "/assistant?view=tasks")
        self.assertEqual(contracts.deep_link(), "/assistant?view=tasks")


# ---- 服务（push.py）：最小 frappe 与 requests 替身 ----

class _Row(dict):
    __getattr__ = dict.get


SUBSCRIPTION = "Pearnly Assistant Push Subscription"
KEY = "Pearnly Assistant Push Key"
NOTICE = "Pearnly Assistant Notice"


class _Frappe(types.ModuleType):
    class ValidationError(Exception):
        pass

    class PermissionError(Exception):
        pass

    class AuthenticationError(Exception):
        pass

    class DuplicateEntryError(Exception):
        pass

    class UniqueValidationError(Exception):
        pass

    def __init__(self):
        super().__init__("frappe")
        self.reset()

    def reset(self):
        outer = self
        self.conf = {"pearnly_product": "cowork"}
        self.local = types.SimpleNamespace(site="t1.example")
        self.session = types.SimpleNamespace(user="a@example.com")
        self.rows, self.singles, self.passwords, self.notices = {}, {}, {}, {}
        self.users = {"a@example.com": 1, "b@example.com": 1, "off@example.com": 0}
        self.logs, self.locks, self.commits, self.saves, self.tick = [], [], 0, 0, 0
        self.stale_public = None  # 本事务快照里的公钥（None = 与已提交的一致）
        self.undecryptable = False
        self.hidden = set()  # exists() 看不到的（模拟同一设备两个页签同时认领）

        class _Lock:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def lock(name, timeout=None):
            outer.locks.append((name, timeout))
            return _Lock()

        self.cache = types.SimpleNamespace(lock=lock)
        self.utils = types.SimpleNamespace(now=lambda: "2026-10-12 12:00:00")
        self.db = types.SimpleNamespace(get_single_value=self._single_value, sql=self._sql, exists=self._exists,
                                        set_value=self._set_value, delete=self._delete, get_value=self._get_value,
                                        commit=self._commit)

    # -- db --
    def _single_value(self, doctype, field):
        assert (doctype, field) == (KEY, "public_key")
        return self.singles.get(field) if self.stale_public is None else self.stale_public

    def _sql(self, query, values):
        assert "FOR UPDATE" in query and "%s" in query and values == (KEY, "public_key"), (query, values)
        value = self.singles.get("public_key")
        return [(value,)] if value is not None else []

    def _exists(self, doctype, name):
        assert doctype == SUBSCRIPTION
        return name if name in self.rows and name not in self.hidden else None

    def _set_value(self, doctype, name, values, value=None, update_modified=True):
        assert doctype == SUBSCRIPTION
        if not isinstance(values, dict):
            values = {values: value}
        if name in self.rows:
            self.rows[name].update(values)
            if update_modified:
                self.tick += 1
                self.rows[name]["modified"] = self.tick

    @staticmethod
    def _match(row, filters):
        for field, wanted in (filters or {}).items():
            if isinstance(wanted, list) and wanted[0] == "in":
                if row.get(field) not in wanted[1]:
                    return False
            elif row.get(field) != wanted:
                return False
        return True

    def _delete(self, doctype, filters=None):
        assert doctype == SUBSCRIPTION
        for name in [n for n, row in self.rows.items() if self._match(row, filters)]:
            del self.rows[name]

    def _get_value(self, doctype, name, fields, as_dict=False):
        if doctype == "User":
            assert fields == "enabled"
            return self.users.get(name)
        assert doctype == NOTICE and as_dict
        found = self.notices.get(name)
        return _Row({f: found.get(f) for f in fields}, name=name) if found else None

    def _commit(self):
        self.commits += 1

    # -- documents --
    def get_single(self, doctype):
        assert doctype == KEY
        outer = self

        class _Single:
            public_key = outer.singles.get("public_key")
            private_key = "*" * 10 if "private_key" in outer.passwords else None

            def get_password(self, field, raise_exception=True):
                if outer.undecryptable:
                    raise outer.ValidationError("Encryption key is invalid")
                return outer.passwords.get(field)

            def save(self, ignore_permissions=False):
                assert ignore_permissions
                outer.singles["public_key"] = self.public_key
                outer.passwords["private_key"] = self.private_key
                outer.undecryptable = False
                outer.saves += 1

        return _Single()

    def get_doc(self, values):
        outer = self

        class _New:
            def insert(self, ignore_permissions=False):
                assert ignore_permissions and values["doctype"] == SUBSCRIPTION
                name = values["endpoint_key"]
                if name in outer.rows:
                    raise outer.DuplicateEntryError(name)
                outer.tick += 1
                outer.rows[name] = _Row({k: v for k, v in values.items() if k != "doctype"}, name=name,
                                        modified=outer.tick)
                return self

        return _New()

    def get_all(self, doctype, filters=None, fields=None, order_by=None, pluck=None):
        assert doctype == SUBSCRIPTION
        rows = [row for row in self.rows.values() if self._match(row, filters)]
        if order_by:
            assert order_by == "modified desc, name desc"
            rows.sort(key=lambda r: (r["modified"], r["name"]), reverse=True)
        if pluck:
            return [row[pluck] for row in rows]
        return [_Row({f: row.get(f) for f in fields}) for row in rows]

    def throw(self, message, exc=None):
        raise (exc or self.ValidationError)(message)

    def log_error(self, title=None, message=None):
        assert message, "push must log a plain message, never a traceback (it carries local variables)"
        self.logs.append((title, message))

    @staticmethod
    def _(text):
        return text


class _Requests(types.ModuleType):
    """`requests` 替身：按顺序交回脚本里的结果（状态码或要抛的异常）。"""

    class RequestException(Exception):
        pass

    def __init__(self):
        super().__init__("requests")
        connection = type("ConnectionError", (self.RequestException,), {})
        timeout = type("ReadTimeout", (self.RequestException,), {})
        self.exceptions = types.SimpleNamespace(ConnectionError=connection, ReadTimeout=timeout,
                                                RequestException=self.RequestException)
        self.calls, self.script = [], []

    def post(self, url, data=None, headers=None, timeout=None, allow_redirects=True):
        self.calls.append({"url": url, "data": data, "headers": dict(headers or {}), "timeout": timeout,
                           "allow_redirects": allow_redirects})
        outcome = self.script.pop(0) if self.script else 201
        if isinstance(outcome, type) and issubclass(outcome, Exception):
            raise outcome("boom " + url)
        return types.SimpleNamespace(status_code=outcome)


def _install_service():
    frappe, requests = _Frappe(), _Requests()
    sys.modules["frappe"], sys.modules["requests"] = frappe, requests
    for name in ("pearnly_core", "pearnly_core.assistant"):
        sys.modules[name] = types.ModuleType(name)
    package = sys.modules["pearnly_core.assistant"]
    package.contracts, package.webpush = contracts, webpush
    package.engines = _load("pearnly_core.assistant.engines", CORE / "assistant/engines/__init__.py")
    package.config = _load("pearnly_core.assistant.config", CORE / "assistant/config.py")
    package.push = _load("pearnly_core.assistant.push", CORE / "assistant/push.py")
    return frappe, requests, package.push


frappe, requests, push = _install_service()


class _Service(unittest.TestCase):
    def setUp(self):
        frappe.reset()
        requests.calls.clear()
        requests.script.clear()

    def subscribe(self, endpoint=FCM, user="a@example.com"):
        frappe.session.user = user
        key, p256dh, auth, secret = device()
        push.subscribe(endpoint, p256dh, auth)
        return key, secret


class KeyTests(_Service):
    def test_the_site_key_is_made_once_under_the_site_lock_and_committed(self):
        first = push.public_key()
        self.assertEqual(len(b64(first)), 65)
        self.assertEqual(frappe.locks, [("t1.example:pearnly_assistant:vapid", push.LOCK_TIMEOUT)])
        self.assertEqual((frappe.saves, frappe.commits), (1, 1))  # 对话页取公钥是 GET，不提交就随请求回滚
        self.assertTrue(webpush.matches(frappe.passwords["private_key"], first))
        self.assertEqual(push.public_key(), first)
        self.assertEqual((len(frappe.locks), frappe.saves, frappe.commits), (1, 1, 1))

    def test_a_key_committed_while_waiting_for_the_lock_is_used_not_replaced(self):
        private_key, public = webpush.new_keys()
        frappe.singles["public_key"], frappe.passwords["private_key"] = public, private_key
        frappe.stale_public = ""  # 本事务的快照还没有，加锁读读到别的进程刚提交的
        self.assertEqual(push.public_key(), public)
        self.assertEqual((frappe.saves, frappe.commits), (0, 0))

    def test_an_unreadable_private_key_is_replaced_and_old_subscriptions_dropped(self):
        old = push.public_key()
        self.subscribe()
        self.subscribe("https://web.push.apple.com/QGx", user="b@example.com")
        frappe.undecryptable = True
        result = push.send("a@example.com", title="t", body="b", url="/assistant", tag="x")
        self.assertEqual(result, {"sent": 0, "gone": 0, "failed": 0})
        self.assertEqual(frappe.rows, {})
        self.assertEqual(requests.calls, [])
        self.assertNotEqual(frappe.singles["public_key"], old)
        self.assertTrue(webpush.matches(frappe.passwords["private_key"], frappe.singles["public_key"]))
        self.assertEqual([title for title, _ in frappe.logs], ["Assistant push keys replaced"])


class SubscribeTests(_Service):
    def test_the_same_device_belongs_to_whoever_signed_in_last(self):
        self.subscribe(user="a@example.com")
        self.subscribe(user="b@example.com")
        key = hashlib.sha256(FCM.encode()).hexdigest()
        self.assertEqual(list(frappe.rows), [key])
        self.assertEqual(frappe.rows[key]["user"], "b@example.com")
        self.assertEqual(frappe.rows[key]["endpoint"], FCM)

    def test_two_tabs_claiming_at_once_update_instead_of_failing(self):
        self.subscribe(user="a@example.com")
        frappe.hidden.add(hashlib.sha256(FCM.encode()).hexdigest())
        self.subscribe(user="b@example.com")
        self.assertEqual([row["user"] for row in frappe.rows.values()], ["b@example.com"])

    def test_each_person_keeps_their_ten_latest_devices(self):
        endpoints = [f"{FCM}-{i}" for i in range(12)]
        for endpoint in endpoints:
            self.subscribe(endpoint)
        self.subscribe("https://web.push.apple.com/other", user="b@example.com")
        mine = sorted(row["endpoint"] for row in frappe.rows.values() if row["user"] == "a@example.com")
        self.assertEqual(mine, sorted(endpoints[2:]))
        self.subscribe(endpoints[2])  # 重新认领的算最新
        self.subscribe(f"{FCM}-new")
        mine = {row["endpoint"] for row in frappe.rows.values() if row["user"] == "a@example.com"}
        self.assertIn(endpoints[2], mine)
        self.assertNotIn(endpoints[3], mine)
        self.assertEqual(len(mine), push.MAX_PER_USER)

    def test_bad_subscriptions_guests_and_sites_without_a_product_are_refused(self):
        _, p256dh, auth, _ = device()
        with self.assertRaises(contracts.ContractError):
            push.subscribe("https://billing-web:8000/x", p256dh, auth)
        with self.assertRaises(contracts.ContractError):
            push.subscribe(FCM, p256dh, "short")
        frappe.session.user = "Guest"
        with self.assertRaises(frappe.AuthenticationError):
            push.subscribe(FCM, p256dh, auth)
        frappe.session.user = "a@example.com"
        frappe.conf = {}
        with self.assertRaises(frappe.PermissionError):
            push.subscribe(FCM, p256dh, auth)
        self.assertEqual(frappe.rows, {})

    def test_people_can_only_remove_their_own_device(self):
        self.subscribe(user="a@example.com")
        frappe.session.user = "b@example.com"
        push.unsubscribe(FCM)
        self.assertEqual(len(frappe.rows), 1)
        push.unsubscribe("")
        push.unsubscribe("x" * 5000)
        frappe.session.user = "a@example.com"
        push.unsubscribe(FCM)
        self.assertEqual(frappe.rows, {})
        frappe.session.user = "Guest"
        with self.assertRaises(frappe.AuthenticationError):
            push.unsubscribe(FCM)


class SendTests(_Service):
    APPLE = "https://web.push.apple.com/QGx-secret-device-token"

    def test_delivered_messages_are_readable_and_signed_for_the_push_service(self):
        key, secret = self.subscribe()
        result = push.send("a@example.com", title="Reminder", body="2 files", url="/assistant?c=s_1",
                           tag="notice:pnotice_1", ttl=3600)
        self.assertEqual(result, {"sent": 1, "gone": 0, "failed": 0})
        [call] = requests.calls
        self.assertEqual(call["url"], FCM)
        self.assertIs(call["allow_redirects"], False)
        self.assertEqual(call["timeout"], push.TIMEOUT)
        headers = call["headers"]
        self.assertEqual({k: headers[k] for k in ("TTL", "Content-Encoding", "Content-Type", "Urgency")},
                         {"TTL": "3600", "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                          "Urgency": "high"})
        match = re.fullmatch(r"vapid t=(\S+), k=(\S+)", headers["Authorization"])
        self.assertEqual(match.group(2), frappe.singles["public_key"])
        self.assertTrue(webpush.verify(match.group(1), frappe.singles["public_key"]))
        claims = json.loads(b64(match.group(1).split(".")[1]))
        self.assertEqual((claims["aud"], claims["sub"]), ("https://fcm.googleapis.com", push.SUBJECT))
        self.assertEqual(json.loads(receive(call["data"], key, secret)),
                         {"title": "Reminder", "body": "2 files", "url": "/assistant?c=s_1", "tag": "notice:pnotice_1"})
        row = frappe.rows[hashlib.sha256(FCM.encode()).hexdigest()]
        self.assertEqual((row["last_ok"], row["last_error"]), ("2026-10-12 12:00:00", ""))
        self.assertEqual(frappe.logs, [])

    def test_gone_devices_are_removed_other_failures_kept_without_addresses_in_logs(self):
        endpoints = {404: FCM + "-404", 410: FCM + "-410", 403: self.APPLE, 500: FCM + "-500", 201: FCM + "-ok"}
        for endpoint in endpoints.values():
            self.subscribe(endpoint)
        requests.script[:] = list(endpoints)
        result = push.send("a@example.com", title="t", body="b", url="/assistant", tag="x")
        self.assertEqual(result, {"sent": 1, "gone": 2, "failed": 2})
        left = {row["endpoint"]: row for row in frappe.rows.values()}
        self.assertEqual(set(left), {endpoints[403], endpoints[500], endpoints[201]})
        self.assertEqual(left[endpoints[403]]["last_error"], "HTTP 403 web.push.apple.com")
        self.assertEqual(left[endpoints[500]]["last_error"], "HTTP 500 fcm.googleapis.com")
        self.assertEqual([title for title, _ in frappe.logs], ["Assistant push failed"])
        logged = "\n".join(message for _, message in frappe.logs) + "".join(r["last_error"] for r in left.values())
        for secret in ("/fcm/send", "secret-device-token", "APA91b", "vapid", frappe.singles["public_key"]):
            self.assertNotIn(secret, logged)

    def test_a_connection_failure_is_tried_once_more(self):
        self.subscribe()
        requests.script[:] = [requests.exceptions.ConnectionError, 201]
        self.assertEqual(push.send("a@example.com", title="t", body="b", url="/assistant", tag="x")["sent"], 1)
        self.assertEqual(len(requests.calls), 2)
        requests.calls.clear()
        requests.script[:] = [requests.exceptions.ConnectionError, requests.exceptions.ConnectionError]
        self.assertEqual(push.send("a@example.com", title="t", body="b", url="/assistant", tag="x")["failed"], 1)
        self.assertEqual(len(requests.calls), 2)
        requests.calls.clear()
        requests.script[:] = [requests.exceptions.ReadTimeout]
        self.assertEqual(push.send("a@example.com", title="t", body="b", url="/assistant", tag="x")["failed"], 1)
        self.assertEqual(len(requests.calls), 1)  # 已经发出去的不重发
        self.assertEqual(next(iter(frappe.rows.values()))["last_error"], "ReadTimeout fcm.googleapis.com")
        self.assertNotIn("boom", "".join(message for _, message in frappe.logs))

    def test_stored_rows_outside_the_allow_list_are_never_contacted(self):
        self.subscribe()
        name = hashlib.sha256(FCM.encode()).hexdigest()
        frappe.rows[name]["endpoint"] = "https://billing-web:8000/api/method/x"
        self.assertEqual(push.send("a@example.com", title="t", body="b", url="/assistant", tag="x")["failed"], 1)
        self.assertEqual(requests.calls, [])
        self.assertEqual(frappe.rows[name]["last_error"], "InvalidSubscription")

    def test_nobody_to_send_to_means_no_key_and_no_request(self):
        for user in ("off@example.com", "Guest", "", "a@example.com"):
            self.assertEqual(push.send(user, title="t", body="b", url="/assistant", tag="x"),
                             {"sent": 0, "gone": 0, "failed": 0})
        self.subscribe(user="off@example.com")
        frappe.singles.clear()
        self.assertEqual(push.send("off@example.com", title="t", body="b", url="/assistant", tag="x")["sent"], 0)
        self.assertEqual((requests.calls, frappe.singles), ([], {}))

    def test_urgency_and_ttl_stay_within_what_push_services_accept(self):
        self.subscribe()
        push.send("a@example.com", title="t", body="b", url="/assistant", tag="x", ttl=10**9, urgency="urgent!")
        self.assertEqual((requests.calls[0]["headers"]["TTL"], requests.calls[0]["headers"]["Urgency"]),
                         (str(push.TTL_MAX), "normal"))

    def test_notices_open_their_conversation_or_the_task_page(self):
        key, secret = self.subscribe()
        frappe.notices["n1"] = {"user": "a@example.com", "title": "", "text": "Folder A: 3 new files",
                                "session_id": "sesn_0123", "ttl": 3600}
        frappe.notices["n2"] = {"user": "a@example.com", "title": "Brief", "text": "Done", "session_id": "", "ttl": 0}
        self.assertEqual(push.send_notice("n1")["sent"], 1)
        self.assertEqual(push.send_notice("n2")["sent"], 1)
        self.assertEqual(push.send_notice("missing"), {"sent": 0, "gone": 0, "failed": 0})
        first, second = (json.loads(receive(call["data"], key, secret)) for call in requests.calls)
        self.assertEqual(first, {"title": "CC", "body": "Folder A: 3 new files", "url": "/assistant?c=sesn_0123",
                                 "tag": "notice:pnotice_n1"})
        self.assertEqual(second["url"], "/assistant?view=tasks")
        self.assertEqual(second["title"], "Brief")
        self.assertEqual([call["headers"]["TTL"] for call in requests.calls], ["3600", str(push.DEFAULT_TTL)])


class ApiTests(unittest.TestCase):
    def test_push_endpoints_need_sign_in_and_keep_reads_and_writes_apart(self):
        tree = ast.parse((CORE / "assistant/api.py").read_text(encoding="utf-8"))
        found = {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("push_"):
                [decorator] = node.decorator_list
                keywords = {k.arg: ast.literal_eval(k.value) for k in decorator.keywords}
                found[node.name] = (keywords, [a.arg for a in node.args.args])
        self.assertEqual(found, {"push_key": ({"methods": ["GET"]}, []),
                                 "push_subscribe": ({"methods": ["POST"]}, ["endpoint", "p256dh", "auth"]),
                                 "push_unsubscribe": ({"methods": ["POST"]}, ["endpoint"])})
        source = (CORE / "assistant/api.py").read_text(encoding="utf-8").split("def push_subscribe(", 1)[1]
        self.assertIn("except contracts.ContractError:", source)
        self.assertIn('frappe.throw(_("This device cannot receive notifications."))', source)
        rows = (CORE / "i18n/messages.tsv").read_text(encoding="utf-8").splitlines()
        [row] = [r.split("\t") for r in rows if r.startswith("This device cannot receive notifications.\t")]
        self.assertTrue(all(row[1:4]), row)

    def test_the_shared_layer_stays_vendor_neutral_and_the_crypto_module_pure(self):
        for name in ("webpush.py", "push.py"):
            source = (CORE / "assistant" / name).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"anthropic|sk-ant|claude-", source, re.I), name)
        source = (CORE / "assistant/webpush.py").read_text(encoding="utf-8")
        imported = {n.names[0].name.split(".")[0] if isinstance(n, ast.Import) else n.module.split(".")[0]
                    for n in ast.walk(ast.parse(source)) if isinstance(n, (ast.Import, ast.ImportFrom))}
        self.assertEqual(imported - set(sys.stdlib_module_names), {"cryptography"})


if __name__ == "__main__":
    unittest.main()
