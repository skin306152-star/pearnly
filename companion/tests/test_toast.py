# -*- coding: utf-8 -*-
"""Windows 应用通知（toast.py）：2026-10-13 测试机实测托盘气泡锁屏时不进通知中心，桌面通知改走 windows-toasts，
发不出来返回 False 由托盘退回气泡。这里用假模块测，不真弹系统通知（conftest 默认把库当作没装）。"""

import ast
import sys
import types
from pathlib import Path

from src.companion import toast

ROOT = Path(__file__).resolve().parents[1]


class _Toast:
    def __init__(self, text_fields=None):
        self.text_fields = list(text_fields or [])
        self.tag = "library-default-tag"
        self.on_activated = None


class _Notifier:
    def __init__(self):
        self.shown = []

    def show(self, notification):
        self.shown.append(notification)

    def clear(self):
        return "cleared"


def _fake_library(monkeypatch, fail=False):
    toasters = []

    class Toaster:
        def __init__(self, applicationText, notifierAUMID=None):
            self.ids = (applicationText, notifierAUMID)
            self.toastNotifier = _Notifier()
            self.toasts = []
            toasters.append(self)

        def show_toast(self, item):
            if fail:
                raise OSError("Element not found")
            self.toasts.append(item)
            self.toastNotifier.show(("notification", item.tag))

    module = types.ModuleType("windows_toasts")
    module.Toast = _Toast
    module.InteractableWindowsToaster = Toaster
    monkeypatch.setitem(sys.modules, "windows_toasts", module)
    monkeypatch.setattr(toast, "_WINDOWS", True)
    monkeypatch.setattr(toast, "_toaster", None)
    return toasters


def test_without_windows_or_the_library_nothing_is_shown_and_nothing_raises(monkeypatch):
    monkeypatch.setattr(toast, "_WINDOWS", False)
    assert toast.show("T", "B", "notice:p1", lambda: None) is False
    monkeypatch.setattr(toast, "_WINDOWS", True)   # Windows 但没装库（conftest 把 windows_toasts 设成导入失败）
    assert toast.show("T", "B", "notice:p1", lambda: None) is False


def test_the_library_is_only_imported_when_a_notification_is_shown():
    # 开发机与测试装不了 Windows 专用的库：模块顶层不能导入它
    tree = ast.parse((ROOT / "src" / "companion" / "toast.py").read_text(encoding="utf-8"))
    top = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    names = [alias.name for node in top for alias in node.names] + [node.module or "" for node in top
                                                                  if isinstance(node, ast.ImportFrom)]
    assert not any("windows_toasts" in name or name.startswith("winrt") for name in names)


def test_notifications_go_out_under_the_cc_app_id_with_title_body_and_tag(monkeypatch):
    toasters = _fake_library(monkeypatch)
    clicks = []

    assert toast.show("New reply", "2 files waiting", "notice:p1", lambda: clicks.append("open")) is True
    assert toast.show("CC", "done", "", None) is True
    assert toast.show("CC", "x" * 3, "n" * 200, None) is True
    assert toast.show("CC", "x" * 3, "n" * 200, None) is True

    assert len(toasters) == 1   # 发通知的对象只建一次
    assert toasters[0].ids == ("CC", toast.AUMID)
    first, plain, long_a, long_b = toasters[0].toasts
    assert first.text_fields == ["New reply", "2 files waiting"] and first.tag == "notice:p1"
    assert plain.tag == "library-default-tag" and plain.on_activated is None   # 没 tag：每条都留着
    assert len(long_a.tag) <= toast.TAG_MAX and long_a.tag == long_b.tag        # 超长 tag 换成同一个摘要
    first.on_activated(object())   # 系统线程上的点击：只调调用方给的钩子
    assert clicks == ["open"]
    # 发出去的通知对象留着引用（点击回调挂在上面），其余调用原样转给系统的发送器
    assert len(toasters[0].toastNotifier.shown) == 4 and toasters[0].toastNotifier.clear() == "cleared"


def test_a_failing_click_hook_or_a_refused_notification_never_raises(monkeypatch):
    toasters = _fake_library(monkeypatch)

    def broken():
        raise RuntimeError("window gone")

    assert toast.show("T", "B", "", broken) is True
    toasters[0].toasts[0].on_activated(object())   # 不抛

    _fake_library(monkeypatch, fail=True)
    assert toast.show("T", "B", "", None) is False   # 系统拒绝 → 托盘退回气泡


def test_the_app_id_matches_the_process_and_the_start_menu_shortcut():
    from src.companion import main

    assert toast.AUMID == main.APP_USER_MODEL_ID
    iss = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    assert f'AppUserModelID: "{toast.AUMID}"' in iss


def test_the_windows_notification_library_is_pinned_and_packaged():
    # CI 用 --no-deps 装：库与它的每个间接依赖都要精确锁版本；打包时 winrt 的原生模块在运行时才导入，要显式列出
    pins = {line.split("==")[0].lower(): line.split("==")[1]
            for line in (ROOT / "packaging" / "requirements-build.txt").read_text(encoding="utf-8").splitlines()
            if "==" in line and not line.startswith("#")}
    for name in ("windows-toasts", "winrt-runtime", "winrt-windows.data.xml.dom", "winrt-windows.foundation",
                 "winrt-windows.foundation.collections", "winrt-windows.ui.notifications", "typing_extensions"):
        assert name in pins, name
    spec = (ROOT / "packaging" / "companion.spec").read_text(encoding="utf-8")
    for module in ("windows_toasts", "winrt.windows.ui.notifications", "winrt.windows.foundation.collections"):
        assert f'"{module}"' in spec, module
