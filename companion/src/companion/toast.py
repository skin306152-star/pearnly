# -*- coding: utf-8 -*-
"""Windows 应用通知：CC 的桌面通知先走这里，锁屏时弹出的也留在通知中心。

2026-10-13 办公室测试机（Windows 11，CC 1.4.4，常驻模式锁屏）实测：托盘气泡（QSystemTrayIcon.showMessage）
不进通知中心（用户的 wpndatabase.db 里没有记录），用同一个 AppUserModelID「Pearnly.Companion」发的 Windows
应用通知会留下。所以改用 windows-toasts 库（依赖逐个锁在 packaging/requirements-build.txt），发不出来
（非 Windows、缺库、系统拒绝）就返回 False，由托盘退回原来的气泡。

点通知的回调在系统线程上：这里只调用调用方给的线程安全钩子（托盘那边是发 Qt 信号、排队回界面线程），不碰窗口。
"""

from __future__ import annotations

import hashlib
import sys
from collections import deque

from .logger import get_logger

log = get_logger(__name__)

# 与 main.APP_USER_MODEL_ID、installer.iss 开始菜单快捷方式一致：通知归在 CC 名下，才进通知中心。
AUMID = "Pearnly.Companion"
APP_NAME = "CC"
TAG_MAX = 64  # Windows 通知 tag 的长度上限
KEEP = 64     # 留住最近这么多条已发通知：点击回调挂在通知对象上，这边丢了引用就可能收不到点击

_WINDOWS = sys.platform == "win32"
_toaster = None  # (windows_toasts 模块, 发通知的对象)：第一次发时才建


def app_id() -> str:
    """通知归属的 AppUserModelID：Microsoft Store 装的（以 MSIX 包身份运行）用包里这个应用的，否则用 ``AUMID``。"""
    from . import package_identity

    return package_identity.app_user_model_id() or AUMID


def show(title: str, body: str, tag: str = "", on_click=None) -> bool:
    """发一条 Windows 应用通知，发出去了返回 True；同一 tag 的新通知替换旧的。从不抛异常。

    on_click 在系统线程上被调用，必须线程安全（只发信号 / 排队），不能直接碰界面。"""
    if not _WINDOWS:
        return False
    try:
        import windows_toasts  # 只在 Windows 构建里有；导入放这里，开发机与测试不受影响
    except Exception as exc:  # 没装库，或系统版本太旧（库导入时就报）
        log.info("Windows 应用通知不可用，改用托盘气泡：%s", exc)
        return False
    try:
        toast = windows_toasts.Toast([str(title or ""), str(body or "")])
        clean = _tag(tag)
        if clean:
            toast.tag = clean  # 不给就是库自带的随机 tag，每条都留着
        if on_click is not None:
            toast.on_activated = lambda _args, hook=on_click: _run(hook)
        _get_toaster(windows_toasts).show_toast(toast)
        return True
    except Exception as exc:
        log.info("Windows 应用通知发送失败，改用托盘气泡：%s", exc)
        return False


def _tag(tag) -> str:
    """调用方的 tag 原样用；超过系统上限的换成定长摘要（同一个 tag 摘要也相同，照样替换旧通知）。"""
    value = str(tag or "").strip()
    if len(value) <= TAG_MAX:
        return value
    return "h:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:40]


def _run(hook) -> None:
    try:
        hook()
    except Exception:
        log.exception("通知点击回调失败")


def _get_toaster(module):
    global _toaster
    if _toaster is None or _toaster[0] is not module:
        # WindowsToaster 只收一个名字并把它当 AppUserModelID；这个类才能分开给显示名与 AppUserModelID。
        toaster = module.InteractableWindowsToaster(APP_NAME, notifierAUMID=app_id())
        toaster.toastNotifier = _Keep(toaster.toastNotifier)
        _toaster = (module, toaster)
    return _toaster[1]


class _Keep:
    """包住系统的通知发送器：发出的通知对象留最近 KEEP 条引用，其余调用原样转过去。"""

    def __init__(self, notifier) -> None:
        self._notifier = notifier
        self.shown: deque = deque(maxlen=KEEP)

    def show(self, notification) -> None:
        self.shown.append(notification)
        self._notifier.show(notification)

    def __getattr__(self, name):
        return getattr(self._notifier, name)
