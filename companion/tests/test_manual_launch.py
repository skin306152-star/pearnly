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
        def __init__(self, configs, *, open_connections=False):
            launched.append((list(configs), open_connections))

        def run(self):
            return 17

    tray.TrayApp = FakeTrayApp
    monkeypatch.setitem(sys.modules, "src.companion.gui_tray", tray)
    return opened, launched


def test_manual_first_launch_opens_connection_manager(monkeypatch):
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
        _Store(), SimpleNamespace(background=False, headless=False)
    )

    assert result == 17
    assert opened == [True]
    assert launched == [([configured], True)]


def test_background_start_never_opens_connection_manager(monkeypatch):
    opened, launched = _install_runtime_stubs(monkeypatch, reloaded_store=_Store())

    result = main._run_dbf(
        _Store(), SimpleNamespace(background=True, headless=False)
    )

    assert result == 2
    assert opened == []
    assert launched == []


def test_manual_launch_with_profile_opens_manager_from_tray(monkeypatch):
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
