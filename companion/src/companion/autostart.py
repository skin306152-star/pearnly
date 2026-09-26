# -*- coding: utf-8 -*-
"""开机自启(用户级 · 注册表 HKCU\\…\\Run · 免管理员)。

配对成功后写一条 Run 项,登录即后台拉起小助手。用标准库 winreg(跨 32/64 位、无需
pywin32)。disable 幂等(没装也不报错)。
"""
from __future__ import annotations

import sys
from typing import Optional

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "PearnlyCompanion"


def _winreg():
    import winreg

    return winreg


def default_command() -> str:
    """自启命令:打包后 = exe 路径(--background 静默);开发时 = python -m 模块。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --background'
    return f'"{sys.executable}" -m src.companion.main --background'


def enable(command: Optional[str] = None) -> str:
    winreg = _winreg()
    cmd = command or default_command()
    # CreateKeyEx:Run 键不存在(全新账户)时建出来;已存在时等同打开。
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, cmd)
    return cmd


def disable() -> None:
    winreg = _winreg()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, _VALUE_NAME)
    except FileNotFoundError:
        pass


def is_enabled() -> bool:
    winreg = _winreg()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ
        ) as key:
            winreg.QueryValueEx(key, _VALUE_NAME)  # 值存在即启用(缺失抛 FileNotFoundError)
            return True
    except FileNotFoundError:
        return False
