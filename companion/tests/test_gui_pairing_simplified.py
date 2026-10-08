# -*- coding: utf-8 -*-
"""配对窗只留必要项（2026-09-26）：账套 / 年度由网页第一步选择，小助手不选、不显示。无 PySide6 则跳过。"""

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from src.companion.account_probe import AccountSet  # noqa: E402
from src.companion.gui_pairing import PairingDialog  # noqa: E402
from src.companion.gui_tray import MENU_TEXT_MAX, _menu_text  # noqa: E402


def _acc(path):
    return AccountSet(code="A", name="A", company="C", path=path, tax_id="", writable=True, row=2)


def _dialog(prefill, account_set=""):
    dialog = PairingDialog.__new__(PairingDialog)
    dialog._prefill = prefill
    dialog._cfg = SimpleNamespace(account_set=account_set, account_dir="")
    return dialog


def test_default_account_is_picked_without_asking():
    sets = [_acc(r"S:\\EXP68\\A"), _acc(r"S:\\EXP68\\B")]
    assert PairingDialog._prefill_account_index(_dialog(False), sets) == 0
    assert PairingDialog._prefill_account_index(_dialog(True, r"S:\\EXP68\\B"), sets) == 1
    assert PairingDialog._prefill_account_index(_dialog(True, r"S:\\GONE"), sets) == 0
    assert PairingDialog._prefill_account_index(_dialog(True), []) == -1


def test_account_section_is_not_shown(qtbot=None):
    from PySide6.QtWidgets import QApplication

    from src.companion import gui_pairing
    from src.companion.profile_store import ProfileStore

    QApplication.instance() or QApplication([])
    original = gui_pairing.PairingDialog._autodetect
    gui_pairing.PairingDialog._autodetect = lambda self: None
    try:
        store = ProfileStore.__new__(ProfileStore)
        store.new_runtime_config = lambda: SimpleNamespace(paired=False, authorized_roots=[], ui_lang="zh",
                                                           autostart=True, express_root="", account_set="",
                                                           account_dir="")
        dialog = PairingDialog(profile_store=store, lang="zh")
        for widget in (dialog.intro, dialog.sec_account, dialog.root_combo, dialog.account,
                       dialog.help_acct, dialog.all_roots):
            assert widget.isHidden()
        assert dialog.all_roots.isChecked()
        for widget in (dialog.token, dialog.express_value, dialog.browse_btn, dialog.pair_btn):
            assert not widget.isHidden()
        # 语言、版本、开机启动只在主窗口（2026-10-08）
        for name in ("lang_btn", "ver_lbl", "autostart"):
            assert not hasattr(dialog, name)
        assert dialog.pair_btn.text() == "配对"
        from test_gui_profiles import _assert_free_window

        _assert_free_window(dialog)
    finally:
        gui_pairing.PairingDialog._autodetect = original


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
