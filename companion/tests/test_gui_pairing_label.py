# -*- coding: utf-8 -*-
"""添加成员 / 更换密钥窗（2026-10-08）：只填配对码，账套用这台电脑的全局设置。无 PySide6 则跳过。"""

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from src.companion import gui_pairing  # noqa: E402
from src.companion.account_probe import AccountSet  # noqa: E402
from src.companion.gui_pairing import PairingDialog  # noqa: E402


def _acc(path, name="A"):
    return AccountSet(code="A", name=name, company=f"{name} Co.", path=path, tax_id="", writable=True, row=2)


def _bare(prefill=False, account_set=""):
    dialog = PairingDialog.__new__(PairingDialog)
    dialog._prefill = prefill
    dialog._cfg = SimpleNamespace(account_set=account_set, account_dir="", revenue_acc="4999", ar_acc="",
                                  vat_output_acc="", fallback_acc="", ap_acc="", vat_input_acc="")
    return dialog


def test_default_account_is_picked_without_asking():
    sets = [_acc(r"S:\\EXP68\\A"), _acc(r"S:\\EXP68\\B")]
    assert PairingDialog._prefill_account_index(_bare(False), sets) == 0
    assert PairingDialog._prefill_account_index(_bare(True, r"S:\\EXP68\\B"), sets) == 1
    assert PairingDialog._prefill_account_index(_bare(True, r"S:\\GONE"), sets) == 0
    assert PairingDialog._prefill_account_index(_bare(True), []) == -1


def test_changing_the_key_keeps_hand_edited_accounts_of_the_same_account_set():
    dialog = _bare(True, r"S:\\EXP68\\B")
    same = dialog._auto_mapping(_acc(r"S:\\EXP68\\B"), {"revenue_acc": "4100"})
    other = dialog._auto_mapping(_acc(r"S:\\EXP68\\A"), {"revenue_acc": "4100"})
    assert same["revenue_acc"] == "4999" and other["revenue_acc"] == "4100"


def test_pairing_uses_the_machine_roots_and_reports_every_account_set(monkeypatch):
    dialog = _bare(True, r"S:\\EXP68\\B")
    dialog._store = object()
    sets = [_acc(r"S:\\EXP68\\A"), _acc(r"S:\\EXP68\\B", "B")]
    calls = {}
    monkeypatch.setattr(gui_pairing, "probe_account_sets", lambda root: calls.setdefault("probed", root) and sets)
    monkeypatch.setattr(gui_pairing, "probe_default_accounts", lambda path: {})
    monkeypatch.setattr(gui_pairing.pairing, "pair", lambda **kwargs: calls.setdefault("pair", kwargs))

    dialog._pair_work("exp_e_s@testcowork.pearnly.com", [r"S:\\EXP68", r"S:\\EXP69"], True, "zh")

    assert calls["probed"] == r"S:\\EXP68"
    pair = calls["pair"]
    assert pair["chosen"].path == r"S:\\EXP68\\B" and pair["all_sets"] == sets
    assert pair["express_root"] == r"S:\\EXP68"
    assert pair["authorized_roots"] == [r"S:\\EXP68", r"S:\\EXP69"]
    assert pair["enable_autostart"] is True


def test_pair_spawn_sync_error_restores_controls_and_safe_status():
    dialog = PairingDialog.__new__(PairingDialog)
    dialog.token = SimpleNamespace(text=lambda: "exp_e_s")
    dialog._roots = [r"S:\\EXP68"]
    dialog.lang = "zh"
    dialog._store = SimpleNamespace(global_settings=SimpleNamespace(autostart=True))
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
