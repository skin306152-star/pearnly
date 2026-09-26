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
