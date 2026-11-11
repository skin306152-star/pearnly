"""Cowork：MR.ERP 登录在 CC 桌面版里授权（ADR-026 第 7、8 条，评估 §14.4，2026-11-02）。要 Frappe 环境（生产镜像里跑，
同 `test_cowork_mrerp_push.py`），不连数据库、不连 Redis、不连真实 MR.ERP：

- 写进连接（`connections.mrerp_login`）：只有能管这条连接、或在使用名单上的人能改；只写两个 Password 字段与授权记录、不升版本；
  页面看到的「已授权：用户名 · 电脑 · 时间」不含密码；
- **老连接兼容**：没经过 CC 授权、只有网页上存的密码的连接，直写取凭据（`service.credentials_for_use`）照样拿到原来的密码；
  在 CC 里重新授权后拿到新密码；
- 机器方法（`companion.credential_api.mrerp_login`）：票只给发票时那台电脑；回给电脑的不含密码；权限、票、报文的问题回码，
  意外出错只回「忙」且不带密码；
- **同账号互斥**（`session_lock` 的代录租约，与直写短锁同一把）：代录租约占着时直写不领、照旧待发送；租约过期（电脑不在）后
  直写恢复、短锁拿得到；直写正占着时代录租不到；
- 假 MR.ERP 站点上用授权后的凭据登录成功，错误密码只登录一次（不重试，防锁账号）。
"""
import contextlib
import pathlib
import socket
import sys
import time
import types
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import connection_doubles  # noqa: E402

try:
    import frappe
    import requests
    from pearnly_core import session_lock
    from pearnly_core.cc_desktop import login_tickets
    from pearnly_core.mrerp.session import MrErpSession, SessionError, lock_key
    from pearnly_cowork.companion import credential_api
    from pearnly_cowork.connections import mrerp_login, service
    from pearnly_cowork.push.mrerp import runner
except ImportError as exc:  # 需要 Frappe 环境（bench 的 Python）
    raise unittest.SkipTest(f"needs the Frappe bench environment: {exc}")

PASSWORD = "S3cret-pass!"
NEW_PASSWORD = "N3w-pass!"
URL = "https://www.mrerp4sme.com"


class Doc(dict):
    """连接行的替身：Password 字段写进同一份「加密存储」（``secrets``），文档上只留 *****（同 Frappe）。"""

    def __init__(self, secrets, **values):
        super().__init__(values)
        self.secrets = secrets
        self.writes = 0

    def __getattr__(self, key):
        return self.get(key)

    def set(self, key, value):
        self[key] = value

    def save_secrets(self):
        for key in ("credential_username", "credential_password"):
            value = self.get(key)
            if value and set(str(value)) != {"*"}:
                self.secrets[key] = value
                self[key] = "*****"
        self.writes += 1


def _throw(message, exc=None):
    raise (exc or frappe.ValidationError)(message)


class Framework(unittest.TestCase):
    """不连库、不连 Redis：翻译、弹窗、消息、请求上下文换成最小的替身。"""

    def setUp(self):
        frappe.local.flags = frappe._dict()
        frappe.local.message_log = []
        frappe.local.form_dict = frappe._dict()
        frappe.local.site = "testcowork.localhost"
        for module in (frappe, session_lock, credential_api.http_auth):
            self.enterContext(patch.object(module, "_", lambda text, *a, **k: text))
        self.enterContext(patch.object(frappe, "throw", _throw))
        self.enterContext(patch.object(frappe, "clear_messages", lambda: None))


def connection(secrets, **values):
    base = {"name": "CWEC-1", "adapter": "mrerp", "enabled": 1, "record_revision": 7, "workspace": "WS",
            "client": "", "config_json": '{"system_url": "%s"}' % URL, "allowed_users": '["staff@x"]'}
    base.update(values)
    return Doc(secrets, **base)


