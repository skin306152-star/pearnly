"""云端连接窗口的远程桌面控件宿主（``companion.cloud_link.ax_host``）。

守住：ATL 宿主的调用顺序（初始化 → 父窗口裁剪子窗口 → 建 AtlAxWin 子窗口、窗口文本是控件 CLSID → 取控件 → IDispatch → 接事件）；
任何一步失败都撤掉已做的并抛 AxUnavailable；属性设不进去回假（不带值）；子对象取不到回 None；IUnknown 裸指针交给非脚本设置；
事件按名字转给 EventRouter、异常不回控件；关窗退订事件、放掉引用、销毁子窗口且只做一次；事件接口的 IID 与 dispid 钉死
（微软 Learn「IMsTscAxEvents interface」与类型库一致）；非 Windows 建不出真调用。"""
from __future__ import annotations

import sys

import pytest

from companion.cloud_link import ax_host


class FakeWin32:
    def __init__(self, log, fail=None):
        self.log = log
        self.fail = fail

    def _step(self, name, *args):
        self.log.append((name,) + args)
        if self.fail == name:
            raise ax_host.AxUnavailable(name)

    def init(self):
        self._step("init")

    def clip_children(self, parent):
        self._step("clip", parent)

    def create_child(self, parent, control_id, width, height):
        self._step("create", parent, control_id, width, height)
        return 0x55

    def get_control(self, hwnd):
        self._step("get_control", hwnd)
        return 0x9000

    def move(self, hwnd, width, height):
        self.log.append(("move", hwnd, width, height))

    def destroy(self, hwnd):
        self.log.append(("destroy", hwnd))


class ComError(Exception):
    def __init__(self, hresult):
        super().__init__(hresult, "fail", None)
        self.hresult = hresult


class FakeCom:
    def __init__(self, log, fail=None, refuse=(), missing=()):
        self.log = log
        self.fail = fail
        self.refuse = set(refuse)
        self.missing = set(missing)
        self.values = {}
        self.forwarder = None

    def adopt(self, address):
        self.log.append(("adopt", address))
        return ("unknown", address)

    def address(self, unknown):
        return unknown[1]

    def dispatch(self, unknown):
        self.log.append(("dispatch",))
        if self.fail == "dispatch":
            raise ComError(0x80004002)
        return "disp"

    def advise(self, unknown, forwarder):
        self.log.append(("advise",))
        if self.fail == "advise":
            raise ComError(0x80040200)
        self.forwarder = forwarder
        return "cookie"

    def unadvise(self, advise):
        self.log.append(("unadvise", advise))

    def put(self, dispatch, name, value):
        if name in self.refuse:
            raise ComError(0x80020003)
        self.values[(dispatch, name)] = value

    def get(self, dispatch, name):
        if name in self.missing:
            raise ComError(0x80020006)
        if name.startswith("AdvancedSettings"):
            return "adv"
        return self.values.get((dispatch, name), 0)

    def call(self, dispatch, name, *args):
        self.log.append(("call", dispatch, name) + args)
        return "ok"


class Router:
    def __init__(self, boom=False):
        self.seen = []
        self.boom = boom

    def __getattr__(self, name):
        if not name.startswith("on_"):
            raise AttributeError(name)

        def handler(*args):
            self.seen.append((name,) + args)
            if self.boom:
                raise RuntimeError("router failed")

        return handler


def _host(fail_win=None, fail_com=None, router=None, **com_kw):
    log = []
    win, com = FakeWin32(log, fail_win), FakeCom(log, fail_com, **com_kw)
    host = ax_host.AxHost(0x77, 1280, 800, router=router or Router(), win32=win, com=com)
    return host, log, com


def test_build_order_and_control_clsid():
    host, log, _ = _host()
    assert log == [("init",), ("clip", 0x77), ("create", 0x77, ax_host.CONTROL_CLSID, 1280, 800),
                   ("get_control", 0x55), ("adopt", 0x9000), ("dispatch",), ("advise",)]
    assert host.control.unknown_address() == 0x9000
    assert ax_host.CONTROL_CLSID == "{A0C63C30-F08D-4AB4-907C-34905D770C7D}"   # MsRdpClient10NotSafeForScripting
    assert ax_host.ATL_WINDOW_CLASS == "AtlAxWin"


@pytest.mark.parametrize("fail_win, fail_com, undone", [
    ("init", None, []),
    ("create", None, []),
    ("get_control", None, [("destroy", 0x55)]),
    (None, "dispatch", [("destroy", 0x55)]),
    (None, "advise", [("destroy", 0x55)]),
])
def test_any_failed_step_undoes_and_raises(fail_win, fail_com, undone):
    log = []
    with pytest.raises(ax_host.AxUnavailable):
        ax_host.AxHost(0x77, 10, 10, router=Router(), win32=FakeWin32(log, fail_win), com=FakeCom(log, fail_com))
    assert [e for e in log if e[0] in ("destroy", "unadvise")] == undone


