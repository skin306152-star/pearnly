"""云端 Express 连接窗口的状态机与原因码（``companion.cloud_link.session`` / ``reasons`` / ``window``）。

守住：未连接 → 正在连接 → 已连接；登录失败、被挤停住、不自动重连，点「连接」才再连；普通断线按 1、2、4、8、15 分钟退避，
稳定 10 分钟后清零；人关窗不重连、之后的断开事件不改结果；原因码按微软文档与 11-15 实测映射；
设置控件只经正式属性、密码只进 ClearTextPassword、没有非脚本接口设置就不连；非 Windows 构造窗口抛明确错误。"""
from __future__ import annotations

import sys

import pytest

from companion.cloud_link import reasons, session, window
from companion.cloud_link.reasons import Outcome
from companion.cloud_link.session import CloudSession, State


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Harness:
    def __init__(self):
        self.clock = Clock()
        self.opened = 0
        self.closed = 0
        self.s = CloudSession(self._open, self._close, self.clock)

    def _open(self):
        self.opened += 1

    def _close(self):
        self.closed += 1

    def login(self):
        self.s.on_connected()
        self.s.on_login_complete()


@pytest.fixture
def h():
    return Harness()


# ---------------- reasons ----------------

@pytest.mark.parametrize("disc, ext, expected", [
    (3, 5, Outcome.REPLACED),                 # 11-15 实测：客户端 3 +「已与远程计算机建立了另一个连接」
    (0, 5, Outcome.REPLACED),
    (2055, 0, Outcome.LOGIN_FAILED),          # 11-15 实测：不写域时 2055
    (2567, 0, Outcome.LOGIN_FAILED),
    (2823, 0, Outcome.LOGIN_FAILED),
    (3079, 0, Outcome.LOGIN_FAILED),
    (3335, 0, Outcome.LOGIN_FAILED),
    (3591, 0, Outcome.LOGIN_FAILED),
    (3847, 0, Outcome.LOGIN_FAILED),
    (4615, 0, Outcome.LOGIN_FAILED),
    (5639, 0, Outcome.LOGIN_FAILED),
    (5895, 0, Outcome.LOGIN_FAILED),
    (6151, 0, Outcome.LOGIN_FAILED),
    (8455, 0, Outcome.LOGIN_FAILED),
    (3, 7, Outcome.LOGIN_FAILED),
    (3, 8, Outcome.LOGIN_FAILED),
    (3, 9, Outcome.LOGIN_FAILED),
    (3, 10, Outcome.LOGIN_FAILED),
    (3, 768, Outcome.LOGIN_FAILED),
    (1, 0, Outcome.USER_CLOSED),
    (2, 0, Outcome.USER_CLOSED),
    (3, 1, Outcome.USER_CLOSED),
    (3, 2, Outcome.USER_CLOSED),
    (3, 11, Outcome.USER_CLOSED),
    (3, 12, Outcome.USER_CLOSED),
    (3, 0, Outcome.DROPPED),                  # 服务器断开、没有扩展原因：普通断线
    (3, 3, Outcome.DROPPED),                  # 空闲超时
    (3, 4, Outcome.DROPPED),
    (3, 6, Outcome.DROPPED),
    (3, 266, Outcome.DROPPED),                # 授权类：服务器那边的事，退避重试
    (3, 4500, Outcome.DROPPED),
    (0, 0, Outcome.DROPPED),
    (264, 0, Outcome.DROPPED),
    (516, 0, Outcome.DROPPED),
    (520, 0, Outcome.DROPPED),
    (1796, 0, Outcome.DROPPED),
    (2308, 0, Outcome.DROPPED),
    (6919, 0, Outcome.DROPPED),
    (99999, 0, Outcome.DROPPED),
    (None, None, Outcome.DROPPED),
])
def test_classify_disconnect(disc, ext, expected):
    assert reasons.classify_disconnect(disc, ext) is expected


def test_replaced_wins_over_login_code_and_local_request_wins_over_all():
    assert reasons.classify_disconnect(2055, 5) is Outcome.REPLACED
    for disc, ext in ((3, 5), (2055, 0), (3, 0), (1, 0)):
        assert reasons.classify_disconnect(disc, ext, local_requested=True) is Outcome.USER_CLOSED


