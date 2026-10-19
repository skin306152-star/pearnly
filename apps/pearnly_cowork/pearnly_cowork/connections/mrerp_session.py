"""MR.ERP 登录会话：账套目录采集（只读）与发票推送（导入）共用这一份。

来源（旧线上最后版本 `ec2e5c957e6b`）：`services/erp/mrerp_http/session.py` —— 必须先 GET 登录页才建
PHPSESSID；checklogin 失败也回 200，真假只有进 mainmenu 才验得出；idus（内部用户号）在业务页的
hidden input 里，登录选账套后抓一次；业务请求遇 401/403 = 会话失效。`services/erp/ssrf_guard.py` ——
用户可填的系统地址只许解析到公网。

与旧版的差异：跳转不许离开原站点（同采集器）；错误统一成带码的 `SessionError`
（`ERR_AUTH` 凭据/会话 · `ERR_TECHNICAL` 网络/5xx · `ERR_URL_BLOCKED` 非公网地址），调用方各自映射。
已知残留同旧版：DNS rebinding 不在本层覆盖。
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit

import requests

from . import model, mrerp_catalog

TIMEOUT_SECONDS = 30
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
_LOGIN_BOUNCE = ("checklogin", "/login/index", "/login/?", "/login.php", "/index.php")
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
        model.validate_url(url)
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        infos = socket.getaddrinfo(host, None)
    except (model.ConnectionError, ValueError):
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
        return response

    @staticmethod
    def _bounced(response):
        if any(marker in response.url.lower() for marker in _LOGIN_BOUNCE):
            return True
        body = (response.text or "")[:6000].lower()
        return "txtpasswords" in body or "txtusers" in body

    def login(self):
        # 老 PHP 站：必须先 GET 一次登录页才建会话；checklogin 失败也回 200，真假留给选账套验。
        self._send("GET", "/")
        self._send("GET", "login/login.php")
        self._send("POST", "login/checklogin.php",
                   data={"txtusers": self._username, "txtpasswords": self._password, "btnsubmit": "Submit"},
                   headers={"Referer": f"{self.base}/", "Origin": self.base})
        if not self.http.cookies.get("PHPSESSID"):
            raise SessionError("ERR_AUTH", "no PHPSESSID after checklogin")
        self._logged_in = True

    def account_sets(self):
        if not self._logged_in:
            self.login()
        response = self._send("GET", "login/selectdb.php")
        if self._bounced(response):
            raise SessionError("ERR_AUTH", "bounced to login on selectdb")
        return mrerp_catalog.parse_account_sets(response.text)

    def select(self, comidyear, seldb, probe=IDUS_PROBE):
        """进一个账套并抓 idus；被踢回登录页 = 凭据错。返回 idus。"""
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
        """登录 + 选账套（幂等）：让会话就绪可发业务请求。"""
        if not self._selected:
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
