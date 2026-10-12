# -*- coding: utf-8 -*-
"""托盘：打开 Tomo、连接与设置、通知点击、自动更新时机（2026-10-10 起小助手并入 Tomo）。无 PySide6 则跳过。"""

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from src.companion.gui_tray import MENU_TEXT_MAX, _menu_text  # noqa: E402


def test_tray_status_line_is_capped():
    long = "บริษัท " * 30 + " · 离线 · 云端推送未开启"
    assert len(_menu_text(long)) == MENU_TEXT_MAX
    assert _menu_text("离线") == "离线"


def test_tray_menu_opens_tomo_first_and_left_click_opens_tomo():
    # 2026-10-10：小助手并入 Tomo 桌面端：托盘是状态、打开 Tomo、连接与设置、退出；左键打开 Tomo。
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from src.companion.gui_tray import TrayApp

    QApplication.instance() or QApplication([])
    app = TrayApp.__new__(TrayApp)
    app.lang = "zh"
    app.configs = []
    app.tray = QSystemTrayIcon()
    app._quit = lambda: None
    opened, settings = [], []
    app.open_tomo = lambda text=None: opened.append(text)
    app._repair = lambda: settings.append(True)
    app._build_menu()

    texts = [action.text() for action in app.tray.contextMenu().actions()
             if action.isVisible() and not action.isSeparator()]
    assert texts == ["启动中…", "打开 CC", "连接与设置", "退出"]

    app._on_tray_activated(QSystemTrayIcon.Context)
    assert opened == []
    app._on_tray_activated(QSystemTrayIcon.Trigger)
    assert opened == [None]
    assert settings == []


def test_tray_without_express_says_not_connected():
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from src.companion.gui_tray import TrayApp

    QApplication.instance() or QApplication([])
    app = TrayApp.__new__(TrayApp)
    app.lang = "zh"
    app.configs = []
    app.tray = QSystemTrayIcon()
    app._quit = lambda: None
    app._repair = lambda: None
    app.open_tomo = lambda text=None: None
    app._profile_states = {}
    app._build_menu()
    app._render_status()
    assert app._status_action.text() == "未连接 Express"


def test_tomo_notifications_run_their_click_action_once():
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from src.companion.gui_tray import TrayApp

    QApplication.instance() or QApplication([])
    app = TrayApp.__new__(TrayApp)
    app.tray = QSystemTrayIcon()
    clicked = []
    app._notify("Tomo", "done", lambda: clicked.append(1))
    app._on_message_clicked()
    app._on_message_clicked()
    assert clicked == [1]


def test_reminders_pop_up_once_per_notice_and_open_their_conversation():
    # 2026-10：对话提醒（页面实时收到 / 文件夹同步带来）按 notice:<编号> 只弹一次，点了打开那段对话；去重记忆有上限
    from PySide6.QtWidgets import QApplication

    from src.companion.gui_tray import NOTICE_MEMORY, TrayApp

    QApplication.instance() or QApplication([])
    app = TrayApp.__new__(TrayApp)   # 不走 __init__：去重记忆现建
    shown, opened = [], []
    app.tray = SimpleNamespace(showMessage=lambda *args: shown.append(args))
    app.open_conversation = lambda site, session: opened.append((site, session))
    items = [{"id": "pnotice_1", "title": "", "text": "2 files waiting", "created": 1.0, "session": "sess-1"}]
    app._on_folder_notices("https://acme.pearnly.com", items)
    app._on_folder_notices("https://acme.pearnly.com", items)
    app._notify("CC", "2 files waiting", None, tag="notice:pnotice_1")   # 对话页实时收到的同一条
    assert [(title, body) for title, body, *_ in shown] == [("CC", "2 files waiting")]
    app._on_message_clicked()
    assert opened == [("https://acme.pearnly.com", "sess-1")]
    app._notify("CC", "reply", None, tag="tomo-reply")
    app._notify("CC", "reply", None, tag="tomo-reply")
    assert len(shown) == 3   # 回复完成等别的通知不去重
    for n in range(NOTICE_MEMORY + 20):
        app._notify("CC", "x", None, tag=f"notice:{n}")
    assert len(app._seen_notices) == NOTICE_MEMORY


def test_tray_opens_a_reminder_conversation_in_the_cc_window():
    from src.companion.gui_tray import TrayApp

    app = TrayApp.__new__(TrayApp)
    calls = []
    app._tomo = SimpleNamespace(open_conversation=lambda url, session: calls.append((url, session)))
    app.open_tomo = lambda text=None: calls.append("window")
    app.open_conversation("https://acme.pearnly.com", "s-123")
    app.open_conversation("https://acme.pearnly.com", "")
    app.open_conversation("https://evil.example", "s-123")
    assert calls == [("https://acme.pearnly.com/assistant?c=s-123", "s-123"),
                     ("https://acme.pearnly.com/assistant?view=tasks", ""), "window"]