@pytest.mark.parametrize("code", [-1, 0, 1, 2, -1073741714, -1073741715, -1073741276,
                                  0xC000006E, 0xC000006D, 0xC0000224, 0xFFFFFFFF])
def test_logon_error_failures(code):
    assert reasons.logon_error_is_failure(code)


@pytest.mark.parametrize("code", [-2, -3, -4, -5, -6, -7, 3, 0xFFFFFFFB, 42])
def test_logon_error_informational(code):
    assert not reasons.logon_error_is_failure(code)


@pytest.mark.parametrize("code", [0, 1, 2, 3, 4, 5, 6, 7, 100])
def test_fatal_is_dropped(code):
    assert reasons.classify_fatal(code) is Outcome.DROPPED


def test_signed32():
    assert reasons.signed32(0xFFFFFFFF) == -1
    assert reasons.signed32(-5) == -5
    assert reasons.signed32(7) == 7


def test_reason_name():
    assert reasons.reason_name(3, 5) == "disconnectReasonByServer/exDiscReasonReplacedByOtherConnection"
    assert reasons.reason_name(2055) == "SSL_ERR_LOGON_FAILURE"
    assert reasons.reason_name(3, 9) == "disconnectReasonByServer/exDiscReasonServerInsufficientPrivileges"
    assert reasons.reason_name(516, 4500) == "disc_516/ext_4500"


# ---------------- session ----------------

def test_retry_delay_series():
    assert [session.retry_delay(i) for i in range(-1, 8)] == [60, 60, 120, 240, 480, 900, 900, 900, 900]


def test_initial_and_connect_flow(h):
    assert h.s.state is State.IDLE and h.opened == 0
    assert h.s.connect() is True
    assert h.s.state is State.CONNECTING and h.opened == 1
    assert h.s.connect() is False and h.opened == 1          # 正在连接时再点不重复起
    h.s.on_connected()
    assert h.s.state is State.CONNECTING                     # 网络通了还没登录完
    h.s.on_login_complete()
    assert h.s.state is State.CONNECTED
    assert h.s.connect() is False and h.opened == 1
    assert h.s.seconds_until_retry() is None


def test_login_complete_ignored_when_not_connecting(h):
    h.s.on_login_complete()
    assert h.s.state is State.IDLE


@pytest.mark.parametrize("disc, ext, halt", [(3, 5, Outcome.REPLACED), (2055, 0, Outcome.LOGIN_FAILED),
                                             (3, 9, Outcome.LOGIN_FAILED)])
def test_halt_no_auto_reconnect(h, disc, ext, halt):
    h.s.connect()
    h.login()
    assert h.s.on_disconnected(disc, ext) is halt
    snap = h.s.snapshot()
    assert snap.state is State.HALTED and snap.halt is halt and snap.retry_at is None
    h.clock.now += 10 * 3600
    assert h.s.tick() is False and h.opened == 1             # 永不自己再连
    assert h.s.seconds_until_retry() is None
    assert h.s.connect() is True and h.opened == 2           # 人点「连接」才连，标记清掉
    assert h.s.snapshot().halt is None and h.s.state is State.CONNECTING


def test_login_failure_during_connecting(h):
    h.s.connect()
    h.s.on_disconnected(2055, 0)
    assert h.s.state is State.HALTED and h.s.snapshot().halt is Outcome.LOGIN_FAILED


def test_logon_error_halts_and_closes(h):
    h.s.connect()
    h.s.on_connected()
    h.s.on_logon_error(0)                                    # 密码不对
    assert h.s.state is State.HALTED and h.s.snapshot().halt is Outcome.LOGIN_FAILED
    assert h.closed == 1
    h.s.on_login_complete()                                  # 之后的事件不改结果
    assert h.s.state is State.HALTED
    h.s.on_disconnected(1, 0)
    assert h.s.state is State.HALTED and h.s.snapshot().halt is Outcome.LOGIN_FAILED
    h.clock.now += 3600
    assert h.s.tick() is False and h.opened == 1


