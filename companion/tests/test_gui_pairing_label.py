# -*- coding: utf-8 -*-
"""配对窗下拉项文案 = Express grid 显示名(COMPNAM)· 无 PySide6 则跳过。"""

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from src.companion.account_probe import AccountSet  # noqa: E402
from src.companion.gui_pairing import PairingDialog, account_choice_label  # noqa: E402
from src.companion.gui_pairing_support import set_pairing_busy  # noqa: E402


def _acc(name, tax="0735527000289"):
    return AccountSet(
        code="DATAT",
        name=name,
        company="บริษัท มานะชัยบริการ จำกัด",
        path="X",
        tax_id=tax,
        writable=True,
        row=2,
    )


def test_label_is_grid_name_with_tax_tail():
    label = account_choice_label(_acc("9.ข้อมูลทดสอบเวอร์ชั่น 1"))
    assert "9.ข้อมูลทดสอบเวอร์ชั่น 1" in label and "0289" in label  # grid 名 + 税号尾段


def test_label_falls_back_to_code():
    label = account_choice_label(_acc("", tax=""))
    assert "DATAT" in label  # 无 grid 名回落代号


def test_pairing_busy_disables_every_mutable_control():
    class Control:
        def __init__(self):
            self.enabled = True

        def setEnabled(self, enabled):
            self.enabled = enabled

    names = (
        "lang_btn",
        "cancel_btn",
        "browse_btn",
        "root_combo",
        "account",
        "all_roots",
        "token",
        "autostart",
        "pair_btn",
    )
    dialog = SimpleNamespace(
        **{name: Control() for name in names},
        _pairing_in_progress=False,
    )

    set_pairing_busy(dialog, True)
    assert dialog._pairing_in_progress is True
    assert not any(getattr(dialog, name).enabled for name in names)

    set_pairing_busy(dialog, False)
    assert dialog._pairing_in_progress is False
    assert all(getattr(dialog, name).enabled for name in names)


def test_pair_spawn_sync_error_restores_controls_and_safe_status():
    dialog = PairingDialog.__new__(PairingDialog)
    dialog.account = SimpleNamespace(currentData=lambda: object())
    dialog._auto_mapping = lambda _chosen: {}
    dialog.token = SimpleNamespace(text=lambda: "exp_e_s")
    dialog._express_root = r"S:\\EXP68"
    dialog._sets = []
    dialog.autostart = SimpleNamespace(isChecked=lambda: True)
    dialog.lang = "zh"
    dialog._cfg = object()
    dialog._store = object()
    busy, errors = [], []
    dialog._set_pairing_busy = busy.append
    dialog._set_status = lambda *_args, **_kwargs: None
    dialog._set_pair_error = errors.append

    def fail_spawn(*_args):
        raise RuntimeError("QThread start failed")

    dialog._spawn = fail_spawn

    dialog._on_pair()

    assert busy == [True, False]
    assert errors == ["pair_err_unexpected"]
