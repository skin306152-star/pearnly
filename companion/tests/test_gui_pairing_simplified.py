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
        for widget in (dialog.token, dialog.autostart, dialog.pair_btn):
            assert not widget.isHidden()
    finally:
        gui_pairing.PairingDialog._autodetect = original


def test_tray_status_line_is_capped():
    long = "บริษัท " * 30 + " · 离线 · 云端推送未开启"
    assert len(_menu_text(long)) == MENU_TEXT_MAX
    assert _menu_text("离线") == "离线"