def test_logon_error_informational_does_nothing(h):
    h.s.connect()
    h.s.on_logon_error(-5)                                   # 会话争用框：框给人看，状态不变
    h.s.on_logon_error(3)
    assert h.s.state is State.CONNECTING and h.closed == 0
    h.s.on_logon_error(0)
    h.s.on_logon_error(-5)
    assert h.s.state is State.HALTED


def test_logon_error_ignored_when_idle(h):
    h.s.on_logon_error(0)
    assert h.s.state is State.IDLE and h.closed == 0


def test_dropped_backoff_series(h):
    h.s.connect()
    h.login()
    expected = [60, 120, 240, 480, 900, 900, 900]
    for i, delay in enumerate(expected):
        assert h.s.on_disconnected(3, 0) is Outcome.DROPPED
        assert h.s.state is State.RETRY_WAIT
        assert h.s.seconds_until_retry() == delay
        h.clock.now += delay - 1
        assert h.s.tick() is False
        assert h.s.seconds_until_retry() == 1
        h.clock.now += 1
        assert h.s.tick() is True
        assert h.s.state is State.CONNECTING and h.opened == i + 2
    assert h.s.snapshot().attempt == len(expected)


def test_connect_failures_before_login_keep_backing_off(h):
    h.s.connect()
    h.s.on_disconnected(516, 0)                              # 连不上
    assert h.s.seconds_until_retry() == 60
    h.clock.now += 60
    h.s.tick()
    h.s.on_disconnected(264, 0)
    assert h.s.seconds_until_retry() == 120


def test_backoff_resets_only_after_stable(h):
    h.s.connect()
    h.login()
    h.s.on_disconnected(3, 0)
    h.clock.now += 60
    h.s.tick()
    h.login()
    h.clock.now += session.STABLE_SECONDS - 1                # 连上没满 10 分钟就掉：接着退避
    h.s.on_disconnected(3, 0)
    assert h.s.seconds_until_retry() == 120
    h.clock.now += 120
    h.s.tick()
    h.login()
    h.clock.now += session.STABLE_SECONDS                    # 稳定满 10 分钟：清零
    h.s.on_disconnected(3, 0)
    assert h.s.seconds_until_retry() == 60


def test_user_connect_during_retry_wait_connects_now(h):
    h.s.connect()
    h.login()
    h.s.on_disconnected(3, 0)
    h.s.on_disconnected(3, 0)                                # 已在等，重复的事件不再加
    assert h.s.snapshot().attempt == 1
    assert h.s.connect() is True and h.opened == 2
    assert h.s.snapshot().attempt == 0


def test_fatal_error_backs_off_and_closes(h):
    h.s.connect()
    h.s.on_fatal_error(3)
    assert h.s.state is State.RETRY_WAIT and h.closed == 1
    assert h.s.seconds_until_retry() == 60
    h.s.on_fatal_error(3)                                    # 已不在连：忽略
    assert h.closed == 1


def test_user_close_no_reconnect(h):
    h.s.connect()
    h.login()
    h.s.user_close()
    assert h.s.state is State.IDLE and h.closed == 1
    assert h.s.snapshot().last_outcome is Outcome.USER_CLOSED
    assert h.s.on_disconnected(1, 0) is Outcome.USER_CLOSED  # 控件随后报的断开
    assert h.s.on_disconnected(3, 0) is Outcome.USER_CLOSED
    assert h.s.state is State.IDLE
    h.clock.now += 24 * 3600
    assert h.s.tick() is False and h.opened == 1


def test_user_close_while_waiting_or_halted(h):
    h.s.connect()
    h.s.on_disconnected(3, 0)
    h.s.user_close()
    assert h.s.state is State.IDLE and h.closed == 0         # 控件早已断开，不再调关闭
    h.clock.now += 3600
    assert h.s.tick() is False
    h.s.connect()
    h.s.on_disconnected(3, 5)
    h.s.user_close()
    assert h.s.state is State.IDLE and h.s.snapshot().halt is None


def test_remote_logoff_is_user_close(h):
    h.s.connect()
    h.login()
    assert h.s.on_disconnected(3, 12) is Outcome.USER_CLOSED
    assert h.s.state is State.IDLE
    h.clock.now += 3600
    assert h.s.tick() is False


