"""远程桌面控件非脚本两项（``companion.cloud_link.nonscriptable``）。

守住：开 CredSSP、关动态盘、重扫一次；逐个盘只让隧道盘为真、其余全部为假；每项设完读回核对；找不到隧道盘或找到不止一个、
任何一步 HRESULT 失败、读回不符都抛错（不连）；
拿到的指针全部 Release；vtable 下标与 IID 钉死（取自 mstsax.idl，并与 mstscax.dll 类型库核对过）；非 Windows 建不出真调用。"""
from __future__ import annotations

import sys

import pytest

from companion.cloud_link import nonscriptable as ns
from companion.cloud_link import window

CONTROL = 0x1000
NS3 = 0x2000
DRIVES = 0x3000


class FakeCalls:
    def __init__(self, names, *, fail=None):
        self.names = list(names)
        self.fail = fail or {}
        self.log = []
        self.states = {}
        self.refs = {}
        self.credssp = False

    def _hr(self, key):
        return self.fail.get(key, 0)

    def _take(self, ptr):
        self.refs[ptr] = self.refs.get(ptr, 0) + 1
        return ptr

    def query(self, ptr, iid):
        self.log.append(("query", ptr, iid))
        if self._hr("query"):
            raise ns.NonScriptableError("QueryInterface")
        return self._take(NS3)

    def release(self, ptr):
        self.log.append(("release", ptr))
        self.refs[ptr] = self.refs.get(ptr, 0) - 1

    def put_bool(self, ptr, index, value):
        self.log.append(("put", ptr, index, value))
        hr = self._hr(("put", index))
        if not hr and ptr >= 0x4000 and index == ns.V_PUT_REDIRECTION_STATE:
            self.states[ptr - 0x4000] = value
        if not hr and ptr == NS3 and index == ns.V_PUT_ENABLE_CREDSSP:
            self.credssp = value
        return hr

    def get_bool(self, ptr, index):
        self.log.append(("read", ptr, index))
        if ptr == NS3:
            assert index == ns.V_GET_ENABLE_CREDSSP
            value = self.credssp if not self.fail.get("ignore_credssp") else False
            return self._hr(("read", index)), value
        assert index == ns.V_GET_REDIRECTION_STATE
        value = self.states.get(ptr - 0x4000, False)
        if self.fail.get("stuck_drive") == ptr - 0x4000:
            value = not value
        return self._hr(("read", index)), value

    def get_ulong(self, ptr, index):
        assert (ptr, index) == (DRIVES, ns.V_GET_DRIVE_COUNT)
        return self._hr("count"), len(self.names)

    def get_ptr(self, ptr, index, *head):
        if (ptr, index) == (NS3, ns.V_GET_DRIVE_COLLECTION):
            return self._hr("collection"), (self._take(DRIVES) if not self.fail.get("empty") else 0)
        assert (ptr, index) == (DRIVES, ns.V_GET_DRIVE_BY_INDEX)
        return self._hr("by_index"), self._take(0x4000 + head[0])

    def get_bstr(self, ptr, index):
        assert index == ns.V_GET_NAME
        return self._hr("name"), self.names[ptr - 0x4000]


def balanced(calls):
    return all(count == 0 for count in calls.refs.values())


def test_only_tunnel_drive_is_redirected_and_credssp_on():
    calls = FakeCalls(["Local Disk (C:)", "D:", "Pearnly (X:)", "USB (E:)"])
    seen = ns.apply(CONTROL, drive="x", calls=calls)
    assert seen == ["C", "D", "X", "E"]
    assert calls.states == {0: False, 1: False, 2: True, 3: False}
    puts = [entry for entry in calls.log if entry[0] == "put" and entry[1] == NS3]
    assert puts == [("put", NS3, ns.V_PUT_ENABLE_CREDSSP, True), ("put", NS3, ns.V_PUT_REDIRECT_DYNAMIC_DRIVES, False)]
    assert ("put", DRIVES, ns.V_RESCAN_DRIVES, False) in calls.log
    assert calls.log[0] == ("query", CONTROL, ns.IID_NONSCRIPTABLE3)
    assert balanced(calls)


@pytest.mark.parametrize("names", [["C:", "D:"], ["X:", "Backup (X:)"], []])
def test_missing_or_ambiguous_tunnel_drive_refuses(names):
    calls = FakeCalls(names)
    with pytest.raises(ns.NonScriptableError):
        ns.apply(CONTROL, drive="X", calls=calls)
    assert balanced(calls)