def test_cc_window_hands_the_conversation_to_an_open_chat_page_or_loads_it():
    pytest.importorskip("PySide6.QtWebEngineWidgets")
    from src.companion import gui_tomo

    def window(current, *, ready=True, loading=False):
        calls = []
        page = SimpleNamespace(url=lambda: SimpleNamespace(toString=lambda: current), isLoading=lambda: loading,
                               runJavaScript=lambda script: calls.append(("js", script)),
                               setUrl=lambda url: calls.append(("load", url.toString())))
        return SimpleNamespace(show_front=lambda: calls.append("front"), page=page, _ready=ready,
                               _pending_ask="x", _pending_settings=None), calls

    url = "https://acme.pearnly.com/assistant?c=s-123"
    fake, calls = window("https://acme.pearnly.com/assistant")
    gui_tomo.TomoWindow.open_conversation(fake, url, "s-123")
    assert calls[0] == "front" and calls[1][0] == "js" and 't.open("s-123")' in calls[1][1]
    for current, kwargs in (("https://other.pearnly.com/assistant", {}), ("https://fatcc.com/", {}),
                            ("https://acme.pearnly.com/assistant", {"loading": True}),
                            ("https://acme.pearnly.com/assistant", {"ready": False})):
        fake, calls = window(current, **kwargs)
        gui_tomo.TomoWindow.open_conversation(fake, url, "s-123")
        assert calls == ["front", ("load", url)], current
        assert fake._pending_ask is None   # 别的页面里排着的「新对话」不再交过去

    notes = []
    fake = SimpleNamespace(_notify=lambda *args, **kwargs: notes.append((args, kwargs)), show_front=lambda: None)
    notification = SimpleNamespace(title=lambda: "", message=lambda: "m", tag=lambda: "notice:pnotice_1",
                                   click=lambda: None)
    gui_tomo.TomoWindow._present(fake, notification)
    assert notes[0][0][:2] == ("CC", "m") and notes[0][1] == {"tag": "notice:pnotice_1"}


def test_tray_checks_for_a_new_version_in_the_background_and_hands_it_to_auto_update(monkeypatch):
    # 2026-10-09：托盘不再定时查（新版随心跳回复带来）；只有点「检查更新」时在托盘自己的后台线程查一次。
    import time

    from PySide6.QtWidgets import QApplication

    from src.companion import gui_tray, updater
    from src.companion.gui_tray import TrayApp

    QApplication.instance() or QApplication([])
    assert not hasattr(gui_tray, "UPDATE_CHECK_MS")
    app = TrayApp.__new__(TrayApp)
    app._quitting = app._updating = False
    app._checking = None
    app._checked_latest = False
    offered = []
    app._on_update_available = lambda version, url: offered.append((version, url))
    app._auto_update = SimpleNamespace(pending=None, state=lambda: ("none", ""))
    answers = [None, {"version": "1.4.0", "url": "/x.exe"}]
    monkeypatch.setattr(updater, "check_update", lambda _site: answers.pop(0))

    def run(manual):
        app._check_for_update(manual=manual)
        assert app.update_status() == ("checking", "")
        deadline = time.monotonic() + 5
        while app._checking is not None and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)

    run(True)
    assert offered == [] and app.update_status() == ("latest", "")
    run(False)
    assert offered == [("1.4.0", "/x.exe")]


def test_auto_update_waits_while_a_helper_window_is_open_unless_someone_asked(monkeypatch):
    # 2026-10-08：自动更新不打断正在用小助手的人：窗口开着先不装；点了「立即更新」就照装。录入中永远不装。
    from src.companion import gui_tray
    from src.companion.gui_tray import TrayApp

    app = TrayApp.__new__(TrayApp)
    app._profile_states = {"A": ("idle", "")}
    app._update_requested = False
    opened = {"value": False}
    monkeypatch.setattr(gui_tray, "_window_open", lambda: opened["value"])

    assert app._busy() is False
    opened["value"] = True
    assert app._busy() is True
    app._update_requested = True
    assert app._busy() is False
    app._profile_states = {"A": ("working", "INV-1")}
    assert app._busy() is True


def test_auto_update_only_waits_while_someone_is_using_the_tomo_window(monkeypatch):
    # 2026-10-10：原小助手窗口并入 Tomo；Tomo 窗口开着且在最前面才算「正在用」，开着放在后面照常更新
    from types import SimpleNamespace

    from src.companion import gui_tomo, gui_tray

    monkeypatch.setattr(gui_tomo, "OPEN_WINDOW", None)
    assert gui_tray._window_open() is False
    monkeypatch.setattr(gui_tomo, "OPEN_WINDOW", SimpleNamespace(isVisible=lambda: True, isActiveWindow=lambda: True))
    assert gui_tray._window_open() is True
    monkeypatch.setattr(gui_tomo, "OPEN_WINDOW", SimpleNamespace(isVisible=lambda: True, isActiveWindow=lambda: False))
    assert gui_tray._window_open() is False
