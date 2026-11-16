# -*- coding: utf-8 -*-
"""CC「设置 → 连接」里「云端 Express」一行背后的桥动作（用户 11-16 同意的示意；评估第 4.2 节、施工短版步 E）。

``tomo_bridge`` 只在对话页调 ``cloud_prefill`` / ``cloud_connect`` / ``cloud_disconnect`` / ``cloud_status`` 时按需导入本模块：

- ``prefill``：连接弹窗的预填。已存过云端连接就回那条的主机、端口、用户名；没有就读本机 TSplus 留下的 ``Session.rdp``
  （``rdp.find_session_rdp`` / ``read_session_rdp``，只取主机、端口；用户名那个文件按设计不读），读不到就空着让人填。
  **不回任何密码**，也不回域（域只在桥里用，作用户只填用户名时的默认域）。
- ``connect``：地址来自设置页的连接弹窗，账号和密码来自桥弹的 CC 原生小窗（``login_dialog.ask``，同 MR.ERP 授权，ADR-026
  第 7 条：网页不收密码；页面上填的账号只作小窗的预填）。密码只存进本机凭据管理器两处——Generic ``Pearnly CC/cloud/<主机>``
  （``link_store.store_password``，给连接窗口的 ``password_provider``）与域凭据 ``TERMSRV/<主机>``（``credentials.store``，
  网络级身份验证用）；不写文件、不传服务器、不进日志、不回页面。然后存连接配置（新连接生成云端那台电脑的设备密钥）。
  换了另一台主机：先删旧的那条（吊销令牌、删两条本机凭据），一个桥只有一条。本函数只存、回「未连接」；存好后由
  ``tomo_bridge`` 在界面线程起这条连接的宿主（``host.CloudHost.start``：隧道盘、会话交接、中转、连接窗口），回包里的状态
  换成宿主会话的实况。起不来（不是 Windows、没有 PySide6 或 comtypes、映射不上隧道盘）照样存着、回「未连接」。
  CC 启动时就算存有云端连接也不自动连，等人再点「连接」。
- ``disconnect``：``tomo_bridge`` 先停宿主（关窗即断开、停中转、删会话交接、撤隧道盘）；这里有会话再按人断开一次
  （``CloudSession.user_close``，不重连），再 ``LinkStore.remove``（吊销令牌、删配置与两条凭据）。
- ``status``：连接（主机、端口、用户名、云端电脑名）与状态词；状态来自宿主窗口的 ``session.CloudSession``，宿主没起（没点过
  「连接」、窗口已关）时是「未连接」。
  ``places`` 是云端那份 CC 报上来的 Express 位置——现在还没有这条数据来源，回空列表（不造数据）。

状态词只有五个：``idle`` 未连接 / ``connecting`` 正在连接 / ``connected`` 已连接 / ``login_failed`` 登录失败 /
``replaced`` 账号在别处使用。登录失败、被挤都停住不自动重连（``session``），这里照实回。等重连（普通断线退避中）算「未连接」：
人点「连接」立刻重连（``CloudSession.connect``）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from ..logger import get_logger
from . import credentials as rdp_credentials
from . import link_store as links
from . import rdp
from .reasons import Outcome
from .session import State

log = get_logger(__name__)

IDLE, CONNECTING, CONNECTED, LOGIN_FAILED, REPLACED = "idle", "connecting", "connected", "login_failed", "replaced"
STATES = (IDLE, CONNECTING, CONNECTED, LOGIN_FAILED, REPLACED)


def status_of(snapshot) -> str:
    """会话快照（``session.Snapshot``）→ 状态词；没有会话（宿主没起、窗口已关）是「未连接」。"""
    if snapshot is None:
        return IDLE
    if snapshot.state is State.CONNECTING:
        return CONNECTING
    if snapshot.state is State.CONNECTED:
        return CONNECTED
    if snapshot.state is State.HALTED:
        return REPLACED if snapshot.halt is Outcome.REPLACED else LOGIN_FAILED
    return IDLE


def _first(store) -> Optional[links.CloudLink]:
    return store.links[0] if store is not None and store.links else None


def _session_target(session_rdp: Optional[Path]) -> Optional[rdp.CloudTarget]:
    if session_rdp is None:
        return None
    try:
        return rdp.read_session_rdp(session_rdp)
    except (OSError, rdp.RdpValueError) as exc:
        log.info("读 Session.rdp 失败（预填留空）: %s", type(exc).__name__)
        return None


def prefill(store, session_rdp: Optional[Path]) -> dict:
    link = _first(store)
    if link is not None:
        return {"ok": True, "host": link.host, "port": link.port, "user": link.user}
    target = _session_target(session_rdp)
    if target is not None:
        return {"ok": True, "host": target.host, "port": target.port, "user": ""}
    return {"ok": True, "host": "", "port": rdp.DEFAULT_PORT, "user": ""}


def _invalid(field: str) -> dict:
    return {"ok": False, "code": "invalid", "field": field}


def check_address(host, port) -> Optional[dict]:
    """弹小窗之前先核地址：主机、端口不合规回 ``{ok: false, code: invalid, field}``，合规回 None。"""
    try:
        rdp.host(host)
    except rdp.RdpValueError:
        return _invalid("host")
    try:
        rdp.port(port if port not in (None, "") else rdp.DEFAULT_PORT)
    except rdp.RdpValueError:
        return _invalid("port")
    return None


def connect(store, *, new_store: Callable[[], object], session_rdp: Optional[Path], host, port, user, password,
            via: str = "") -> dict:
    """存一条云端连接与它的密码。回 ``{ok, state, host}`` 或 ``{ok: false, code[, field]}``，从不回密码。"""
    try:
        host = rdp.host(host)
    except rdp.RdpValueError:
        return _invalid("host")
    try:
        port = rdp.port(port if port not in (None, "") else rdp.DEFAULT_PORT)
    except rdp.RdpValueError:
        return _invalid("port")
    # 只填用户名时的默认域：同一台主机已存的那条，或 Session.rdp 里同一台主机的域
    current = store.get(host) if store is not None else None
    default_domain = current.domain if current is not None else ""
    if current is None:
        target = _session_target(session_rdp)
        if target is not None and target.host == host:
            default_domain = target.domain
    try:
        name, domain = rdp.split_account(str(user or ""), default_domain)
    except rdp.RdpValueError:
        return _invalid("user")
    if not isinstance(password, str) or not password:
        return _invalid("password")
    try:
        links.store_password(host, name, domain, password)
        rdp_credentials.store(host, name, domain, password)
    except rdp_credentials.NotWindows:
        return {"ok": False, "code": "unsupported"}
    except (OSError, ValueError, links.LinkStoreError) as exc:
        log.warning("存云端连接的本机凭据失败 · 主机=%s: %s", host, type(exc).__name__)
        _forget(host)
        return {"ok": False, "code": "credential_failed"}
    finally:
        password = None
    target_store = store if store is not None else new_store()
    try:
        for old in [link for link in target_store.links if link.host != host]:
            target_store.remove(old.host)
        target_store.save_link(host=host, port=port, user=name, domain=domain, via=via)
    except links.LinkStoreError as exc:
        log.warning("存云端连接失败 · 主机=%s: %s", host, exc)
        if target_store.get(host) is None:
            _forget(host)
        return {"ok": False, "code": "save_failed"}
    return {"ok": True, "state": IDLE, "host": host}


def _forget(host: str) -> None:
    for forget in (links.forget_password, rdp_credentials.forget):
        try:
            forget(host)
        except Exception as exc:  # noqa: BLE001 - 清理尽力而为
            log.info("清理云端连接的本机凭据失败 · 主机=%s: %s", host, type(exc).__name__)


def disconnect(store, session=None) -> dict:
    if session is not None:
        try:
            session.user_close()
        except Exception:
            log.exception("断开云端连接会话失败")
    if store is not None:
        for link in list(store.links):
            store.remove(link.host)
    return {"ok": True}


def status(store, session=None) -> dict:
    link = _first(store)
    if link is None:
        return {"ok": True, "link": None, "state": IDLE, "places": []}
    snapshot = session.snapshot() if session is not None else None
    return {"ok": True, "state": status_of(snapshot), "places": [],
            "link": {"host": link.host, "port": link.port, "user": link.user, "name": link.host_name}}


__all__ = ["CONNECTED", "CONNECTING", "IDLE", "LOGIN_FAILED", "REPLACED", "STATES", "check_address", "connect", "disconnect", "prefill",
           "status", "status_of"]
