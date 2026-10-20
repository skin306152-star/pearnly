# -*- coding: utf-8 -*-
"""Microsoft Store 版（以 MSIX 包身份运行，教程问题第 1 条）：判据，以及包身份下开机自启、更新、通知的换法。"""
import sys
import types

from src.companion import autostart, package_identity, toast


def test_plain_installs_have_no_package_identity():
    # 开发机、CI、Inno 安装包装的都没有包身份（CI 的 Windows 上真调 GetCurrentPackageFullName）
    package_identity.full_name.cache_clear()
    package_identity.app_user_model_id.cache_clear()
    assert package_identity.full_name() == ""
    assert package_identity.packaged() is False
    assert package_identity.app_user_model_id() == ""
    assert toast.app_id() == toast.AUMID


def _packaged(monkeypatch, aumid="12345Pearnly.CC_abcdefghijklm!CC"):
    monkeypatch.setattr(package_identity, "full_name", lambda: "12345Pearnly.CC_1.9.1.0_x64__abcdefghijklm")
    monkeypatch.setattr(package_identity, "app_user_model_id", lambda: aumid)


def test_store_install_uses_the_startup_task_instead_of_the_run_key(monkeypatch):
    _packaged(monkeypatch)
    calls = []
    monkeypatch.setattr(autostart, "_startup_task", lambda action: calls.append(action) or action != "disable")
    monkeypatch.setattr(autostart, "_winreg", lambda: (_ for _ in ()).throw(AssertionError("Run key touched")))
    assert autostart.enable() == ""
    autostart.disable()
    assert autostart.is_enabled() is True
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert autostart.repoint() is False  # Store 版不碰 Run 项
    assert calls == ["enable", "disable", "read"]


def test_startup_task_states(monkeypatch):
    # WinRT 的 StartupTask：没开就请求开；用户在任务管理器里关的（DisabledByUser）程序开不了，如实回「没开」
    class State:
        DISABLED, DISABLED_BY_USER, ENABLED, DISABLED_BY_POLICY, ENABLED_BY_POLICY = range(5)

    class Task:
        def __init__(self, state):
            self.state = state
            self.calls = []

        async def request_enable_async(self):
            self.calls.append("request")
            self.state = State.ENABLED if self.state == State.DISABLED else self.state
            return self.state

        def disable(self):
            self.calls.append("disable")
            self.state = State.DISABLED

    tasks = {}

    class StartupTask:
        @staticmethod
        async def get_async(task_id):
            assert task_id == package_identity.STARTUP_TASK_ID
            return tasks["current"]

    module = types.ModuleType("winrt.windows.applicationmodel")
    module.StartupTask, module.StartupTaskState = StartupTask, State
    for name in ("winrt", "winrt.windows"):
        monkeypatch.setitem(sys.modules, name, sys.modules.get(name) or types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "winrt.windows.applicationmodel", module)

    tasks["current"] = Task(State.DISABLED)
    assert autostart._startup_task("enable") is True and tasks["current"].calls == ["request"]
    assert autostart._startup_task("disable") is False and tasks["current"].calls == ["request", "disable"]
    tasks["current"] = Task(State.DISABLED_BY_USER)
    assert autostart._startup_task("enable") is False and tasks["current"].calls == []
    tasks["current"] = Task(State.ENABLED_BY_POLICY)
    assert autostart._startup_task("read") is True
    assert autostart._startup_task("disable") is True and tasks["current"].calls == []  # 策略开的关不了


def test_store_install_notifies_under_the_package_app_id(monkeypatch):
    _packaged(monkeypatch)
    assert toast.app_id() == "12345Pearnly.CC_abcdefghijklm!CC"
    _packaged(monkeypatch, aumid="")
    assert toast.app_id() == toast.AUMID


def test_store_install_leaves_updates_to_the_store(monkeypatch):
    from src.companion import gui_tray

    _packaged(monkeypatch)
    assert gui_tray._store_updates() is True

    offered = []
    tray = types.SimpleNamespace(_auto_update=types.SimpleNamespace(offer=lambda *a: offered.append(a) or True),
                                 _quitting=False, _updating=False, _checking=None)
    gui_tray.TrayApp._on_update_available(tray, "9.9.9", "/downloads/companion/CC-Setup.exe")
    gui_tray.TrayApp._check_for_update(tray, manual=True)
    assert offered == [] and tray._checking is None
