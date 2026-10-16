# -*- coding: utf-8 -*-
"""设备密钥的第二份存放处与这台电脑的系统标识（多台电脑，ADR-021；CC 桌面版 1.7.0 起）。

设备密钥（``device_link``）一变，这台电脑的所有成员都会被服务端判 ``claimed_elsewhere`` 而停用，没人在电脑前时更没人去点。
所以它不能只靠 ``companion.json`` 里的一份：

- **备份**：同一份 DPAPI 密文另存在 ``HKCU\\Software\\Pearnly\\CC Desktop`` 的 ``Device`` 值里（只有当前 Windows 用户能解）。
  旧版（≤1.6）重写 ``companion.json`` 会丢掉设备密钥那一键，但从不碰这个注册表项：再升级时从这里恢复，不换密钥。
- **系统标识**：``HKLM\\SOFTWARE\\Microsoft\\Cryptography`` 的 ``MachineGuid``（普通用户可读，按 64 位视图读）。随密钥一起存下；
  用同一个系统镜像克隆出来的电脑会带着同一份配置和同一把密钥，但 sysprep 过的系统标识不同 → 重新生成，不两台共用一把。
  读不出标识（不是 Windows、注册表读不了）一律不比较。

都只在 Windows 上起作用；别的系统上备份读不到、写不进，标识为空，行为与没有这一层相同。从不抛。
"""
from __future__ import annotations

import json
import sys
from typing import Optional, Protocol

from .logger import get_logger

log = get_logger(__name__)

BACKUP_KEY = r"Software\Pearnly\CC Desktop"
BACKUP_VALUE = "Device"


class DeviceBackup(Protocol):
    def read(self) -> dict: ...          # {"key": 密文, "machine": 系统标识}；没有 / 读不了回 {}
    def write(self, data: dict) -> None: ...


def clean(data) -> dict:
    """只留两个字符串键；``key`` 为空当没有。"""
    if not isinstance(data, dict):
        return {}
    key = data.get("key")
    machine = data.get("machine")
    if not isinstance(key, str) or not key:
        return {}
    return {"key": key, "machine": machine if isinstance(machine, str) else ""}


class RegistryBackup:
    """``HKCU\\Software\\Pearnly\\CC Desktop`` 的 ``Device`` 值（JSON 字符串）。"""

    def read(self) -> dict:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, BACKUP_KEY) as key:
                raw, _kind = winreg.QueryValueEx(key, BACKUP_VALUE)
            return clean(json.loads(str(raw or "")))
        except Exception:
            return {}

    def write(self, data: dict) -> None:
        payload = clean(data)
        if not payload:
            return
        try:
            import winreg

            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, BACKUP_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, BACKUP_VALUE, 0, winreg.REG_SZ, json.dumps(payload))
        except Exception:
            log.warning("设备密钥的备份写不进注册表（忽略）", exc_info=True)


class NoBackup:
    def read(self) -> dict:
        return {}

    def write(self, data: dict) -> None:
        return None


def default_backup() -> DeviceBackup:
    return RegistryBackup() if sys.platform == "win32" else NoBackup()


def machine_id() -> str:
    """Windows 的 ``MachineGuid``；读不出回空串。"""
    if sys.platform != "win32":
        return ""
    try:
        import winreg

        access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, access) as key:
            value, _kind = winreg.QueryValueEx(key, "MachineGuid")
        return str(value or "").strip().lower()
    except Exception:
        return ""


def other_machine(stored: Optional[str], current: Optional[str]) -> bool:
    """存下的系统标识与这台的不同（两边都读得出才比较）。"""
    left, right = str(stored or "").strip().lower(), str(current or "").strip().lower()
    return bool(left and right and left != right)


__all__ = ["BACKUP_KEY", "BACKUP_VALUE", "DeviceBackup", "NoBackup", "RegistryBackup", "clean", "default_backup",
           "machine_id", "other_machine"]
