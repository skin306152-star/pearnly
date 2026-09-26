from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from src.companion.gui_profiles import (
    ProfileManagerDialog,
    masked_token,
    profile_item_text,
    profile_status_key,
)  # noqa: E402
from src.companion import gui_profiles  # noqa: E402


def test_profile_item_shows_only_the_pearnly_account_and_state():
    # 账套 / 年度由网页第一步选择（2026-09-26）：列表不显示公司、账套、端点、密钥。
    active = SimpleNamespace(
        account_company="Acme Co.",
        account_dir=r"S:\\EXP68\\ACME",
        account_set="",
        paused=False,
        endpoint_name="Cowork Express",
        pearnly_account="owner@example.com",
    )
    paused = SimpleNamespace(**{**vars(active), "paused": True})

    assert profile_item_text(active, "zh", ("idle", ""), "exp_secret-1234") == (
        "owner@example.com · Pearnly 已连接"
    )
    assert "พักการใช้งาน" in profile_item_text(paused, "th", ("idle", ""))


def test_masked_token_never_exposes_the_middle():
    assert masked_token("exp_1234567890abcd") == "exp***abcd"
    assert masked_token("short") == "***"


def test_profile_status_maps_worker_snapshot_and_paused_wins():
    active = SimpleNamespace(paused=False)
    paused = SimpleNamespace(paused=True)

    assert profile_status_key(active, ("idle", "")) == "profiles_online"
    assert profile_status_key(active, ("working", "INV-1")) == "profiles_processing"
    assert profile_status_key(active, ("indexing", "")) == "profiles_processing"
    assert profile_status_key(active, ("waiting_lock", "")) == "profiles_attention"
    assert profile_status_key(active, ("error", "")) == "profiles_attention"
    assert profile_status_key(active, ("offline", "")) == "profiles_offline"
    assert profile_status_key(active, None) == "profiles_connecting"
    assert profile_status_key(paused, ("idle", "")) == "profiles_paused"


def test_successful_profile_change_notifies_runtime_and_keeps_close_fallback():
    calls = []
    dialog = ProfileManagerDialog.__new__(ProfileManagerDialog)
    dialog.changed = False
    dialog._on_profiles_changed = lambda: calls.append("reload")

    dialog._profiles_changed()

    assert dialog.changed is True
    assert calls == ["reload"]


def test_open_manager_refreshes_persisted_identity_and_live_state(monkeypatch):
    old_store = SimpleNamespace(path=Path("profiles.json"), profiles=("old",))
    new_store = SimpleNamespace(path=old_store.path, profiles=("new",))
    dialog = ProfileManagerDialog.__new__(ProfileManagerDialog)
    dialog._store = old_store
    dialog._profile_states = {}
    dialog._profile_state_provider = lambda: {"profile-a": ("idle", "")}
    dialog._selected_profile_id = lambda: "profile-a"
    refreshed = []
    dialog._refresh = refreshed.append
    monkeypatch.setattr(gui_profiles.ProfileStore, "load", lambda _path: new_store)

    dialog._refresh_runtime_snapshot()

    assert dialog._store is new_store
    assert dialog._profile_states == {"profile-a": ("idle", "")}
    assert refreshed == ["profile-a"]