class Store(Framework):
    def setUp(self):
        super().setUp()
        self.secrets = {"credential_username": "old-user", "credential_password": PASSWORD}
        self.doc = connection(self.secrets, credential_username="*****", credential_password="*****")
        self.enterContext(patch.object(mrerp_login, "_read", lambda doctype, name, lock=True: self.doc))
        self.enterContext(patch.object(mrerp_login.access, "write", lambda doc: doc.save_secrets()))
        self.enterContext(patch.object(mrerp_login, "get_decrypted_password",
                                       lambda doctype, name, field, raise_exception=False: self.secrets.get(field)))
        self.managers, self.members = {"boss@x"}, {"staff@x"}

        def require(doc, *, actor=None, management=False, enabled=True, lock=True):
            if actor in (self.managers if management else self.members):
                return actor
            raise frappe.PermissionError

        self.enterContext(patch.object(mrerp_login.access, "require_connection", require))
        self.enterContext(patch.object(mrerp_login.access, "deny", MagicMock(side_effect=frappe.PermissionError)))

    def test_managers_write_the_site_members_keep_it_local_others_get_nothing(self):
        self.assertEqual(mrerp_login.scope_of(self.doc, "boss@x"), "site")
        self.assertEqual(mrerp_login.scope_of(self.doc, "staff@x"), "local")
        with self.assertRaises(frappe.PermissionError):
            mrerp_login.scope_of(self.doc, "stranger@x")
        with self.assertRaises(frappe.PermissionError):
            mrerp_login.scope_of(connection({}, adapter="express"), "boss@x")

    def test_store_writes_only_the_login_and_its_record_and_keeps_the_revision(self):
        result = mrerp_login.store("CWEC-1", "test01", NEW_PASSWORD, actor="boss@x", device_label="ACC-PC")
        self.assertTrue(result["written"])
        self.assertEqual(self.secrets, {"credential_username": "test01", "credential_password": NEW_PASSWORD})
        self.assertEqual(self.doc["record_revision"], 7)
        self.assertEqual((self.doc["credential_authorized_by"], self.doc["credential_device"]), ("boss@x", "ACC-PC"))
        self.assertEqual((result["connection"], result["system_url"], result["username"]), ("CWEC-1", URL, "test01"))
        self.assertTrue(result["at"].endswith("+00:00"))
        self.assertNotIn(NEW_PASSWORD, repr(result))

    def test_a_member_is_refused_the_site_but_may_keep_it_local(self):
        with self.assertRaises(frappe.PermissionError):
            mrerp_login.store("CWEC-1", "own", NEW_PASSWORD, actor="staff@x", device_label="PC", scope="site")
        result = mrerp_login.store("CWEC-1", "own", NEW_PASSWORD, actor="staff@x", device_label="PC", scope="local")
        self.assertFalse(result["written"])
        self.assertEqual(result["system_url"], URL)
        self.assertEqual(self.secrets["credential_password"], PASSWORD)
        self.assertEqual(self.doc.writes, 0)
        self.assertIsNone(self.doc.get("credential_authorized_by"))
        self.assertNotIn(NEW_PASSWORD, repr(result))

    def test_store_rejects_strangers_and_bad_input_without_writing(self):
        for scope in ("site", "local"):
            with self.assertRaises(frappe.PermissionError):
                mrerp_login.store("CWEC-1", "test01", NEW_PASSWORD, actor="stranger@x", device_label="PC", scope=scope)
        for username, password in ((" test01", NEW_PASSWORD), ("test01", "*****"), ("test01", ""), ("a\nb", "x")):
            with self.subTest(username=username), self.assertRaises(frappe.ValidationError) as caught:
                mrerp_login.store("CWEC-1", username, password, actor="boss@x", device_label="PC")
            self.assertNotIn(NEW_PASSWORD, str(caught.exception))
        self.assertEqual(self.secrets["credential_password"], PASSWORD)

    def test_the_page_view_has_user_computer_and_time_never_the_password(self):
        self.assertEqual(mrerp_login.view(self.doc), {"username": "old-user", "device": "", "at": ""})
        mrerp_login.store("CWEC-1", "test01", NEW_PASSWORD, actor="boss@x", device_label="ACC-PC")
        shown = mrerp_login.view(self.doc)
        self.assertEqual((shown["username"], shown["device"]), ("test01", "ACC-PC"))
        self.assertNotIn(NEW_PASSWORD, repr(shown))
        self.assertIsNone(mrerp_login.view(connection({})))
        self.assertIsNone(mrerp_login.view(connection({}, adapter="express", credential_username="*****")))

    def test_an_old_connection_keeps_logging_in_and_reauthorizing_replaces_it(self):
        """老连接没经过 CC 授权：直写、目录采集、主档读取取凭据的同一个入口照样拿到原来的密码；CC 里重新授权就覆盖。"""
        loaded = service.LoadedConnection(self.doc, {}, types.SimpleNamespace(user_id="staff@x"), None, 7)
        self.enterContext(patch.object(service, "read_for_push", connection_doubles.read_for_push_returning(loaded)))
        self.enterContext(patch.object(service, "get_decrypted_password",
                                       lambda doctype, name, field, raise_exception=False: self.secrets.get(field)))
        old = service.credentials_for_use(loaded)
        self.assertEqual((old["username"], old["password"]), ("old-user", PASSWORD))
        mrerp_login.store("CWEC-1", "test01", NEW_PASSWORD, actor="boss@x", device_label="ACC-PC")
        new = service.credentials_for_use(loaded)
        self.assertEqual((new["username"], new["password"]), ("test01", NEW_PASSWORD))