def test_open_failure_returns_to_idle():
    clock = Clock()

    def boom():
        raise window.NonScriptableUnavailable("x")

    s = CloudSession(boom, lambda: None, clock)
    with pytest.raises(window.NonScriptableUnavailable):
        s.connect()
    assert s.state is State.IDLE and s.snapshot().last_reason == "open_failed_NonScriptableUnavailable"
    clock.now += 3600
    assert s.tick() is False


# ---------------- window：不依赖 Windows 的部分 ----------------

class FakeAx:
    def __init__(self, missing=(), refuse=()):
        self.props = {}
        self.subs = {}
        self.missing = set(missing)
        self.refuse = set(refuse)

    def setProperty(self, name, value):  # noqa: N802
        if name in self.refuse:
            return False
        self.props[name] = value
        return True

    def querySubObject(self, name):  # noqa: N802
        if name in self.missing:
            return None
        return self.subs.setdefault(name, FakeAx(refuse=self.refuse))


PARAMS = window.ConnectParams.make(host="203.151.54.45", port=4490, user="W10E061734-01",
                                   domain="expressoncloud", tunnel_drive="x:")


def test_connect_params_validation():
    assert PARAMS.host == "203.151.54.45" and PARAMS.port == 4490
    assert PARAMS.user == "W10E061734-01" and PARAMS.domain == "expressoncloud" and PARAMS.tunnel_drive == "X"
    with pytest.raises(Exception):
        window.ConnectParams.make(host="bad host\n", port=1, user="u", domain="", tunnel_drive="X")
    with pytest.raises(Exception):
        window.ConnectParams.make(host="h", port=3389, user="u", domain="", tunnel_drive="C")
    with pytest.raises(Exception):
        window.ConnectParams.make(host="h", port=3389, user="u", domain="", tunnel_drive="X", width=100)


def test_apply_settings_properties_and_password_only_in_cleartext():
    ax, calls = FakeAx(), []
    window.apply_settings(ax, PARAMS, "s3cret!", nonscriptable=lambda a, ns: calls.append((a, ns)))
    assert ax.props == {"Server": "203.151.54.45", "UserName": "W10E061734-01", "Domain": "expressoncloud",
                        "DesktopWidth": 1280, "DesktopHeight": 800, "ColorDepth": 16}
    adv = ax.subs["AdvancedSettings9"]
    assert adv.props == {"RDPPort": 4490, "AuthenticationLevel": 0, "EnableAutoReconnect": False,
                         "RedirectClipboard": False, "RedirectPrinters": False, "RedirectPorts": False,
                         "RedirectSmartCards": False, "RedirectDrives": False, "ClearTextPassword": "s3cret!"}
    assert calls == [(ax, window.NonScriptable(enable_credssp=True, drive="X"))]
    leaked = [k for k, v in ax.props.items() if v == "s3cret!"]
    assert leaked == []


def test_apply_settings_falls_back_to_older_advanced_settings():
    ax = FakeAx(missing={"AdvancedSettings9", "AdvancedSettings8"})
    window.apply_settings(ax, PARAMS, "pw", nonscriptable=lambda a, ns: None)
    assert list(ax.subs) == ["AdvancedSettings7"]
    ax = FakeAx(missing=set(window.ADVANCED_SETTINGS))
    with pytest.raises(window.ControlSettingError, match="AdvancedSettings"):
        window.apply_settings(ax, PARAMS, "pw", nonscriptable=lambda a, ns: None)


def test_apply_settings_refuses_without_nonscriptable_or_password():
    ax = FakeAx()
    with pytest.raises(window.NonScriptableUnavailable):
        window.apply_settings(ax, PARAMS, "pw", nonscriptable=None)
    assert ax.props == {}                                    # 一项都没写
    with pytest.raises(ValueError):
        window.apply_settings(ax, PARAMS, "", nonscriptable=lambda a, ns: None)


