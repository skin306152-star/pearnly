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


def test_tray_menu_has_one_entry_and_left_click_opens_the_main_window():
    # 2026-10-08：托盘只留状态、打开小助手、退出；文件夹、日志、设置都在主窗口。
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from src.companion.gui_tray import TrayApp

    QApplication.instance() or QApplication([])
    app = TrayApp.__new__(TrayApp)
    app.lang = "zh"
    app.tray = QSystemTrayIcon()
    app._quit = lambda: None
    opened = []
    app._repair = lambda: opened.append(True)
    app._build_menu()

    texts = [action.text() for action in app.tray.contextMenu().actions()
             if action.isVisible() and not action.isSeparator()]
    assert texts == ["启动中…", "打开小助手", "退出"]

    app._on_tray_activated(QSystemTrayIcon.Context)
    assert opened == []
    app._on_tray_activated(QSystemTrayIcon.Trigger)
    assert opened == [True]
