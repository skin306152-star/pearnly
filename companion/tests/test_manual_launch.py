from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

from src.companion import main


class _Store:
    def __init__(self, profiles=()):
        self.profiles = list(profiles)

    def runtime_configs(self, *, include_paused=False):
        return list(self.profiles)


def _install_runtime_stubs(monkeypatch, *, reloaded_store):
    opened = []
    launched = []

    profiles = ModuleType("src.companion.gui_profiles")
    profiles.open_profile_manager = lambda: opened.append(True)
    monkeypatch.setitem(sys.modules, "src.companion.gui_profiles", profiles)

    profile_store = ModuleType("src.companion.profile_store")
    profile_store.ProfileStore = SimpleNamespace(load=lambda: reloaded_store)
    monkeypatch.setitem(sys.modules, "src.companion.profile_store", profile_store)

    tray = ModuleType("src.companion.gui_tray")

    class FakeTrayApp:
        def __init__(self, configs, *, open_tomo=False):
            launched.append((list(configs), open_tomo))

        def run(self):
            return 17

    tray.TrayApp = FakeTrayApp
    monkeypatch.setitem(sys.modules, "src.companion.gui_tray", tray)
    return opened, launched


def test_manual_first_launch_without_express_opens_tomo(monkeypatch):
    # 2026-10-10：小助手并入 Tomo：没配对 Express 也启动，直接打开 Tomo（连接在「连接与设置」里配）
    opened, launched = _install_runtime_stubs(monkeypatch, reloaded_store=_Store())

    result = main._run_dbf(_Store(), SimpleNamespace(background=False, headless=False))

    assert result == 17
    assert opened == []
    assert launched == [([], True)]


def test_background_start_stays_in_the_tray(monkeypatch):
    opened, launched = _install_runtime_stubs(monkeypatch, reloaded_store=_Store())

    result = main._run_dbf(_Store(), SimpleNamespace(background=True, headless=False))

    assert result == 17
    assert opened == []
    assert launched == [([], False)]


def test_headless_still_needs_a_paired_profile(monkeypatch):
    opened, launched = _install_runtime_stubs(monkeypatch, reloaded_store=_Store())

    assert main._run_dbf(_Store(), SimpleNamespace(background=False, headless=True)) == 2
    assert launched == []


def test_manual_launch_with_profile_opens_tomo(monkeypatch):
    configured = SimpleNamespace(
        profile_id="profile-1",
        paused=False,
        account_dir=r"C:\Express\TEST",
        account_set="TEST",
    )
    opened, launched = _install_runtime_stubs(
        monkeypatch, reloaded_store=_Store([configured])
    )

    result = main._run_dbf(
        _Store([configured]), SimpleNamespace(background=False, headless=False)
    )

    assert result == 17
    assert opened == []
    assert launched == [([configured], True)]
