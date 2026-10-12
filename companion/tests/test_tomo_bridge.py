"""Tomo 窗口里对话页与本机的桥（2026-10-10）：只在 Tomo 对话页上应答；慢操作在后台做完再回到界面线程；
成员变化在界面线程里重启取任务线程。无 PySide6 则跳过。"""
import json
import time

import pytest

pytest.importorskip("PySide6")

from src.companion.tomo_bridge import TomoBridge  # noqa: E402


class _Settings:
    def __init__(self):
        self.calls = []

    def snapshot(self):
        return {"members": [{"id": "m1", "folders": ["C:/in"]}], "version": "1.4.0"}

    def pair(self, code, lang, profile_id, switch=False):
        import threading

        self.calls.append(("pair", code, lang, profile_id, threading.current_thread().name) + ((switch,) if switch else ()))
        return {"ok": True, "profiles_changed": True}

    def profiles_changed(self):
        import threading

        self.calls.append(("reload", threading.current_thread().name))

    def set_folders(self, member, paths, lang):
        self.calls.append(("folders", member, paths))
        return {"ok": True}


def _bridge(allowed=True):
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    settings = _Settings()
    langs = []
    bridge = TomoBridge(settings, allowed=lambda: allowed, window=lambda: None, set_lang=langs.append)
    return bridge, settings, langs


def test_other_pages_are_refused():
    bridge, settings, _ = _bridge(allowed=False)
    assert json.loads(bridge.state())["refused"] is True
    assert json.loads(bridge.call("remove_folder", json.dumps({"id": "m1", "path": "C:/in"})))["refused"] is True
    assert settings.calls == []


def test_pairing_runs_in_the_background_and_reloads_on_the_ui_thread():
    from PySide6.QtWidgets import QApplication

    bridge, settings, _ = _bridge()
    done = []
    bridge.done.connect(lambda job, result: done.append((job, json.loads(result))))

    job = json.loads(bridge.call("pair", json.dumps({"code": "exp_x", "lang": "zh"})))["job"]
    deadline = time.monotonic() + 5
    while not done and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)

    assert done == [(job, {"ok": True})]
    pair, reload = settings.calls
    assert pair[:4] == ("pair", "exp_x", "zh", None) and pair[4] != "MainThread"
    assert reload == ("reload", "MainThread")


def test_removing_a_folder_keeps_the_others_and_language_follows_the_page():
    bridge, settings, langs = _bridge()
    assert json.loads(bridge.call("remove_folder", json.dumps({"id": "m1", "path": "C:/in", "lang": "th"}))) == {"ok": True}
    assert settings.calls == [("folders", "m1", [])]
    bridge.call("lang", json.dumps({"lang": "th"}))
    bridge.call("lang", json.dumps({"lang": "en"}))   # 小助手只有中文 / 泰文：英文页面不改托盘语言
    assert langs == ["th"]


def test_window_buttons_drawn_by_the_chat_page_reach_the_window():
    """对话页自画的标题栏（同 Claude 桌面端）：最小化 / 最大化 / 关闭 / 拖动 / 缩放经桥交给窗口，只在对话页上应答。"""
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])

    class _Window:
        def __init__(self):
            self.calls = []

        def window_action(self, action, args):
            self.calls.append((action, args.get("step")))
            return {"ok": True, "maximized": action == "maximize"}

    window = _Window()
    bridge = TomoBridge(_Settings(), allowed=lambda: True, window=lambda: window, set_lang=lambda _l: None)
    assert json.loads(bridge.state())["window"] == {"frameless": True}   # 页面据此画自己的标题栏
    assert json.loads(bridge.call("window", json.dumps({"action": "maximize"}))) == {"ok": True, "maximized": True}
    bridge.call("window", json.dumps({"action": "zoom", "step": -1}))
    assert window.calls == [("maximize", None), ("zoom", -1)]
    refused = TomoBridge(_Settings(), allowed=lambda: False, window=lambda: window, set_lang=lambda _l: None)
    assert json.loads(refused.call("window", json.dumps({"action": "close"})))["refused"] is True
    assert window.calls == [("maximize", None), ("zoom", -1)]
