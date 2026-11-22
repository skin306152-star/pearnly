"""MR.ERP 登录会话：账套目录采集（只读）与发票推送（导入）共用这一份。

来源（旧线上最后版本 `ec2e5c957e6b`）：`services/erp/mrerp_http/session.py` —— 必须先 GET 登录页才建
PHPSESSID；checklogin 失败也回 200，真假只有进 mainmenu 才验得出；idus（内部用户号）在业务页的
hidden input 里，登录选账套后抓一次；业务请求遇 401/403 = 会话失效。`services/erp/ssrf_guard.py` ——
用户可填的系统地址只许解析到公网。

与旧版的差异：跳转不许离开原站点（同采集器）；错误统一成带码的 `SessionError`
（`ERR_AUTH` 凭据/会话 · `ERR_ACCOUNT_IN_USE` 复用的会话被踢 · `ERR_TECHNICAL` 网络/5xx · `ERR_URL_BLOCKED` 非公网地址），
调用方各自映射。11-09 风险 7：直写复用已登录的会话（:meth:`MrErpSession.restore`），不每推一次就登录（每登录一次都会踢掉
用户自己浏览器里同一账号）；复用的会话被踢不自动重登，过期才重登一次。
已知残留同旧版：DNS rebinding 不在本层覆盖。
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit

import requests
from pearnly_core.mrerp.urls import validate_url

from pearnly_core.mrerp import catalog as mrerp_catalog

TIMEOUT_SECONDS = 30
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
_LOGIN_BOUNCE = ("checklogin", "/login/index", "/login/?", "/login.php", "/index.php")
# 同一账号在别处登录后，本会话再访问任何页，MR.ERP（`component/php/checkuseruse.php`）先 alert 这句再跳 logoutkick.php：
# 认出它 = 被踢（不是会话过期）。过期 / 没登录是另一句「กรุณา Login เข้าสู่ระบบ」，那种可以重新登录。
KICKED_TEXT = "มีผู้ Login ซ้ำ"
NO_SESSION = "ERR_NO_SESSION"  # 不许登录（:attr:`MrErpSession.may_login`）而缓存里没有能用的会话
_IDUS = (re.compile(r"<input[^>]*\bname=[\"']idus[\"'][^>]*\bvalue=[\"'](\d+)[\"']", re.IGNORECASE),
         re.compile(r"<input[^>]*\bvalue=[\"'](\d+)[\"'][^>]*\bname=[\"']idus[\"']", re.IGNORECASE))
# 选账套后用来抓 idus 的业务页（旧版默认：销项赊账导入页）。
IDUS_PROBE = "impartran/formupload.php?idmenu=370"


def lock_key(system_url, username):
    """同一 MR.ERP 账号一把锁（老 PHP 单账号单会话：并发登录会互踢）。采集与推送共用。"""
    return f"mrerp|{str(system_url or '').strip().rstrip('/').lower()}|{username}"


class SessionError(Exception):
    def __init__(self, code, detail=""):
        self.code = code
        super().__init__(f"{code}: {detail}"[:300])


def public_host(url):
    """系统地址只许解析到公网（挡内网 / 回环 / 链路本地 / 元数据），返回主机名。"""
    try:
        validate_url(url)
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        infos = socket.getaddrinfo(host, None)
    except ValueError:
        raise SessionError("ERR_URL_BLOCKED", url) from None
    except OSError as exc:
        raise SessionError("ERR_TECHNICAL", f"resolve {url}: {exc}") from None
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise SessionError("ERR_URL_BLOCKED", f"non-public address for {host}")
    return host


def scrape_idus(*pages):
    for text in pages:
        for pattern in _IDUS:
            match = pattern.search(text or "")
            if match:
                return match.group(1)
    return None


class MrErpSession:
    """一次 MR.ERP 登录会话（PHPSESSID cookie + idus）。"""

    def __init__(self, url, username, password, *, timeout=TIMEOUT_SECONDS):
        self.base = url.rstrip("/")
        self.host = public_host(self.base)
        self._username, self._password = username, password
        self.timeout = timeout
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": _UA})
        self.http.max_redirects = 5
        self.http.hooks["response"].append(self._same_site)
        self.idus = None
        self._logged_in = False
        self._selected = False
        self._restored = False
        self.kicked = False
        # False：只接着用缓存里的会话，不登录（`account.use(login=False)`：后台探测不为自己新增登录次数，免得把会计踢下线）
        self.may_login = True

    def _same_site(self, response, *args, **kwargs):
        target = urljoin(response.url, response.headers.get("Location", "")) if response.is_redirect else response.url
        if (urlsplit(target).hostname or "").lower().rstrip(".") != self.host:
            raise SessionError("ERR_TECHNICAL", "redirect left the configured MR.ERP site")

    def _send(self, method, path, **kwargs):
        url = path if path.startswith(("http://", "https://")) else f"{self.base}/{path.lstrip('/')}"
        if (urlsplit(url).hostname or "").lower().rstrip(".") != self.host:
            raise SessionError("ERR_TECHNICAL", "request left the configured MR.ERP site")
        kwargs.setdefault("timeout", self.timeout)
        try:
            response = self.http.request(method, url, **kwargs)
        except requests.RequestException as exc:
            raise SessionError("ERR_TECHNICAL", f"{method} {path}: {type(exc).__name__}") from None
        if response.status_code >= 500:
            raise SessionError("ERR_TECHNICAL", f"{method} {path}: status {response.status_code}")
        if KICKED_TEXT in (response.text or "")[:4000]:
            self.kicked, self._logged_in, self._selected = True, False, False
            raise SessionError("ERR_ACCOUNT_IN_USE", f"{method} {path}: signed in elsewhere")
        return response

    def restore(self, cookie) -> bool:
        """接着用上一次登录留下的会话（PHPSESSID）：不再 POST 登录；选账套时被踢回登录页（过期）才重登一次。"""
        if not cookie:
            return False
        self.http.cookies.set("PHPSESSID", str(cookie), domain=self.host)
        self._logged_in = self._restored = True
        return True

    def cookie(self) -> str:
        """现在这个会话的 PHPSESSID（存起来下次接着用）；被踢过的不给。"""
        if self.kicked or not self._logged_in:
            return ""
        return str(self.http.cookies.get("PHPSESSID") or "")

    @staticmethod
    def _bounced(response):
        if any(marker in response.url.lower() for marker in _LOGIN_BOUNCE):
            return True
        body = (response.text or "")[:6000].lower()
        return "txtpasswords" in body or "txtusers" in body

    def login(self):
        if not self.may_login:
            raise SessionError(NO_SESSION, "no warm session and logging in is not allowed here")
        # 老 PHP 站：必须先 GET 一次登录页才建会话；checklogin 失败也回 200，真假留给选账套验。
        self._send("GET", "/")
        self._send("GET", "login/login.php")
        self._send("POST", "login/checklogin.php",
                   data={"txtusers": self._username, "txtpasswords": self._password, "btnsubmit": "Submit"},
                   headers={"Referer": f"{self.base}/", "Origin": self.base})
        if not self.http.cookies.get("PHPSESSID"):
            raise SessionError("ERR_AUTH", "no PHPSESSID after checklogin")
        self._logged_in = True

    def _once_more_if_expired(self, call):
        """接着用的会话（:meth:`restore`）过期了（被跳回登录页、不是被踢）：清掉 cookie 重登一次再做；被踢
        （:data:`KICKED_TEXT`）照样抛 ``ERR_ACCOUNT_IN_USE``，不重登。推送、读菜单 / 主档、目录采集同一条（11-11）。"""
        try:
            return call()
        except SessionError as error:
            if not (self._restored and error.code == "ERR_AUTH"):
                raise
            self._restored = self._logged_in = self._selected = False
            self.http.cookies.clear()
            return call()

    def account_sets(self):
        return self._once_more_if_expired(self._account_sets)

    def _account_sets(self):
        if not self._logged_in:
            self.login()
        response = self._send("GET", "login/selectdb.php")
        if self._bounced(response):
            raise SessionError("ERR_AUTH", "bounced to login on selectdb")
        return mrerp_catalog.parse_account_sets(response.text)

    def select(self, comidyear, seldb, probe=IDUS_PROBE):
        """进一个账套并抓 idus；被踢回登录页 = 凭据错（接着用的会话过期只重登一次）。返回 idus。"""
        return self._once_more_if_expired(lambda: self._select(comidyear, seldb, probe))

    def _select(self, comidyear, seldb, probe=IDUS_PROBE):
        if not self._logged_in:
            self.login()
        self._send("GET", "login/selectdb.php")
        response = self._send("GET", "login/mainmenu.php", params={"comidyear": comidyear, "seldb": seldb},
                              headers={"Referer": f"{self.base}/login/selectdb.php"})
        if response.status_code != 200:
            raise SessionError("ERR_TECHNICAL", f"select status {response.status_code}")
        if self._bounced(response):
            raise SessionError("ERR_AUTH", "credentials rejected on mainmenu")
        probe_page = self._send("GET", probe, headers={"Referer": f"{self.base}/login/mainmenu.php"})
        idus = scrape_idus(probe_page.text, response.text)
        if not idus:
            raise SessionError("ERR_AUTH", "cannot find idus after selecting an account set")
        self.idus, self._selected = idus, True
        return idus

    def switch(self, comidyear, seldb):
        """切进另一个年度库（同网页在选公司页点那一年）：只走选公司页 → mainmenu，不抓 idus（读期间、税号用不到）。
        被踢回登录页 = 会话失效。"""
        self._send("GET", "login/selectdb.php")
        response = self._send("GET", "login/mainmenu.php", params={"comidyear": comidyear, "seldb": seldb},
                              headers={"Referer": f"{self.base}/login/selectdb.php"})
        if response.status_code != 200 or self._bounced(response):
            raise SessionError("ERR_AUTH", "bounced to login while switching account sets")

    def prepare(self, comidyear, seldb, probe=IDUS_PROBE):
        """登录 + 选账套（幂等）：让会话就绪可发业务请求。接着用的会话过期了（被跳回登录页、不是被踢）只重登一次；
        被踢（:data:`KICKED_TEXT`）照样抛 ``ERR_ACCOUNT_IN_USE``，不重登。"""
        if self._selected:
            return
        self.select(comidyear, seldb, probe)

    def request(self, method, path, **kwargs):
        """业务请求；401/403 = 会话失效（要重新登录）。"""
        response = self._send(method, path, **kwargs)
        if response.status_code in (401, 403):
            self._logged_in = self._selected = False
            raise SessionError("ERR_AUTH", f"session expired ({response.status_code}) on {path}")
        return response

    def close(self):
        self.http.close()
