# -*- coding: utf-8 -*-
"""桥本机保存的「云端连接」（评估 docs/work/2026-11-11-express-cloud-assessment.md 第 4.2 节「配对」、第 5 节；
施工短版 2026-11-15-express-cloud-build-assessment.md 第 13 节步 G）。

一份单独的文件 ``config.CLOUD_LINK_FILE``（``%APPDATA%\\Pearnly\\cloud_link.json``）：只有连了云端才有，没连的电脑不读不写，
现有的 ``companion.json``（``ProfileStore``）一个字节都不动。一个桥最多一条云端连接（结构上是列表，先只许一条）。

每条连接存：
- 主机、端口、用户名、域（不是秘密；**不含密码**）；云端主机名（电脑名，登记时作设备名）；经哪台电脑（本机电脑名）；
- 为云端那台电脑生成的**设备密钥**：格式与 ``device_link.new_device_key`` 相同，用同一个 ``TokenProtector``
  （当前 Windows 用户的 DPAPI）加密后存，同 ``ProfileStore.device_key``；
- 各站点登记后拿到的**令牌**（``exp_`` / ``pcc_``，同一台桥可能同时连 Cowork 与 ERP，云端那份 CC 也就有两个成员，第 5 节）：
  同 ``ProfileRecord.token_ciphertext`` 的保护方式，只存桥上；隧道转发（``tunnel_relay``）代发时换上。设备密钥和令牌都不落云端磁盘。

云端账号的密码不进这个文件：存本用户凭据管理器的 Generic 凭据 ``Pearnly CC/cloud/<主机>``（复用
``express_operator.credentials`` 的 store / read / forget），只给 ``window.CloudWindow`` 的 ``password_provider`` 用；
另一份给远程桌面控件网络级身份验证用的 ``TERMSRV/<主机>`` 域凭据见 ``credentials``。删除一条连接 = 删配置、设备密钥、令牌
（先照现有「断开连接」的做法拿各令牌请站点吊销，失败不拦），并删这两条本机凭据。

日志、异常、``repr`` 里只出现主机与站点，从不出现密码、令牌、设备密钥（明文或密文）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Optional

from .. import channel as channels
from ..config import CLOUD_LINK_FILE
from ..express_operator import credentials as _creds
from ..logger import get_logger
from ..token_protector import ProfileStoreError, TokenProtector, default_protector
from . import credentials as rdp_credentials
from . import rdp

log = get_logger(__name__)

SCHEMA_VERSION = 1
MAX_LINKS = 1
PASSWORD_PREFIX = "Pearnly CC/cloud/"
_NAME_LIMIT = 60   # 与 queue_client._device_name 同长


class LinkStoreError(RuntimeError):
    """云端连接配置读写失败。文本只写哪一项不对，从不带密码、令牌、设备密钥。"""


class PasswordMissing(LinkStoreError):
    """本机凭据管理器里没有这条云端连接的密码（或不是 Windows）。"""


@dataclass(frozen=True)
class Member:
    """这条云端连接在一个站点登记后的成员：站点（域名）、地址、令牌密文。"""

    site: str
    backend_url: str
    token_cipher: str = field(repr=False)


@dataclass(frozen=True)
class CloudLink:
    host: str
    port: int
    user: str
    domain: str
    device_cipher: str = field(repr=False)
    host_name: str = ""
    via: str = ""
    members: tuple = ()
    tunnel_drive: str = ""   # 上次用的隧道盘符（``host`` 下次优先用它）；旧文件没有这一项，读成空串

    @property
    def device_name(self) -> str:
        """登记时的设备名：云端主机名（电脑名）；还不知道时用主机地址。"""
        return (self.host_name or self.host)[:_NAME_LIMIT]

    def member(self, site: str) -> Optional[Member]:
        site = _site(site)
        return next((item for item in self.members if item.site == site), None)

    def to_dict(self) -> dict:
        raw = {"host": self.host, "port": self.port, "user": self.user, "domain": self.domain,
               "host_name": self.host_name, "via": self.via, "device": {"key": self.device_cipher},
               "members": [{"site": m.site, "backend_url": m.backend_url, "token": m.token_cipher} for m in self.members]}
        if self.tunnel_drive:
            raw["tunnel_drive"] = self.tunnel_drive
        return raw

    @classmethod
    def from_dict(cls, raw) -> "CloudLink":
        if not isinstance(raw, dict):
            raise LinkStoreError("cloud link entry is invalid")
        try:
            host = rdp.host(raw.get("host"))
            port = rdp.port(raw.get("port"))
            user, domain = rdp.split_account(_account(raw.get("user"), raw.get("domain")))
        except rdp.RdpValueError as exc:
            raise LinkStoreError(f"cloud link {exc}") from None
        device = raw.get("device") if isinstance(raw.get("device"), dict) else {}
        cipher = device.get("key") if isinstance(device.get("key"), str) else ""
        if not cipher:
            raise LinkStoreError("cloud link device key is missing")
        members = []
        for item in raw.get("members") or []:
            if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item.get(k)
                                                     for k in ("site", "backend_url", "token")):
                raise LinkStoreError("cloud link member is invalid")
            members.append(Member(_site(item["site"]), item["backend_url"], item["token"]))
        return cls(host=host, port=port, user=user, domain=domain, device_cipher=cipher,
                   host_name=_label(raw.get("host_name")), via=_label(raw.get("via")), members=tuple(members),
                   tunnel_drive=_drive(raw.get("tunnel_drive")))


def _account(user, domain) -> str:
    user, domain = str(user or ""), str(domain or "")
    return domain + "\\" + user if domain else user


def _label(value) -> str:
    """电脑名一类的显示名：去掉控制字符，截到 60 个字符。"""
    return "".join(ch for ch in str(value or "") if ch >= " " and ch != "\x7f").strip()[:_NAME_LIMIT]


def _drive(value) -> str:
    """记下的隧道盘符：认不出（没有、不是 D–Z）当没记过，不让一项可选的记录挡住整个文件。"""
    try:
        return rdp.drive(value) if value else ""
    except rdp.RdpValueError:
        return ""


def _site(value) -> str:
    site = str(value or "").strip().lower().rstrip(".")
    if not site or len(site) > 253 or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789.-:" for ch in site):
        raise LinkStoreError("site")
    return site


class LinkStore:
    def __init__(self, path: Path, links: list, protector: TokenProtector) -> None:
        self.path = Path(path)
        self._links = list(links)
        self._protector = protector
        self._lock = threading.RLock()

    def __repr__(self) -> str:
        return f"LinkStore(path={str(self.path)!r}, hosts={[link.host for link in self._links]!r})"

    @classmethod
    def load(cls, path: Optional[Path] = None, protector: Optional[TokenProtector] = None) -> "LinkStore":
        """读配置；文件不在就是空的（**不建文件**：没连云端的电脑磁盘上什么都不多）。"""
        target = Path(path or CLOUD_LINK_FILE)
        cipher = protector or default_protector()
        if not target.exists():
            return cls(target, [], cipher)
        try:
            raw = json.loads(target.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LinkStoreError("cloud link file is not valid JSON") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
            raise LinkStoreError("cloud link file version is not supported")
        links = [CloudLink.from_dict(item) for item in raw.get("links") or []]
        if len({link.host for link in links}) != len(links) or len(links) > MAX_LINKS:
            raise LinkStoreError("cloud link file has too many links")
        return cls(target, links, cipher)

    @property
    def links(self) -> tuple:
        return tuple(self._links)

    def get(self, host: str) -> Optional[CloudLink]:
        try:
            wanted = rdp.host(host)
        except rdp.RdpValueError:
            return None
        return next((link for link in self._links if link.host == wanted), None)

    def _need(self, host: str) -> CloudLink:
        link = self.get(host)
        if link is None:
            raise LinkStoreError("cloud link not found")
        return link

    # ── 连接本身 ────────────────────────────────────────────────────────────────

    def save_link(self, *, host: str, port, user: str, domain: str = "", host_name: str = "", via: str = "") -> CloudLink:
        """新建或改一条云端连接（主机、端口、用户名、域、云端主机名、经哪台电脑）。新建时为云端那台电脑生成一把设备密钥；
        改的时候设备密钥与已登记的令牌照旧。已有另一台主机的连接时抛 :class:`LinkStoreError`（一个桥最多一条）。"""
        try:
            host, port = rdp.host(host), rdp.port(port)
            user, domain = rdp.split_account(_account(user, domain))
        except rdp.RdpValueError as exc:
            raise LinkStoreError(f"cloud link {exc}") from None
        with self._lock:
            current = self.get(host)
            if current is None and len(self._links) >= MAX_LINKS:
                raise LinkStoreError("cloud link limit reached")
            if current is None:
                from ..device_link import new_device_key

                link = CloudLink(host=host, port=port, user=user, domain=domain,
                                 device_cipher=self._protector.protect(new_device_key()),
                                 host_name=_label(host_name), via=_label(via))
            else:
                link = replace(current, port=port, user=user, domain=domain,
                               host_name=_label(host_name) or current.host_name, via=_label(via) or current.via)
            self._put(link)
        log.info("云端连接已保存 · 主机=%s", host)
        return link

    def set_host_name(self, host: str, host_name: str) -> None:
        """云端那份 CC 报上来的电脑名（下次登记作设备名）。"""
        with self._lock:
            self._put(replace(self._need(host), host_name=_label(host_name)))

    def set_tunnel_drive(self, host: str, letter: str) -> None:
        """记下这条连接这次用的隧道盘符（``host.CloudHost`` 起连接时；空串 = 忘掉）。"""
        letter = rdp.drive(letter) if letter else ""
        with self._lock:
            link = self._need(host)
            if link.tunnel_drive != letter:
                self._put(replace(link, tunnel_drive=letter))

    def device_key(self, host: str) -> str:
        """云端那台电脑的设备密钥明文（只交给本站自己的对话页登记用，同 ``device_link.identity``）。"""
        return self._protector.unprotect(self._need(host).device_cipher)

    # ── 各站点的令牌 ─────────────────────────────────────────────────────────────

    def set_token(self, host: str, site: str, backend_url: str, token: str) -> None:
        """对话页登记云端那台电脑后交回的令牌（只认 ``exp_`` / ``pcc_``）：加密存下，同一站点的旧令牌换掉。"""
        token = str(token or "").strip()
        if not channels.known(token) or "@" in token or any(ch.isspace() for ch in token):
            raise LinkStoreError("token shape is invalid")
        backend_url = str(backend_url or "").strip()
        if not backend_url.startswith("https://") and not backend_url.startswith("http://"):
            raise LinkStoreError("backend url is invalid")
        site = _site(site)
        with self._lock:
            link = self._need(host)
            kept = tuple(item for item in link.members if item.site != site)
            self._put(replace(link, members=kept + (Member(site, backend_url, self._protector.protect(token)),)))
        log.info("云端连接登记到站点 · 主机=%s 站点=%s", link.host, site)

    def token(self, host: str, site: str) -> str:
        """这个站点的令牌明文（给隧道转发补 ``Authorization``）；没有回空串。"""
        link = self.get(host)
        member = link.member(site) if link is not None else None
        return self._protector.unprotect(member.token_cipher) if member is not None else ""

    def backend_url(self, host: str, site: str) -> str:
        link = self.get(host)
        member = link.member(site) if link is not None else None
        return member.backend_url if member is not None else ""

    def drop_token(self, host: str, site: str) -> bool:
        """丢掉这个站点的令牌（站点不认了、电脑被移除），下次对话页打开重新登记。丢了回 True。"""
        site = _site(site)
        with self._lock:
            link = self.get(host)
            if link is None or link.member(site) is None:
                return False
            self._put(replace(link, members=tuple(item for item in link.members if item.site != site)))
        log.info("云端连接丢掉站点令牌 · 主机=%s 站点=%s", link.host, site)
        return True

    def pending(self, site: str) -> list:
        """这个站点上还没登记的云端连接（没有这个站点的令牌）。"""
        site = _site(site)
        return [link for link in self._links if link.member(site) is None]

    # ── 删除 ──────────────────────────────────────────────────────────────────

    def remove(self, host: str, *, leave: Optional[Callable[[str, str], bool]] = None) -> bool:
        """删一条云端连接：先拿各站点的令牌请站点吊销（同「断开连接」先 ``device_link.leave``，连不上也照删），再删配置
        （设备密钥、令牌随之没了），最后删本机两条凭据（``TERMSRV/<主机>`` 与 ``Pearnly CC/cloud/<主机>``）。没有这条回 False。"""
        if leave is None:
            from ..device_link import leave as leave_site

            leave = leave_site
        with self._lock:
            link = self.get(host)
            if link is None:
                return False
            for member in link.members:
                try:
                    leave(member.backend_url, self._protector.unprotect(member.token_cipher))
                except Exception as exc:
                    log.info("删云端连接时吊销令牌失败（照删本机） · 站点=%s: %s", member.site, type(exc).__name__)
            self._links = [item for item in self._links if item.host != link.host]
            self._write()
        for name, forget in (("TERMSRV", rdp_credentials.forget), ("password", forget_password)):
            try:
                forget(link.host)
            except Exception as exc:
                log.warning("删云端连接的本机凭据失败 · %s · 主机=%s: %s", name, link.host, type(exc).__name__)
        log.info("云端连接已删除 · 主机=%s", link.host)
        return True

    # ── 写文件 ────────────────────────────────────────────────────────────────

    def _put(self, link: CloudLink) -> None:
        links = [item for item in self._links if item.host != link.host] + [link]
        previous, self._links = self._links, links
        try:
            self._write()
        except Exception:
            self._links = previous
            raise

    def _write(self) -> None:
        """原子写（临时文件 → 回读核对能解开 → 换上），同 ``ProfileStore._write_locked``；没有连接了就删掉文件。"""
        if not self._links:
            try:
                self.path.unlink(missing_ok=True)
            except OSError as exc:
                raise LinkStoreError("cloud link file could not be removed") from exc
            return
        document = {"schema_version": SCHEMA_VERSION, "links": [link.to_dict() for link in self._links]}
        encoded = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent))
        temp_path = Path(temp_name)
        try:
            try:
                os.chmod(temp_path, 0o600)
            except OSError:
                pass
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            check = json.loads(temp_path.read_text(encoding="utf-8"))
            for item in check.get("links") or []:
                parsed = CloudLink.from_dict(item)
                if not self._protector.unprotect(parsed.device_cipher):
                    raise LinkStoreError("cloud link device key verification failed")
                for member in parsed.members:
                    if not self._protector.unprotect(member.token_cipher):
                        raise LinkStoreError("cloud link token verification failed")
            os.replace(temp_path, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except ProfileStoreError as exc:
            temp_path.unlink(missing_ok=True)
            raise LinkStoreError("cloud link protection failed") from exc
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise


# ── 云端账号的密码（本机凭据管理器，Generic） ─────────────────────────────────────────

def password_target(host: str) -> str:
    """密码凭据的目标名 ``Pearnly CC/cloud/<主机>``（主机按 ``rdp.host`` 校验、转小写）。"""
    try:
        return PASSWORD_PREFIX + rdp.host(host)
    except rdp.RdpValueError as exc:
        raise LinkStoreError(f"cloud link {exc}") from None


def store_password(host: str, user: str, domain: str, password: str) -> None:
    """用户本人在 CC 自己的界面输的云端密码：存进本机凭据管理器（``CRED_PERSIST_LOCAL_MACHINE``，DPAPI），不写文件、
    不传服务器、不进日志。失败抛 ``OSError(错误码, "CredWriteW")``；不是 Windows 抛 ``credentials.NotWindows``。"""
    name = password_target(host)
    account = rdp_credentials.credential_user(user, domain)
    if sys.platform != "win32":
        raise rdp_credentials.NotWindows("cloud_link.link_store needs Windows")
    if not isinstance(password, str) or not password:
        raise ValueError("password")
    _creds.store(name, account, password)


def has_password(host: str) -> bool:
    return bool(_creds.user_name(password_target(host)))


def password_provider(host: str) -> Callable[[], str]:
    """给 ``window.CloudWindow(password_provider=…)``：每次起连接时取一次本机存的密码；没有抛 :class:`PasswordMissing`
    （文本不带任何账号信息）。"""
    name = password_target(host)

    def provide() -> str:
        found = _creds.read(name)
        if not found or not found[1]:
            raise PasswordMissing("cloud link password is not stored")
        return found[1]

    return provide


def forget_password(host: str) -> bool:
    return bool(_creds.forget(password_target(host)))


__all__ = ["CloudLink", "LinkStore", "LinkStoreError", "MAX_LINKS", "Member", "PASSWORD_PREFIX", "PasswordMissing",
           "forget_password", "has_password", "password_provider", "password_target", "store_password"]
