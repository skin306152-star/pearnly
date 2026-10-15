"""常驻模式（always_on.py）：保持不睡眠挂在专用线程上、开关可重复；非 Windows 不做事；没人值守时不弹账套占用询问框。"""
import sys
import threading
import types

from src.companion import always_on


def _fake_windows(monkeypatch):
    calls = []
    kernel32 = types.SimpleNamespace(SetThreadExecutionState=lambda flags: calls.append(
        (threading.current_thread().name, flags)) or 1)
    user32 = types.SimpleNamespace(LockWorkStation=lambda: calls.append(("lock",)) or 1)
    monkeypatch.setattr(sys, "platform", "win32")
    import ctypes

    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=kernel32, user32=user32), raising=False)
    return calls


def test_keep_awake_holds_the_state_on_its_own_thread_and_releases_it(monkeypatch):
    calls = _fake_windows(monkeypatch)
    awake = always_on.KeepAwake()
    assert awake.set(True) is True and awake.active
    assert awake.set(True) is True  # 再开一次不多起线程
    # 系统 + 屏幕：现代待机的电脑只保持系统拦不住关屏后的待机（2026-10-12 测试机实测）
    assert calls == [("keep-awake", always_on.ES_CONTINUOUS | always_on.ES_SYSTEM_REQUIRED | always_on.ES_DISPLAY_REQUIRED)]
    assert awake.set(False) is True and not awake.active
    assert calls[-1] == ("keep-awake", always_on.ES_CONTINUOUS)  # 同一条线程上清掉：系统按原电源设置睡眠
    assert always_on.lock_screen() is True and calls[-1] == ("lock",)


def test_nothing_happens_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    awake = always_on.KeepAwake()
    assert awake.set(True) is False and not awake.active
    assert always_on.lock_screen() is False


def test_unattended_worker_never_asks_and_leaves_a_busy_account_set_to_the_waiting_lock_path():
    # 没人点询问框：原先卡住 3 分钟后被看门狗重启、当成用户终止（USER_ABORTED_BUSY）
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src/companion/gui_tray_workers.py").read_text(encoding="utf-8")
    handle = source.split("def handle_task(", 1)[1].split("\n    def ", 1)[0]
    unattended = source.split("def _unattended(", 1)[1].split("\n    def ", 1)[0]
    assert 'bool(getattr(cfg, "always_on", False))' in unattended
    # 多台电脑（1.7.0）：替别人写的单（attended=False）同样不问
    assert 'getattr(task, "attended", None) is False' in unattended
    assert "if not written and not self._unattended(cfg, task) and not self._stop:" in handle
    # 这台写过的单先于占用探测（写过就照上次回执回报，不放回排队）
    assert handle.index("task_journal.written(task.id)") < handle.index("account_lock.is_account_busy(")
    # 有人值守、账套被占用：先放掉租约（按「等账套」回报）再问，不拿着租约等人点
    release = source.split("def _release_then_ask(", 1)[1].split("\n    def ", 1)[0]
    assert release.index("queue.ack_failure(") < release.index("self._ask_busy(lang)")
    assert 'outcome="waiting_lock"' in release
    tray = (Path(__file__).resolve().parents[1] / "src/companion/gui_tray.py").read_text(encoding="utf-8")
    assert "if not open_tomo:\n                lock_screen()" in tray  # 只在开机自启（不弹窗口）时锁屏