def test_apply_settings_stops_when_a_property_is_refused_and_password_not_set():
    ax = FakeAx(refuse={"RedirectClipboard"})
    with pytest.raises(window.ControlSettingError, match="RedirectClipboard") as info:
        window.apply_settings(ax, PARAMS, "pw-xyz", nonscriptable=lambda a, ns: None)
    assert "pw-xyz" not in str(info.value)
    assert "ClearTextPassword" not in ax.subs["AdvancedSettings9"].props


def test_apply_settings_nonscriptable_failure_blocks_password():
    ax = FakeAx()

    def fail(a, ns):
        raise window.NonScriptableUnavailable("drive")

    with pytest.raises(window.NonScriptableUnavailable):
        window.apply_settings(ax, PARAMS, "pw", nonscriptable=fail)
    assert "ClearTextPassword" not in ax.subs["AdvancedSettings9"].props


def test_event_router_forwards(h, caplog):
    changes = []
    ext = {"v": 5}
    router = window.EventRouter(h.s, read_extended=lambda: ext["v"], describe=lambda d, e: f"desc {d}/{e}",
                                on_change=lambda snap, desc: changes.append((snap.state, desc)))
    h.s.connect()
    router.on_connecting()
    router.on_connected()
    router.on_login_complete()
    assert h.s.state is State.CONNECTED
    with caplog.at_level("INFO"):
        router.on_disconnected(3)
    assert h.s.state is State.HALTED and h.s.snapshot().halt is Outcome.REPLACED
    assert changes[-1] == (State.HALTED, "desc 3/5")
    assert "desc 3/5" not in caplog.text                     # 系统说明只给界面，不进日志
    h.s.connect()
    ext["v"] = 0
    router.on_logon_error(0xFFFFFFFF)
    assert h.s.snapshot().halt is Outcome.LOGIN_FAILED
    h.s.connect()
    router.on_fatal_error(7)
    assert h.s.state is State.RETRY_WAIT


def test_event_router_tolerates_control_read_errors(h):
    def bad(*args):
        raise RuntimeError("gone")

    router = window.EventRouter(h.s, read_extended=bad, describe=bad, on_change=lambda s, d: None)
    h.s.connect()
    router.on_disconnected(2055)
    assert h.s.snapshot().halt is Outcome.LOGIN_FAILED


@pytest.mark.skipif(window.available(), reason="Windows 上走下面的真控件用例")
def test_window_construct_raises_off_windows():
    with pytest.raises(window.WindowUnavailable):
        window.CloudWindow(PARAMS, password_provider=lambda: "pw", nonscriptable=None)


# ---------------- window：只在 Windows（有 QtAxContainer 与 MsTscAx）上跑 ----------------

windows_only = pytest.mark.skipif(not window.available(), reason="需要 Windows 与 PySide6.QtAxContainer")


@pytest.fixture
def qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@windows_only
def test_window_minimized_titled_and_closing_is_disconnect(qt_app):
    opened = []
    w = window.CloudWindow(PARAMS, password_provider=lambda: opened.append(1) or "pw",
                           nonscriptable=lambda a, ns: (_ for _ in ()).throw(window.NonScriptableUnavailable("x")))
    assert w.windowTitle() == window.WINDOW_TITLE
    w.showMinimized()
    qt_app.processEvents()
    assert w.isMinimized() and w.isVisible()
    assert w.session.state is State.IDLE
    w.close()
    assert w.session.snapshot().last_outcome is Outcome.USER_CLOSED


@windows_only
def test_window_events_drive_session(qt_app):
    w = window.CloudWindow(PARAMS, password_provider=lambda: "pw", nonscriptable=lambda a, ns: None)
    w.session._open = lambda: None                           # 不真连：只验事件转发
    w.session.connect()
    w.events.on_connected()
    w.events.on_login_complete()
    assert w.session.state is State.CONNECTED
    w.events.on_disconnected(3)
    assert w.session.state in (State.RETRY_WAIT, State.HALTED)
    w.close()
    assert w.session.state is State.IDLE


def test_module_imports_off_windows():
    assert window.CONTROL == "MsTscAx.MsTscAx"
    assert "云端 Express" in window.WINDOW_TITLE
    if sys.platform != "win32":
        assert not window.available()
