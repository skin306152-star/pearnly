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
    monkeypatch.setitem(sys.modules, "PySide6", pyside)
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", qtcore)
    monkeypatch.setitem(sys.modules, "PySide6.QtGui", qtgui)
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", qtwidgets)
    sys.modules.pop("src.companion.gui_tray", None)
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


def test_profile_callback_reloads_immediately_without_close_restart(monkeypatch):
    gui_tray = _gui_tray_with_fake_qt(monkeypatch)
    manager = ModuleType("src.companion.gui_profiles")

    def open_manager(**kwargs):
        kwargs["on_profiles_changed"]()
        return True

    manager.open_profile_manager = open_manager
    monkeypatch.setitem(sys.modules, "src.companion.gui_profiles", manager)
    app = gui_tray.TrayApp.__new__(gui_tray.TrayApp)
    app._profile_states = {}
    reloads = _CallSink()
    app._reload_profiles = reloads
    app._apply_language = lambda _lang: None

    app._repair()

    assert reloads.calls == 1


def test_profile_callback_failure_keeps_close_fallback(monkeypatch):
    gui_tray = _gui_tray_with_fake_qt(monkeypatch)
    manager = ModuleType("src.companion.gui_profiles")

    def open_manager(**kwargs):
        try:
            kwargs["on_profiles_changed"]()
        except RuntimeError:
            pass
        return True

    manager.open_profile_manager = open_manager
    monkeypatch.setitem(sys.modules, "src.companion.gui_profiles", manager)
    app = gui_tray.TrayApp.__new__(gui_tray.TrayApp)
    app._profile_states = {}
    calls = {"count": 0}

    def reload_profiles():
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("live reload failed")

    app._reload_profiles = reload_profiles
    app._apply_language = lambda _lang: None

    app._repair()

    assert calls["count"] == 2
