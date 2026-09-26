"""MR.ERP 目录采集的对外读取部分：假 HTTP，不连真实 MR.ERP、不碰数据库。

对照旧线上 `ec2e5c957e6b`：`mrerp_http/session.py`（登录三步、选公司页、进账套抓 idus、
被踢回登录页＝账号错）、`erp_mrerp_listing.test_mrerp_endpoint`（永不抛、错误码）、
`mrerp_target_projection._run_live`（网络类失败重试一次）、`ssrf_guard`（只许公网）。
"""
import contextlib
import socket
import unittest
from unittest.mock import patch

try:
    import requests
    from pearnly_cowork.connections import mrerp_collector as collector
except ImportError as exc:  # 需要 Frappe 环境（bench 的 Python）
    raise unittest.SkipTest(f"needs the Frappe bench environment: {exc}")

BASE = "https://www.mrerp4sme.com"
SELECTDB = ('<table><tr><td>TEST</td><td>บริษัท ทดสอบ</td><td></td><td>'
            '<a href="mainmenu.php?comidyear=6&seldb=1">TEST2019</a>'
            '<a href="mainmenu.php?comidyear=15&seldb=1">TEST2020</a></td></tr></table>')
LOGIN_PAGE = '<form><input name="txtusers"><input name="txtpasswords"></form>'
PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


def response(url, text="", status=200, headers=None):
    r = requests.Response()
    r.url, r.status_code, r._content = url, status, text.encode()
    r.headers.update(headers or {})
    r.encoding = "utf-8"
    return r


class FakeSite:
    """按路径回页面；记录请求；`password` 不对时进账套被踢回登录页。"""

    def __init__(self, password="test01", pages=None):
        self.password, self.calls, self.pages = password, [], pages or {}
        self.accepted = False

    def __call__(self, session, method, url, **kwargs):
        self.calls.append((method, url.replace(BASE, "")))
        path = url.replace(BASE, "")
        if path.startswith("/login/checklogin.php"):
            session.cookies.set("PHPSESSID", "abc")
            self.accepted = kwargs["data"]["txtpasswords"] == self.password
            return response(url)
        if path in self.pages:
            return self.pages[path](url, kwargs)
        if path.startswith("/login/selectdb.php"):
            return response(url, SELECTDB)
        if path.startswith("/login/mainmenu.php"):
            if not self.accepted:
                return response(BASE + "/login/login.php", LOGIN_PAGE)
            return response(url, "<p>menu</p>")
        if path.startswith("/impartran/formupload.php"):
            return response(url, '<input type="hidden" name="idus" id="idus" value="15">')
        return response(url, "<html></html>")


def run(site, lease=None):
    lease = lease or {"system_url": BASE, "username": "test01", "password": "test01"}

    def send(session, method, url, **kwargs):
        r = site(session, method, url, **kwargs)
        for hook in session.hooks["response"]:
            hook(r)
        return r

    # 账号锁走 Redis（站点环境）；这里只测对外读取，锁换成立即拿到。
    with patch.object(requests.Session, "request", autospec=True, side_effect=send), \
            patch.object(socket, "getaddrinfo", return_value=PUBLIC), \
            patch.object(collector.time, "sleep"), \
            patch.object(collector, "account_lock", lambda *a, **k: contextlib.nullcontext(True)):
        return collector.collect(lease)


