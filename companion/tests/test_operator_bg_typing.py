"""后台做法打泰文（bg_desk.type_any）：字符消息两头都按线程输入法的代码页换，10-28 测试机默认中文输入法时泰文变「?」。
先把 Express 的界面线程切到泰文输入法再发 Unicode；切不过去照旧发 cp874 字节；纯 ASCII 不切。假 user32，不依赖 Windows。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion.express_operator import bg_desk  # noqa: E402

CHINESE, THAI = 0x08040804, 0x041E041E


class FakeUser32:
    def __init__(self, switches: bool):
        self.layout, self.switches, self.calls = CHINESE, switches, []

    def GetWindowThreadProcessId(self, hwnd, _pid):
        return 7

    def GetKeyboardLayout(self, tid):
        return self.layout

    def PostMessageW(self, hwnd, msg, wparam, lparam):
        self.calls.append(("post", msg, lparam))
        if msg == bg_desk.WM_INPUTLANGCHANGEREQUEST and self.switches:
            self.layout = lparam

    def SendMessageW(self, hwnd, msg, wparam, lparam):
        self.calls.append(("W", wparam))

    def SendMessageA(self, hwnd, msg, wparam, lparam):
        self.calls.append(("A", wparam))


class Desk(bg_desk.BackgroundDesk):
    def __init__(self):
        self.sent, self._hkl = 0, {bg_desk.TH_LAYOUT: THAI}

    def _focus(self):
        return 42

    def _main_hwnd(self):
        return 41


@pytest.fixture
def fake(monkeypatch):
    def make(switches):
        user32 = FakeUser32(switches)
        monkeypatch.setattr(bg_desk, "user32", user32, raising=False)
        return user32
    return make


def test_thai_switches_express_to_thai_input_then_sends_unicode(fake):
    user32 = fake(True)
    Desk().type_any("ร008")
    assert ("post", bg_desk.WM_INPUTLANGCHANGEREQUEST, THAI) in user32.calls
    assert [c for c in user32.calls if c[0] != "post"] == [("W", ord(ch)) for ch in "ร008"]


def test_when_express_will_not_switch_the_cp874_bytes_go_as_before(fake):
    user32 = fake(False)
    assert Desk()._thai_input(42, wait_s=0.1) is False
    user32.calls.clear()
    desk = Desk()
    desk._thai_input = lambda focus: False
    desk.type_any("ร008")
    assert user32.calls == [("A", b) for b in "ร008".encode("cp874")]


def test_plain_ascii_does_not_touch_the_input_language(fake):
    user32 = fake(True)
    Desk().type_any("RR-001")
    assert not [c for c in user32.calls if c[0] == "post"]
    assert user32.calls == [("A", b) for b in b"RR-001"]


def test_text_express_cannot_store_is_refused(fake):
    fake(True)
    with pytest.raises(UnicodeEncodeError):
        Desk().type_any("中文")


def test_cc_marks_every_input_it_sends(monkeypatch):
    """「有人在用」的钩子只忽略带 CC 签名的输入：键盘、鼠标都要打上（主会话 10-30）。"""
    from types import SimpleNamespace

    from companion.express_operator import win32_desk
    from companion.express_operator.desk import CC_INPUT_MARK

    sent = []

    class Arr(list):
        def __call__(self, *items):
            return list(items)

    class FakeInput:
        def __mul__(self, n):
            return Arr()

    monkeypatch.setattr(win32_desk, "INPUT", FakeInput(), raising=False)
    monkeypatch.setattr(win32_desk, "ctypes", SimpleNamespace(sizeof=lambda _x: 40), raising=False)
    monkeypatch.setattr(win32_desk, "user32", SimpleNamespace(SendInput=lambda n, arr, size: sent.extend(arr)),
                        raising=False)
    monkeypatch.setattr(win32_desk, "kernel32", SimpleNamespace(GetTickCount=lambda: 5), raising=False)
    key = SimpleNamespace(type=1, u=SimpleNamespace(ki=SimpleNamespace(dwExtraInfo=0)))
    mouse = SimpleNamespace(type=0, u=SimpleNamespace(mi=SimpleNamespace(dwExtraInfo=0)))
    desk = object.__new__(win32_desk.WindowsDesk)
    desk._send([key, mouse])
    assert [x.u.ki.dwExtraInfo if x.type == 1 else x.u.mi.dwExtraInfo for x in sent] == [CC_INPUT_MARK] * 2
    assert desk.last_input_tick == 5