@pytest.mark.parametrize("fail", [
    {("put", ns.V_PUT_ENABLE_CREDSSP): 0x80004005},
    {("put", ns.V_PUT_REDIRECT_DYNAMIC_DRIVES): 0x80004005},
    {"collection": 0x80004001},
    {"empty": True},
    {("put", ns.V_RESCAN_DRIVES): 0x80004005},
    {"count": 0x80004005},
    {"by_index": 0x80004005},
    {"name": 0x80004005},
    {("put", ns.V_PUT_REDIRECTION_STATE): 0x80070005},
    {"query": True},
    {("read", ns.V_GET_ENABLE_CREDSSP): 0x80004005},
    {("read", ns.V_GET_REDIRECTION_STATE): 0x80004005},
    {"ignore_credssp": True},                                # 控件不认 CredSSP：读回仍是假
    {"stuck_drive": 0},                                      # 某个盘设了不生效：读回不符
    {"stuck_drive": 1},                                      # 隧道盘本身没打开
])
def test_any_failed_step_refuses_and_releases(fail):
    calls = FakeCalls(["C:", "X:"], fail=fail)
    with pytest.raises(ns.NonScriptableError):
        ns.apply(CONTROL, drive="X", calls=calls)
    assert balanced(calls)


def test_bad_drive_refuses_before_touching_control():
    calls = FakeCalls(["X:"])
    for bad in ("", "XY", "1"):
        with pytest.raises(ns.NonScriptableError):
            ns.apply(CONTROL, drive=bad, calls=calls)
    assert calls.log == []


def test_drive_count_cap():
    calls = FakeCalls(["C:"] * (ns.MAX_DRIVES + 1))
    with pytest.raises(ns.NonScriptableError):
        ns.apply(CONTROL, drive="X", calls=calls)
    assert balanced(calls)


@pytest.mark.parametrize("name,letter", [
    ("X:", "X"), ("Pearnly (x:)", "X"), ("Local Disk (C:)", "C"), ("", ""), ("no letter", ""), ("http://a:b (Z:)", "Z"),
])
def test_drive_letter(name, letter):
    assert ns.drive_letter(name) == letter


def test_pinned_layout():
    assert ns.IID_NONSCRIPTABLE3 == "{B3378D90-0728-45C7-8ED7-B6159FB92219}"
    assert (ns.V_PUT_ENABLE_CREDSSP, ns.V_GET_ENABLE_CREDSSP) == (23, 24)
    assert (ns.V_PUT_REDIRECT_DYNAMIC_DRIVES, ns.V_GET_DRIVE_COLLECTION) == (25, 30)
    assert (ns.V_RESCAN_DRIVES, ns.V_GET_DRIVE_BY_INDEX, ns.V_GET_DRIVE_COUNT) == (3, 4, 5)
    assert (ns.V_GET_NAME, ns.V_PUT_REDIRECTION_STATE, ns.V_GET_REDIRECTION_STATE) == (3, 4, 5)


def test_never_touches_send_keys_or_warning_switches():
    # SendKeys 是 14；警告框 / 凭据提示类 17–22、31–34 都不碰
    calls = FakeCalls(["X:"])
    ns.apply(CONTROL, drive="X", calls=calls)
    indexes = {entry[2] for entry in calls.log if entry[0] == "put" and entry[1] == NS3}
    assert indexes == {ns.V_PUT_ENABLE_CREDSSP, ns.V_PUT_REDIRECT_DYNAMIC_DRIVES}


def test_every_setting_is_read_back():
    calls = FakeCalls(["C:", "X:"])
    ns.apply(CONTROL, drive="X", calls=calls)
    reads = [entry for entry in calls.log if entry[0] == "read"]
    assert reads == [("read", NS3, ns.V_GET_ENABLE_CREDSSP), ("read", 0x4000, ns.V_GET_REDIRECTION_STATE),
                     ("read", 0x4001, ns.V_GET_REDIRECTION_STATE)]


def test_read_back_reports_without_changing_anything():
    calls = FakeCalls(["Local Disk (C:)", "Pearnly (X:)"])
    ns.apply(CONTROL, drive="X", calls=calls)
    before = len([e for e in calls.log if e[0] == "put"])
    assert ns.read_back(CONTROL, calls=calls) == {"credssp": True, "drives": [("C", False), ("X", True)]}
    assert len([e for e in calls.log if e[0] == "put"]) == before
    assert balanced(calls)


class FakeControl:
    def __init__(self, address):
        self.address = address

    def unknown_address(self):
        return self.address


def test_for_widget_uses_the_borrowed_control_pointer_and_does_not_release_it():
    calls = FakeCalls(["X:"])
    run = ns.for_widget(lambda: calls)
    run(FakeControl(CONTROL), window.NonScriptable(enable_credssp=True, drive="X"))
    assert calls.log[0] == ("query", CONTROL, ns.IID_NONSCRIPTABLE3)
    assert ("release", CONTROL) not in calls.log             # 控件指针归 ax_host.AxHost 持有
    assert balanced(calls) and calls.states == {0: True}
    with pytest.raises(ns.NonScriptableError):
        run(FakeControl(0), window.NonScriptable(enable_credssp=True, drive="X"))


@pytest.mark.skipif(sys.platform == "win32", reason="只在非 Windows 上核")
def test_real_calls_unavailable_off_windows():
    with pytest.raises(ns.NonScriptableError):
        ns.ComCalls()
