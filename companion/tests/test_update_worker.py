# -*- coding: utf-8 -*-
"""自动更新下载放后台线程(治「点更新→UI 卡死」):worker 成功发路径、失败发 failed。"""

import os

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.companion import gui_tray, updater  # noqa: E402


def _app():
    return QApplication.instance() or QApplication([])


def test_worker_emits_path_on_success(monkeypatch):
    _app()
    monkeypatch.setattr(updater, "download_installer", lambda b, u: r"C:\tmp\Setup.exe")
    w = gui_tray._UpdateWorker("https://pearnly.com", "/static/companion/x.exe?v=v9")
    got = []
    w.done.connect(got.append)
    w.run()  # 直接跑(同线程)→ 信号同步触发
    assert got == [r"C:\tmp\Setup.exe"]


def test_worker_emits_failed_on_error(monkeypatch):
    _app()

    def boom(b, u):
        raise RuntimeError("network down")

    monkeypatch.setattr(updater, "download_installer", boom)
    w = gui_tray._UpdateWorker("https://pearnly.com", "/x")
    fired = []
    w.failed.connect(lambda: fired.append(True))
    w.done.connect(lambda p: fired.append(("done", p)))
    w.run()
    assert fired == [True]  # 只发 failed,不发 done
