from __future__ import annotations

import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest

from src.companion import health_check, main
from src.companion.health_check import CheckItem


class _BoundSignal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


class _SignalDescriptor:
    def __init__(self, *_args):
        self._name = ""

    def __set_name__(self, _owner, name):
        self._name = name

    def __get__(self, instance, _owner):
        if instance is None:
            return self
        key = f"_fake_signal_{self._name}"
        if not hasattr(instance, key):
            setattr(instance, key, _BoundSignal())
        return getattr(instance, key)


def _gui_tray_with_fake_qt(monkeypatch):
    class FakeQThread:
        finished = _SignalDescriptor()

        def __init__(self, *_args, **_kwargs):
            pass

    class Dummy:
        def __init__(self, *_args, **_kwargs):
            pass

    pyside = ModuleType("PySide6")
    qtcore = ModuleType("PySide6.QtCore")
    qtcore.Qt = SimpleNamespace()
    qtcore.QObject = Dummy
    qtcore.QThread = FakeQThread
    qtcore.QTimer = Dummy
    qtcore.Signal = _SignalDescriptor
    qtgui = ModuleType("PySide6.QtGui")
    qtwidgets = ModuleType("PySide6.QtWidgets")
    for name in ("QColor", "QIcon", "QPainter", "QPixmap"):
        setattr(qtgui, name, Dummy)
    for name in ("QApplication", "QMenu", "QMessageBox", "QSystemTrayIcon"):
        setattr(qtwidgets, name, Dummy)
    # 托盘还会带进其它窗口模块：没列出的 Qt 名字一律给个空壳。
    for module in (qtcore, qtgui, qtwidgets):
        module.__getattr__ = lambda name: Dummy
    monkeypatch.setitem(sys.modules, "PySide6", pyside)
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", qtcore)
    monkeypatch.setitem(sys.modules, "PySide6.QtGui", qtgui)
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", qtwidgets)
    # 假 Qt 版的托盘模块只在本测试里用：测试结束换回真的，后面的测试不受影响
    import src.companion as package

    for name in ("gui_tray", "gui_tomo"):
        monkeypatch.delitem(sys.modules, "src.companion." + name, raising=False)
        monkeypatch.delattr(package, name, raising=False)   # 包上的同名属性也要换回去
    return importlib.import_module("src.companion.gui_tray")


class _LifecycleWorker:
    def __init__(self):
        self.finished = _BoundSignal()
        self.running = True
        self.stop_calls = 0
        self.offline_signals = []

    def isRunning(self):
        return self.running

    def stop(self, *, signal_offline=True):
        self.stop_calls += 1
        self.offline_signals.append(signal_offline)

    def finish(self):
        self.running = False
        self.finished.emit()


class _CallSink:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1


def _cfg(profile_id: str, *, paused: bool = False):
    return SimpleNamespace(
        profile_id=profile_id,
        paused=paused,
        backend_url="https://pearnly.com",
        bearer_token=f"token-{profile_id}",
        companion_id="one-machine",
    )


def test_headless_entry_uses_one_scheduler_and_offlines_every_client(monkeypatch):
    created = []

    class FakeScheduler:
        def __init__(self, configs, **kwargs):
            self.configs = list(configs)
            self.kwargs = kwargs
            self.closed = None
            created.append(self)

        def run_forever(self, should_stop):
            assert callable(should_stop)

        def close(self, *, signal_offline=False):
            self.closed = signal_offline

    import src.companion.multi_profile_worker as runtime

    monkeypatch.setattr(runtime, "MultiProfileWorker", FakeScheduler)
    configs = [_cfg("A"), _cfg("B")]

    main.step_poll_loop(configs)

    assert len(created) == 1
    assert created[0].configs == configs
    assert created[0].closed is True


def test_health_check_reports_each_active_profile_independently(monkeypatch):
    checked = []

    def fake_check(cfg):
        checked.append(cfg.profile_id)
        return CheckItem("backend", cfg.profile_id == "B", cfg.profile_id)

    monkeypatch.setattr(health_check, "_check_backend", fake_check)
    report = health_check.run_health_check(
        [_cfg("A"), _cfg("P", paused=True), _cfg("B")]
    )

    assert checked == ["A", "B"]
    assert [item.name for item in report.items] == ["backend:A", "backend:B"]
    assert report.overall_ok is False


def test_restart_coalesces_and_spawns_only_after_old_worker_finished(monkeypatch):
    gui_tray = _gui_tray_with_fake_qt(monkeypatch)
    app = gui_tray.TrayApp.__new__(gui_tray.TrayApp)
    old = _LifecycleWorker()
    app.worker = old
    app.configs = ["initial"]
    app._restart_pending = False
    app._quitting = False
    spawned = []

    def spawn():
        worker = _LifecycleWorker()
        worker.config_snapshot = list(app.configs)
        worker.finished.connect(lambda current=worker: app._on_worker_finished(current))
        spawned.append(worker)
        return worker

    app._spawn_worker = spawn
    old.finished.connect(lambda current=old: app._on_worker_finished(current))

    app.configs = ["first change"]
    app._restart_worker()
    app.configs = ["latest change"]
    app._restart_worker()

    assert spawned == []
    assert old.stop_calls == 2
    assert old.offline_signals == [False, False]
    old.finish()
    assert len(spawned) == 1
    assert spawned[0].config_snapshot == ["latest change"]
    assert app.worker is spawned[0]