def test_properties_go_through_dispatch_and_refusals_return_false_without_the_value(caplog):
    host, _, com = _host(refuse={"ClearTextPassword"})
    control = host.control
    assert control.setProperty("Server", "198.51.100.7") is True
    assert com.values[("disp", "Server")] == "198.51.100.7"
    with caplog.at_level("INFO"):
        assert control.setProperty("ClearTextPassword", "s3cret!") is False
    assert "s3cret!" not in caplog.text and "80020003" in caplog.text
    adv = control.querySubObject("AdvancedSettings9")
    assert adv is not None and adv.unknown_address() == 0
    assert adv.setProperty("RDPPort", 3390) is True and com.values[("adv", "RDPPort")] == 3390


def test_missing_sub_object_is_none_so_apply_settings_tries_the_next():
    from companion.cloud_link import window

    host, _, _ = _host(missing={"AdvancedSettings9", "AdvancedSettings8"})
    assert host.control.querySubObject("AdvancedSettings9") is None
    params = window.ConnectParams.make(host="198.51.100.7", port=3390, user="CLOUDUSER-01", domain="example",
                                       tunnel_drive="X")
    window.apply_settings(host.control, params, "pw", nonscriptable=lambda c, ns: None)   # 落到 AdvancedSettings7


def test_apply_settings_refuses_on_the_real_control_wrapper():
    from companion.cloud_link import window

    host, log, _ = _host(refuse={"RedirectDrives"})
    params = window.ConnectParams.make(host="198.51.100.7", port=3390, user="CLOUDUSER-01", domain="example",
                                       tunnel_drive="X")
    with pytest.raises(window.ControlSettingError, match="RedirectDrives"):
        window.apply_settings(host.control, params, "pw", nonscriptable=lambda c, ns: None)
    assert not [e for e in log if e[0] == "call"]


def test_methods_and_reads():
    host, log, _ = _host()
    assert host.control.call("GetErrorDescription", 3, 5) == "ok"
    assert log[-1] == ("call", "disp", "GetErrorDescription", 3, 5)
    assert host.control.property("Connected") == 0


def test_events_are_forwarded_by_name():
    router = Router()
    _, _, com = _host(router=router)
    for _dispid, name, args in ax_host.EVENTS:
        com.forwarder.fire(name, *([7] * len(args)))
    com.forwarder.fire("OnWarning", 1)                       # 没声明的事件不转
    assert router.seen == [("on_connecting",), ("on_connected",), ("on_login_complete",), ("on_disconnected", 7),
                           ("on_fatal_error", 7), ("on_logon_error", 7)]


def test_event_handler_errors_do_not_reach_the_control(caplog):
    router = Router(boom=True)
    _, _, com = _host(router=router)
    with caplog.at_level("INFO"):
        com.forwarder.fire("OnDisconnected", 3)
    assert router.seen == [("on_disconnected", 3)] and "RuntimeError" in caplog.text


def test_close_unadvises_drops_references_and_destroys_once():
    host, log, _ = _host()
    host.close()
    host.close()
    assert [e for e in log if e[0] in ("unadvise", "destroy")] == [("unadvise", "cookie"), ("destroy", 0x55)]
    assert host.control is None and host.closed
    host.resize(100, 100)
    assert not [e for e in log if e[0] == "move"]


def test_resize_moves_the_child_window():
    host, log, _ = _host()
    host.resize(1024, 0)
    assert log[-1] == ("move", 0x55, 1024, 1)


def test_pinned_event_interface():
    assert ax_host.IID_EVENTS == "{336D5562-EFA8-482E-8CB3-C5C0FC7A7DB6}"
    assert [(d, n) for d, n, _ in ax_host.EVENTS] == [
        (1, "OnConnecting"), (2, "OnConnected"), (3, "OnLoginComplete"), (4, "OnDisconnected"),
        (10, "OnFatalError"), (22, "OnLogonError")]
    assert set(ax_host.ROUTES) == {n for _, n, _ in ax_host.EVENTS}


@pytest.mark.skipif(sys.platform == "win32", reason="只在非 Windows 上核")
def test_real_calls_unavailable_off_windows():
    assert not ax_host.available()
    with pytest.raises(ax_host.AxUnavailable):
        ax_host.AxHost(1, 10, 10, router=Router())
    with pytest.raises(ax_host.AxUnavailable):
        ax_host.Win32()
    with pytest.raises(ax_host.AxUnavailable):
        ax_host.Com()
