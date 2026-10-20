# -*- coding: utf-8 -*-
"""开机自启(用户级 · 免管理员)。

Inno 安装包装的(主发布方式):注册表 HKCU\\…\\Run 的 ``PearnlyCompanion`` 一项,登录即后台拉起。用标准库 winreg
(跨 32/64 位、无需 pywin32)。disable 幂等(没装也不报错)。卸载时由安装包的卸载程序删掉这一项(installer.iss)。

Microsoft Store 装的(以 MSIX 包身份运行,package_identity.packaged()):包内写 HKCU 会被系统按包虚拟化,Run 项
登录时不生效,改用清单里声明的 StartupTask(packaging/msix/AppxManifest.xml,TaskId 见 package_identity,带
``--background``)。开 / 关 / 读状态经 WinRT 的 ``Windows.ApplicationModel.StartupTask``;用户在任务管理器「启动应用」
里关掉的(DisabledByUser),程序打不开,按系统规矩只能用户自己再开。卸载包时系统一并清掉。
"""
from __future__ import annotations

import sys
from typing import Optional

from . import package_identity

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "PearnlyCompanion"   # 名字不改(2026-10-20):老用户的开机自启项原样沿用,任务管理器里显示的是 exe 的「CC」
_STARTUP_TIMEOUT = 10.0


def _winreg():
    import winreg

    return winreg


def default_command() -> str:
    """自启命令:打包后 = exe 路径(--background 静默);开发时 = python -m 模块。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --background'
    return f'"{sys.executable}" -m src.companion.main --background'


# ── Microsoft Store 版:StartupTask ──
def _startup_task(action: str) -> bool:
    """在单独的线程里跑 WinRT 的异步调用(界面线程是 STA,不在上面等异步结果)。返回调用后是否启用。"""
    import asyncio
    import threading

    result: dict = {}

    async def run():
        from winrt.windows.applicationmodel import StartupTask, StartupTaskState

        task = await StartupTask.get_async(package_identity.STARTUP_TASK_ID)
        if action == "enable" and task.state == StartupTaskState.DISABLED:
            await task.request_enable_async()
        elif action == "disable" and task.state == StartupTaskState.ENABLED:
            task.disable()
        return task.state in (StartupTaskState.ENABLED, StartupTaskState.ENABLED_BY_POLICY)

    def worker():
        try:
            result["enabled"] = asyncio.run(run())
        except BaseException as error:  # noqa: BLE001 — 交回调用方的线程再抛
            result["error"] = error

    thread = threading.Thread(target=worker, name="cc-startup-task", daemon=True)
    thread.start()
    thread.join(_STARTUP_TIMEOUT)
    if "error" in result:
        raise result["error"]
    if "enabled" not in result:
        raise TimeoutError("StartupTask did not answer")
    return bool(result["enabled"])


def enable(command: Optional[str] = None) -> str:
    if package_identity.packaged():
        _startup_task("enable")
        return ""
    winreg = _winreg()
    cmd = command or default_command()
    # CreateKeyEx:Run 键不存在(全新账户)时建出来;已存在时等同打开。
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, cmd)
    return cmd


def disable() -> None:
    if package_identity.packaged():
        _startup_task("disable")
        return
    winreg = _winreg()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, _VALUE_NAME)
    except FileNotFoundError:
        pass


def repoint() -> bool:
    """开机自启已开、但指向的不是本程序(例如从 Program Files 迁到用户目录安装后,2026-10-08;
    目录从「Pearnly Companion」改成「CC」后,2026-10-20):改指向本程序。

    只在打包后的程序里做(开发时 sys.executable 是 python,不能写进开机启动);Store 版用 StartupTask,不碰 Run。返回是否改了。
    """
    if not getattr(sys, "frozen", False) or package_identity.packaged():
        return False
    winreg = _winreg()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ
        ) as key:
            current, _kind = winreg.QueryValueEx(key, _VALUE_NAME)
    except FileNotFoundError:
        return False  # 没开开机自启:不替人打开
    wanted = default_command()
    if str(current).strip().casefold() == wanted.casefold():
        return False
    enable(wanted)
    return True


def is_enabled() -> bool:
    if package_identity.packaged():
        return _startup_task("read")
    winreg = _winreg()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ
        ) as key:
            winreg.QueryValueEx(key, _VALUE_NAME)  # 值存在即启用(缺失抛 FileNotFoundError)
            return True
    except FileNotFoundError:
        return False