class MachineMethod(Framework):
    def setUp(self):
        super().setUp()
        self.context = types.SimpleNamespace(site="testcowork.localhost", issuer="boss@x")
        self.claim = {"site": "testcowork.localhost", "channel": "cowork", "connection": "CWEC-1",
                      "user": "staff@x", "device": "DEV-1", "scope": "site"}
        self.ticket = login_tickets.new_ticket()
        self.stored, self.noted = [], []
        self.enterContext(patch.object(credential_api.credentials, "login_device", lambda context: "DEV-1"))
        self.enterContext(patch.object(credential_api.devices, "note_mrerp_login",
                                       lambda device, name: self.noted.append((device, name))))
        self.enterContext(patch.object(frappe, "db", types.SimpleNamespace(
            get_value=lambda *a, **k: "ACC-PC", savepoint=lambda point: None, rollback=lambda **k: None)))
        self.enterContext(patch.object(credential_api.login_tickets, "redeem", self.redeem))
        self.redeemed = 0

    def redeem(self, ticket):
        self.redeemed += 1
        return dict(self.claim) if ticket == self.ticket and self.redeemed == 1 else None

    def call(self, body, store=None):
        def default(name, username, password, *, actor, device_label, scope):
            self.stored.append((name, username, password, actor, device_label, scope))
            return {"connection": name, "system_url": URL, "username": username, "device": device_label,
                    "at": "2026-11-02T13:00:00+00:00", "written": scope == "site"}

        frappe.local.form_dict = frappe._dict(body)
        with patch.object(credential_api.http_auth, "current_request", lambda op: (self.context, dict(body))), \
                patch.object(credential_api.logins, "store", store or default):
            return credential_api.mrerp_login()

    def body(self, **values):
        return {"ticket": self.ticket, "username": "test01", "password": PASSWORD, **values}

    def test_the_login_lands_once_and_nothing_comes_back_with_the_password(self):
        result = self.call(self.body())
        self.assertTrue(result["ok"])
        self.assertEqual(result["authorized"]["username"], "test01")
        self.assertNotIn(PASSWORD, repr(result))
        self.assertTrue(result["written"])
        self.assertEqual(self.stored, [("CWEC-1", "test01", PASSWORD, "staff@x", "ACC-PC", "site")])
        self.assertEqual(self.noted, [("DEV-1", "CWEC-1")])
        self.assertNotIn("password", frappe.local.form_dict)
        self.assertEqual(self.call(self.body()), {"ok": False, "code": "ticket_invalid"})

    def test_a_member_ticket_does_not_write_the_site(self):
        self.claim["scope"] = "local"
        result = self.call(self.body())
        self.assertTrue(result["ok"])
        self.assertFalse(result["written"])
        self.assertEqual(self.stored[0][5], "local")
        self.assertNotIn(PASSWORD, repr(result))

    def test_another_computer_cannot_use_the_ticket(self):
        with patch.object(credential_api.credentials, "login_device", lambda context: "DEV-2"):
            self.assertEqual(self.call(self.body()), {"ok": False, "code": "ticket_invalid"})
        self.assertEqual(self.stored, [])

    def test_codes_for_permission_and_bad_input(self):
        def denied(*a, **k):
            raise frappe.PermissionError

        self.assertEqual(self.call(self.body(), store=denied), {"ok": False, "code": "denied"})
        self.redeemed = 0
        self.assertEqual(self.call(self.body(password="")), {"ok": False, "code": "login_invalid"})

    def test_an_unexpected_failure_is_busy_without_the_password(self):
        def broken(*a, **k):
            raise RuntimeError("boom " + PASSWORD)

        logged = []
        logger = types.SimpleNamespace(warning=lambda *args: logged.append(args))
        with patch.object(frappe, "logger", lambda *a, **k: logger), \
                self.assertRaises(frappe.AuthenticationError) as caught:
            self.call(self.body(), store=broken)
        self.assertEqual(caught.exception.http_status_code, 503)
        self.assertNotIn(PASSWORD, str(caught.exception))
        self.assertNotIn(PASSWORD, repr(frappe.local.message_log))
        self.assertTrue(logged)
        self.assertNotIn(PASSWORD, repr(logged))
        self.assertIsNone(caught.exception.__cause__)