def test_quit_waits_for_worker_and_never_respawns(monkeypatch):
    gui_tray = _gui_tray_with_fake_qt(monkeypatch)
    monkeypatch.setattr(gui_tray, "activity", lambda *_args, **_kwargs: None)
    app = gui_tray.TrayApp.__new__(gui_tray.TrayApp)
    old = _LifecycleWorker()
    hide, quit_app, stop_watchdog = _CallSink(), _CallSink(), _CallSink()
    app.worker = old
    app.lang = "zh"
    app.tray = SimpleNamespace(hide=hide)
    app.app = SimpleNamespace(quit=quit_app)
    app._watchdog = SimpleNamespace(stop=stop_watchdog)
    app._restart_pending = True
    app._quitting = False
    app._spawn_worker = lambda: pytest.fail("quit must not spawn a worker")
    old.finished.connect(lambda current=old: app._on_worker_finished(current))

    app._quit()

    assert old.stop_calls == 1
    assert old.offline_signals == [True]
    assert hide.calls == quit_app.calls == 0
    old.finish()
    assert hide.calls == quit_app.calls == 1
    assert stop_watchdog.calls == 1


def test_worker_stop_pauses_only_its_runtime_copies(monkeypatch):
    gui_tray = _gui_tray_with_fake_qt(monkeypatch)
    from src.companion.config import CompanionConfig

    original = CompanionConfig(profile_id="A", paused=False)
    worker = gui_tray.PollWorker([original])

    worker.stop()

    assert original.paused is False
    assert worker.configs[0].paused is True


def test_connections_and_settings_opens_tomo_at_the_connections_section(monkeypatch):
    # 2026-10-10：原小助手主窗口并入 Tomo 设置页；成员变化由设置页经桥回到界面线程重启取任务线程（test_tomo_bridge.py）
    gui_tray = _gui_tray_with_fake_qt(monkeypatch)
    app = gui_tray.TrayApp.__new__(gui_tray.TrayApp)
    opened = []
    # 2026-10-18：窗口在窗口进程里（window_link.WindowHost）：托盘交过去「打开设置的连接一栏」
    app._window = SimpleNamespace(open=opened.append)
    app.open_tomo = lambda text=None: None

    app._repair()

    assert opened == [{"op": "open_settings", "section": "connections"}]


def test_stopping_for_an_update_waits_for_both_threads_and_never_respawns(monkeypatch):
    gui_tray = _gui_tray_with_fake_qt(monkeypatch)
    app = gui_tray.TrayApp.__new__(gui_tray.TrayApp)
    old = _LifecycleWorker()
    folders = SimpleNamespace(stopped=False, alive=True)
    folders.stop = lambda: setattr(folders, "stopped", True)
    folders.running = lambda: folders.alive
    folders.busy = lambda: False  # 文件夹线程没在逐份上传
    app.worker = old
    app._folders = folders
    app._restart_pending = False
    app._quitting = False
    app._updating = False
    app._profile_states = {"A": ("working", ""), "B": ("idle", "")}
    spawned = []
    app._spawn_worker = lambda: spawned.append(True)
    old.finished.connect(lambda current=old: app._on_worker_finished(current))

    assert app._busy() is True  # 有连接正在推送：不换版本
    app._profile_states["A"] = ("idle", "")
    assert app._busy() is False
    app._stop_for_update()
    assert folders.stopped is True and old.stop_calls == 1
    old.finish()
    assert app.worker is None and spawned == []  # 不再起新的取任务线程
    assert app._work_stopped() is False  # 文件夹线程还在做手上那一步
    folders.alive = False
    assert app._work_stopped() is True
    app._restart_worker()  # 期间改了连接设置也不重启
    assert spawned == []


def test_an_update_waits_for_the_on_demand_thread_too(monkeypatch):
    # 按需请求（CC 对话里读账套、取文件）的线程正在交结果：不换版本；叫停后等它停稳再装，装完再起来
    gui_tray = _gui_tray_with_fake_qt(monkeypatch)
    app = gui_tray.TrayApp.__new__(gui_tray.TrayApp)
    requests = SimpleNamespace(stopped=False, alive=True, working=True, started=0)
    requests.stop = lambda: setattr(requests, "stopped", True)
    requests.running = lambda: requests.alive
    requests.busy = lambda: requests.working
    requests.start = lambda: setattr(requests, "started", requests.started + 1)
    app.worker = None
    app._folders = None
    app._requests = requests
    app._updating = False
    app._profile_states = {"A": ("idle", "")}
    app._spawn_worker = lambda: None
    monkeypatch.setattr(gui_tray, "_window_open", lambda: False)

    assert app._busy() is True
    requests.working = False
    assert app._busy() is False
    app._stop_for_update()
    assert requests.stopped is True
    assert app._work_stopped() is False
    requests.alive = False
    assert app._work_stopped() is True
    app._resume_after_update()
    assert requests.started == 1


def test_a_key_the_server_will_not_take_is_settled_then_every_thread_restarts(monkeypatch):
    # 多台电脑（ADR-021，1.7.0）：认电脑回 superseded / claimed_elsewhere → 界面线程改本机成员、重启各线程、对话页重取状态
    gui_tray = _gui_tray_with_fake_qt(monkeypatch)
    from src.companion import device_link
    from src.companion import profile_store

    app = gui_tray.TrayApp.__new__(gui_tray.TrayApp)
    calls = []
    store = SimpleNamespace(name="store")
    monkeypatch.setattr(profile_store.ProfileStore, "load", classmethod(lambda cls, *a, **k: store))
    monkeypatch.setattr(device_link, "settle", lambda st, pid, status: calls.append(("settle", st, pid, status))
                        or "marked")
    app._reload_profiles = lambda: calls.append(("reload",))
    app._bridge = SimpleNamespace(changed=SimpleNamespace(emit=lambda: calls.append(("page",))))

    app._on_device_result("P1", "claimed_elsewhere")

    assert calls == [("settle", store, "P1", "claimed_elsewhere"), ("reload",), ("page",)]
