"""后台做法丢焦点（bg_desk._focus）：10-30 实机，别的程序抢了前台后 Express 界面线程没有激活窗口、也没有焦点格，打字报
「Express has no focused field」转人工、作废。改为给它最后激活的顶层窗口发 WM_ACTIVATE，让它自己放回焦点（录制机实测有效，
前台不变）。假 user32，不依赖 Windows。"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion.express_operator import bg_desk  # noqa: E402
from companion.express_operator.desk import Refused  # noqa: E402

FIELD, POPUP, MAIN = 42, 40, 41


class FakeUser32:
    def __init__(self):
        self.focus, self.active, self.calls = FIELD, MAIN, []
        self.popup, self.enabled, self.restores_to = MAIN, True, FIELD

    def GetLastActivePopup(self, hwnd):
        return self.popup

    def IsWindowEnabled(self, hwnd):
        return self.enabled

    def SendMessageW(self, hwnd, msg, wparam, lparam):
        self.calls.append(("send", hwnd, msg, wparam))
        if msg == bg_desk.WM_ACTIVATE:
            self.active, self.focus = hwnd, self.restores_to


class Desk(bg_desk.BackgroundDesk):
    def __init__(self, user32):
        self.sent, self._last_focus, self.u = 0, 0, user32

    def _thread_info(self):
        return SimpleNamespace(hwndFocus=self.u.focus, hwndActive=self.u.active)

    def _main_hwnd(self):
        return MAIN


@pytest.fixture
def u(monkeypatch):
    user32 = FakeUser32()
    monkeypatch.setattr(bg_desk, "user32", user32, raising=False)
    return user32


def _lose_foreground(u):
    u.focus, u.active = None, None


def test_a_focused_field_is_used_as_is(u):
    assert Desk(u)._focus() == FIELD and u.calls == []


def test_after_another_program_took_the_foreground_express_puts_its_own_focus_back(u):
    desk = Desk(u)
    _lose_foreground(u)
    assert desk._focus() == FIELD
    assert u.calls == [("send", MAIN, bg_desk.WM_ACTIVATE, bg_desk.WA_ACTIVE)]


def test_an_open_dialog_is_the_window_activated(u):
    desk = Desk(u)
    _lose_foreground(u)
    u.popup = POPUP
    assert desk._focus() == FIELD and u.calls[0][1] == POPUP


def test_focus_landing_on_the_main_window_itself_hands_over(u):
    desk = Desk(u)
    _lose_foreground(u)
    u.restores_to = MAIN
    with pytest.raises(Refused):
        desk._focus()


def test_express_with_an_active_window_but_no_focus_is_left_alone(u):
    """Express 自己有激活窗口却没有焦点格：不是被抢前台，不动它。"""
    u.focus = None
    with pytest.raises(Refused):
        Desk(u)._focus()
    assert u.calls == [] and Desk(u).focus_info()["hwnd"] == 0
