# -*- coding: utf-8 -*-
"""添加成员窗只有配对码一行（2026-10-08：账套是这台电脑的全局设置，在主窗口里）。无 PySide6 则跳过。"""

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from src.companion.gui_pairing import PairingDialog  # noqa: E402
from src.companion.gui_tray import MENU_TEXT_MAX, _menu_text  # noqa: E402


def test_add_member_window_has_only_the_pairing_code():
    from PySide6.QtWidgets import QApplication, QLineEdit

    from src.companion.profile_store import ProfileStore

    QApplication.instance() or QApplication([])
    store = ProfileStore.__new__(ProfileStore)
    store.new_runtime_config = lambda: SimpleNamespace(paired=False, ui_lang="zh", account_set="", account_dir="")
    dialog = PairingDialog(profile_store=store, lang="zh", roots=[r"S:\\EXP68"])

    assert dialog.windowTitle() == "添加成员"
    assert dialog.findChildren(QLineEdit) == [dialog.token]
    # 语言、版本、开机启动、Express 账套都在主窗口（2026-10-08）
    for name in ("lang_btn", "ver_lbl", "autostart", "browse_btn", "root_combo", "account"):
        assert not hasattr(dialog, name)
    assert dialog.pair_btn.text() == "配对" and not dialog.pair_btn.isEnabled()  # 没填配对码不能点
    dialog.token.setText("exp_e_s@testcowork.pearnly.com")
    assert dialog.pair_btn.isEnabled()
    from test_gui_profiles import _assert_free_window

    _assert_free_window(dialog)


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
    assert texts == ["启动中…", "打开 Tomo", "连接与设置", "退出"]

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


def test_window_open_sees_the_open_main_window(monkeypatch):
    from types import SimpleNamespace

    from src.companion import gui_profiles, gui_tray

    assert gui_tray._window_open() is False or gui_profiles._OPEN_MANAGER is not None
    monkeypatch.setattr(gui_profiles, "_OPEN_MANAGER", SimpleNamespace(isVisible=lambda: True))
    assert gui_tray._window_open() is True
    monkeypatch.setattr(gui_profiles, "_OPEN_MANAGER", SimpleNamespace(isVisible=lambda: False))
    assert gui_tray._window_open() is False