class Collect(unittest.TestCase):
    def test_collection_waits_for_the_same_account_lock_as_push(self):
        taken = []

        @contextlib.contextmanager
        def lock(key, timeout_sec):
            taken.append(key)
            yield True

        with patch.object(collector, "account_lock", lock), patch.object(collector, "_collect_locked",
                                                                         return_value={"rows": []}):
            collector._collect_once({"system_url": BASE + "/", "username": "test01", "password": "x"})
        self.assertEqual(taken, ["mrerp|https://www.mrerp4sme.com|test01"])

    def test_busy_account_is_a_retryable_technical_error(self):
        from pearnly_core.session_lock import SessionLockUnavailable

        @contextlib.contextmanager
        def busy(key, timeout_sec):
            raise SessionLockUnavailable("busy")
            yield

        with patch.object(collector, "account_lock", busy):
            self.assertEqual(collector._collect_once({"system_url": BASE, "username": "u", "password": "p"}),
                             {"error_code": "ERR_TECHNICAL"})

    def test_login_lists_company_years_and_verifies_by_entering_the_first(self):
        site = FakeSite()
        result = run(site)
        self.assertNotIn("error_code", result)
        self.assertEqual([(r["comidyear"], r["name"], r["company"]) for r in result["rows"]],
                         [("6", "TEST2019", "TEST"), ("15", "TEST2020", "TEST")])
        self.assertEqual([path.split("?")[0] for _, path in site.calls], [
            "/", "/login/login.php", "/login/checklogin.php", "/login/selectdb.php",
            "/login/selectdb.php", "/login/mainmenu.php", "/impartran/formupload.php"])
        self.assertIsNotNone(result["observed_at"].tzinfo)

    def test_wrong_password_is_an_auth_error_not_retried(self):
        site = FakeSite(password="other")
        self.assertEqual(run(site), {"error_code": "ERR_AUTH"})
        self.assertEqual(sum(1 for _, path in site.calls if path == "/login/checklogin.php"), 1)

    def test_missing_credentials_never_call_out(self):
        site = FakeSite()
        self.assertEqual(run(site, {"system_url": BASE, "username": "", "password": "x"}),
                         {"error_code": "ERR_NO_CREDS"})
        self.assertEqual(site.calls, [])

    def test_network_failure_is_retried_once_then_reported(self):
        def down(url, kwargs):
            raise requests.ConnectionError("down")
        site = FakeSite(pages={"/": down})
        self.assertEqual(run(site), {"error_code": "ERR_TECHNICAL"})
        self.assertEqual([path for _, path in site.calls], ["/", "/"])

    def test_server_error_is_technical(self):
        site = FakeSite(pages={"/login/selectdb.php": lambda url, kw: response(url, "boom", 502)})
        self.assertEqual(run(site), {"error_code": "ERR_TECHNICAL"})

    def test_redirect_off_the_configured_site_is_refused(self):
        away = {"/": lambda url, kw: response(url, status=302, headers={"Location": "http://169.254.169.254/"})}
        self.assertEqual(run(FakeSite(pages=away)), {"error_code": "ERR_TECHNICAL"})

    def test_page_without_account_sets_returns_no_rows(self):
        site = FakeSite(pages={"/login/selectdb.php": lambda url, kw: response(url, "<p>none</p>")})
        result = run(site)
        self.assertEqual(result["rows"], [])
        self.assertNotIn("/login/mainmenu.php", [path.split("?")[0] for _, path in site.calls])

    def test_unexpected_exception_is_contained(self):
        def broken(url, kwargs):
            raise RuntimeError("bug")
        self.assertEqual(run(FakeSite(pages={"/login/selectdb.php": broken})), {"error_code": "ERR_UNEXPECTED"})


class PublicHost(unittest.TestCase):
    def test_only_public_addresses_are_allowed(self):
        with patch.object(socket, "getaddrinfo", return_value=PUBLIC):
            self.assertEqual(collector.public_host(BASE + "/"), "www.mrerp4sme.com")
        for address in ("10.0.0.5", "127.0.0.1", "169.254.169.254", "192.168.1.1"):
            private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]
            with patch.object(socket, "getaddrinfo", return_value=private):
                with self.assertRaises(collector.CollectError) as caught:
                    collector.public_host(BASE)
                self.assertEqual(caught.exception.code, "ERR_URL_BLOCKED")

    def test_syntax_guard_runs_before_resolution(self):
        with patch.object(socket, "getaddrinfo") as resolve:
            for url in ("http://localhost:8000", "http://10.1.2.3", "ftp://www.mrerp4sme.com", "http://metadata"):
                with self.assertRaises(collector.CollectError):
                    collector.public_host(url)
            resolve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
