# -*- coding: utf-8 -*-
"""开机自启(注册表 HKCU Run · 用户级)单测。真写 HKCU(安全·测后清)。"""
import sys

import pytest

from src.companion import autostart

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_enable_then_detected():
    autostart.disable()
    try:
        assert not autostart.is_enabled()
        cmd = autostart.enable('"X.exe" --background')
        assert "--background" in cmd
        assert autostart.is_enabled()
    finally:
        autostart.disable()


def test_disable_idempotent():
    autostart.disable()
    autostart.disable()  # 没装也不报错
    assert not autostart.is_enabled()


def test_default_command_has_background_flag():
    assert "--background" in autostart.default_command()


def test_repoint_moves_an_enabled_autostart_to_this_install_only(monkeypatch):
    # 2026-10-08：从 Program Files 迁到用户目录后，开机自启改指向新位置；没开的不替人打开；开发时不碰。
    import sys
    import types

    from src.companion import autostart

    store = {}

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def query(_key, name):
        if name not in store:
            raise FileNotFoundError(name)
        return store[name], 1

    fake = types.SimpleNamespace(HKEY_CURRENT_USER=0, KEY_READ=1, KEY_SET_VALUE=2, REG_SZ=1,
                                 OpenKey=lambda *_a: Key(), CreateKeyEx=lambda *_a: Key(), QueryValueEx=query,
                                 SetValueEx=lambda _key, name, _r, _t, value: store.__setitem__(name, value))
    monkeypatch.setattr(autostart, "_winreg", lambda: fake)
    monkeypatch.setattr(autostart, "default_command", lambda: '"C:\\Users\\a\\AppData\\Local\\Programs\\Pearnly Companion\\companion.exe" --background')

    monkeypatch.setattr(sys, "frozen", False, raising=False)
    store["PearnlyCompanion"] = '"C:\\Program Files\\Pearnly Companion\\companion.exe" --background'
    assert autostart.repoint() is False  # 开发时不碰

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert autostart.repoint() is True
    assert "AppData" in store["PearnlyCompanion"]
    assert autostart.repoint() is False  # 已经指向本程序

    store.clear()
    assert autostart.repoint() is False and store == {}  # 没开：不替人打开