class FakeLocks:
    """Redis 锁的替身：同一个键名一份（值、到期时刻），语义同 redis-py 的 Lock（SET NX PX、按值续期 / 释放）。"""

    def __init__(self):
        self.keys, self.now = {}, 1000.0

    def get(self, name):
        found = self.keys.get(name)
        if found and found[1] > self.now:
            return found[0]
        self.keys.pop(name, None)
        return None

    def lock(self, name, timeout=None, blocking_timeout=None, thread_local=True):
        return FakeLock(self, name, timeout)


class FakeLock:
    def __init__(self, store, name, timeout):
        from redis.exceptions import LockError, LockNotOwnedError

        self.store, self.name, self.timeout = store, name, timeout
        self.redis = store
        self.local = types.SimpleNamespace(token=None)
        self.errors = (LockError, LockNotOwnedError)

    def acquire(self, blocking=None, blocking_timeout=None, token=None):
        token = token or b"import-" + str(id(self)).encode()
        if self.store.get(self.name) is not None:
            return False
        self.store.keys[self.name] = (token, self.store.now + self.timeout)
        self.local.token = token
        return True

    def extend(self, additional_time, replace_ttl=False):
        if self.local.token is None:
            raise self.errors[0]("unlocked")
        if self.store.get(self.name) != self.local.token:
            raise self.errors[1]("not owned")
        self.store.keys[self.name] = (self.local.token, self.store.now + additional_time)
        return True

    def release(self):
        if self.local.token is None or self.store.get(self.name) != self.local.token:
            raise self.errors[1]("not owned")
        self.store.keys.pop(self.name)

    def ttl(self, name):
        found = self.store.keys.get(name)
        return int(found[1] - self.store.now) if found else -2


class SameAccount(Framework):
    def setUp(self):
        super().setUp()
        self.locks = FakeLocks()
        self.enterContext(patch.object(session_lock.pearnly_state, "store", lambda: self.locks))
        self.key = lock_key(URL, "test01")

    def test_operator_lease_makes_direct_write_wait_and_expiry_restores_it(self):
        self.assertFalse(runner.account_busy(URL, "test01"))
        self.assertTrue(session_lock.lease_account(self.key, "site:batch-1"))
        self.assertTrue(runner.account_busy(URL, "test01"))
        # 直写的短锁拿不到（同一把）：照原来报「锁不上」，不会并发登录同一个账号
        with self.assertRaises(session_lock.SessionLockUnavailable):
            with session_lock.account_lock(self.key, timeout_sec=0):
                pass
        # 另一个批次租不到，同一个批次续期、重复租都算它自己的
        self.assertFalse(session_lock.lease_account(self.key, "site:batch-2"))
        self.assertTrue(session_lock.lease_account(self.key, "site:batch-1"))
        self.assertTrue(session_lock.renew_account(self.key, "site:batch-1"))
        # 电脑不在、不续期：过期后直写恢复
        self.locks.now += session_lock.OPERATOR_LEASE_SEC + 1
        self.assertFalse(runner.account_busy(URL, "test01"))
        self.assertFalse(session_lock.renew_account(self.key, "site:batch-1"))
        with session_lock.account_lock(self.key, timeout_sec=0) as held:
            self.assertTrue(held)
            # 直写正在导入：代录租不到，也不当成代录占着
            self.assertFalse(session_lock.lease_account(self.key, "site:batch-1"))
            self.assertFalse(runner.account_busy(URL, "test01"))

    def test_release_frees_only_its_own_lease(self):
        self.assertTrue(session_lock.lease_account(self.key, "site:batch-1"))
        self.assertFalse(session_lock.release_account(self.key, "site:batch-2"))
        self.assertTrue(runner.account_busy(URL, "test01"))
        self.assertTrue(session_lock.release_account(self.key, "site:batch-1"))
        self.assertFalse(runner.account_busy(URL, "test01"))
        with self.assertRaises(ValueError):
            session_lock.lease_account(self.key, "")

    def test_the_runner_leaves_the_record_pending_while_the_account_is_busy(self):
        row = frappe._dict(name="CWPR-1", status="pending", result_unknown=0,
                           target_json='{"adapter": "mrerp", "account_set": "15:1"}', actor="staff@x",
                           workspace="WS", client="", history="H", history_version=1, connection="CWEC-1",
                           account_key="k", branch="legacy", binding_generation=0, connection_revision=7)
        loaded = types.SimpleNamespace(endpoint={"config": {"system_url": URL}})
        db = types.SimpleNamespace(get_value=lambda *a, **k: row, rollback=MagicMock(), commit=MagicMock())
        execute = MagicMock(return_value={"status": "success", "docnum": "IV1", "reason": "", "meta": {}, "menus": {}})
        with patch.object(runner.frappe, "db", db), patch.object(runner.frappe, "set_user", lambda user: None), \
                patch.object(runner.ledger, "lease", lambda *a, **k: {"leased": True, "fence": 1, "attempt_token": "t",
                                                                      "payload": {}}), \
                patch.object(runner.ledger, "ack", MagicMock()) as ack, \
                patch.object(runner.ledger, "trusted_adapter_context", lambda *a, **k: None), \
                patch.object(runner.connections, "read_for_push", connection_doubles.read_for_push_returning(loaded)), \
                patch.object(runner.connections, "credentials_for_use",
                             lambda loaded: {"username": "test01", "password": PASSWORD}), \
                patch.object(runner, "execute", execute), patch.object(runner, "dispatch_next", MagicMock()):
            session_lock.lease_account(self.key, "site:batch-1")
            runner.run("CWPR-1")
            execute.assert_not_called()
            ack.assert_not_called()
            db.rollback.assert_called_once_with()
            db.commit.assert_not_called()
            # 租约过期：同一条照常执行
            self.locks.now += session_lock.OPERATOR_LEASE_SEC + 1
            runner.run("CWPR-1")
            execute.assert_called_once()
            ack.assert_called_once()


PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


class FakeMrErp:
    """假 MR.ERP 站点：登录页 → checklogin（只认 test01 与授权时的密码；失败也回 200，同真站点）→ 选账套进 mainmenu
    （密码错就被踢回登录页）→ 业务页带 idus。记下 checklogin 次数。"""

    def __init__(self, password):
        self.password, self.logins, self.good = password, 0, False

    @staticmethod
    def response(url, text=""):
        r = requests.Response()
        r.url, r.status_code, r._content, r.encoding = url, 200, text.encode(), "utf-8"
        return r

    def __call__(self, session, method, url, **kwargs):
        path = url.split("mrerp4sme.com", 1)[1]
        if path.startswith("/login/checklogin"):
            self.logins += 1
            data = kwargs.get("data") or {}
            self.good = data.get("txtusers") == "test01" and data.get("txtpasswords") == self.password
            session.cookies.set("PHPSESSID", "s1")
            return self.response(URL + path, "<html></html>")
        if path.startswith("/login/mainmenu"):
            if not self.good:
                return self.response(URL + "/login/index.php", "<form><input name='txtusers'></form>")
            return self.response(URL + path, "<html>mainmenu</html>")
        if path.startswith("/impartran/"):
            return self.response(URL + path, "<input type='hidden' name='idus' value='15'>")
        return self.response(URL + path, "<html></html>")


class FakeSiteLogin(Framework):
    def select(self, site, password):
        with patch.object(socket, "getaddrinfo", lambda *a, **k: PUBLIC), \
                patch.object(requests.Session, "request",
                             lambda session, method, url, **kw: site(session, method, url, **kw)):
            session = MrErpSession(URL, "test01", password)
            try:
                return session.select("15", "1")
            finally:
                session.close()

    def test_the_authorized_login_works_and_a_wrong_password_is_tried_once(self):
        secrets = {}
        doc = connection(secrets)
        with patch.object(mrerp_login, "_read", lambda *a, **k: doc), \
                patch.object(mrerp_login.access, "write", lambda d: d.save_secrets()), \
                patch.object(mrerp_login, "scope_of", lambda doc, user, lock=True: "site"):
            mrerp_login.store("CWEC-1", "test01", NEW_PASSWORD, actor="staff@x", device_label="PC")
        site = FakeMrErp(NEW_PASSWORD)
        self.assertEqual(self.select(site, secrets["credential_password"]), "15")
        self.assertEqual(site.logins, 1)
        wrong = FakeMrErp("something-else")
        with self.assertRaises(SessionError) as caught:
            self.select(wrong, secrets["credential_password"])
        self.assertEqual(caught.exception.code, "ERR_AUTH")
        self.assertEqual(wrong.logins, 1)
        self.assertNotIn(NEW_PASSWORD, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
