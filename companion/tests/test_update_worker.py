# -*- coding: utf-8 -*-
"""自动更新下载放后台线程(治「点更新→UI 卡死」)，线程真正结束后才松引用、交结果。

11-01：1.12.7 下载线程发出「下载完」时主线程马上丢掉线程的最后一个引用，线程还没退出，Qt 判「线程还在跑就被销毁」
直接退出（崩在 Qt6Core，0xc0000409），安装包没起来、CC 停在旧版。后台线程一律用 ProbeThread、在 finished 后松引用。
"""

import ast
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.companion import gui_pairing_support, gui_tray, updater  # noqa: E402
from src.companion.gui_tray import TrayApp  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "src" / "companion"


def _app():
    return QApplication.instance() or QApplication([])


def _tray(monkeypatch):
    _app()
    monkeypatch.setattr(gui_tray, "activity", lambda *a, **k: None)
    app = TrayApp.__new__(TrayApp)
    app.lang = "zh"
    app._update_worker = None
    calls = []
    app._auto_update = SimpleNamespace(downloaded=lambda path: calls.append(("downloaded", path)),
                                       download_failed=lambda: calls.append(("download_failed",)))
    app._auto_update_tick = lambda: calls.append(("tick",))
    app._on_update_failed = lambda: calls.append(("failed",))
    return app, calls


def test_the_download_thread_is_kept_until_it_has_finished(monkeypatch):
    """结果信号到了线程还在：不松引用、不交结果；finished 之后才交给自动更新。"""
    app, calls = _tray(monkeypatch)
    monkeypatch.setattr(gui_pairing_support.ProbeThread, "start", lambda self: None)
    app._start_download("1.12.9", "/x.exe")
    worker = app._update_worker
    worker.ok.emit(r"C:\tmp\Setup.exe")
    assert app._update_worker is worker and calls == []
    worker.finished.emit()
    assert app._update_worker is None
    assert calls == [("downloaded", r"C:\tmp\Setup.exe"), ("tick",)]


def test_a_failed_download_is_reported_after_the_thread_finished(monkeypatch):
    app, calls = _tray(monkeypatch)
    monkeypatch.setattr(gui_pairing_support.ProbeThread, "start", lambda self: None)
    app._start_download("1.12.9", "/x.exe")
    worker = app._update_worker
    worker.err.emit(RuntimeError("network down"))
    assert app._update_worker is worker and calls == []
    worker.finished.emit()
    assert app._update_worker is None and calls == [("download_failed",), ("failed",)]


def test_a_real_download_thread_hands_over_the_installer(monkeypatch):
    app, calls = _tray(monkeypatch)
    monkeypatch.setattr(updater, "download_installer", lambda site, url: r"C:\tmp\Setup.exe")
    app._start_download("1.12.9", "/x.exe")
    deadline = time.monotonic() + 5
    while app._update_worker is not None and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert calls == [("downloaded", r"C:\tmp\Setup.exe"), ("tick",)]


def test_background_qt_threads_are_only_the_shared_ones():
    """新的后台 Qt 线程用 ProbeThread / Probes（finished 后才松引用），不再各写一个 QThread 子类自己管引用。"""
    found = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef) and any(
                    getattr(base, "id", getattr(base, "attr", "")) == "QThread" for base in node.bases):
                found.add(f"{path.name}:{node.name}")
    assert found == {"gui_pairing_support.py:ProbeThread", "gui_tray_workers.py:PollWorker"}
