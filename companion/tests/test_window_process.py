"""CC 窗口进程与托盘进程分开（2026-10-18 轻量化，用户：「CC 桌面版必须要轻量设计并满足老机器能正常运行使用」）：

- 托盘进程不加载网页内核（QtWebEngine / QtWebChannel 一个模块都不导入）；
- 打开窗口时才拉起窗口进程（连上前排着的命令按顺序交过去，短时间内重复点开不重复拉起），关窗口它就退出；
- 两边经本地管道说话：口令不对的连接不收；对话页调桥（state / call）在托盘应答；选择文件夹框在窗口进程弹；
  窗口里的通知交托盘弹，点了转回窗口（窗口已退出就打开窗口，下载完成的「在文件夹中显示」托盘自己做）；
- 关窗口：没在回复马上退，正在回复等回复完再退；托盘退出、管道断了也退。
无 PySide6 则跳过。"""
import ast
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, QUrl, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from src.companion import window_link  # noqa: E402

COMPANION = Path(__file__).resolve().parents[1]
SRC = COMPANION / "src" / "companion"


def _app():
    return QApplication.instance() or QApplication([])


def _until(predicate, seconds=5.0):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    return predicate()


# ── 托盘进程里没有网页内核 ──

def test_the_tray_process_never_imports_the_web_engine():
    code = ("import sys; sys.path.insert(0, '.');"
            "from src.companion import gui_tray, gui_quick, window_link, tomo_bridge, desktop_settings, main;"
            "print(sorted(m for m in sys.modules if 'WebEngine' in m or 'WebChannel' in m))")
    out = subprocess.run([sys.executable, "-c", code], cwd=COMPANION, capture_output=True, text=True, timeout=120,
                         env={**__import__("os").environ, "QT_QPA_PLATFORM": "offscreen"})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "[]"
    # 托盘源码里也不在函数里偷偷导入窗口模块（窗口只在 window_main 里建）
    tree = ast.parse((SRC / "gui_tray.py").read_text(encoding="utf-8"))
    imported = {(node.module or "") for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not any("gui_tomo" in name or "WebEngine" in name or "WebChannel" in name for name in imported)


def test_the_window_process_is_the_same_program_with_a_flag():
    assert window_link.window_command(frozen=True, executable=r"C:\CC\companion.exe") == \
        [r"C:\CC\companion.exe", "--window"]
    assert window_link.window_command(frozen=False, executable="/py") == ["/py", "-m", "src.companion.main", "--window"]
    assert Path(window_link.window_cwd(frozen=False, executable="/py")) == COMPANION
    launcher = (COMPANION / "packaging" / "launcher_companion.py").read_text(encoding="utf-8")
    assert '"--window" in sys.argv[1:]' in launcher and "from src.companion.window_main import run" in launcher
    main = (SRC / "main.py").read_text(encoding="utf-8")
    window_branch = main.split("if args.window:", 1)[1].split("from .profile_store import ProfileStore", 1)[0]
    assert "run_window()" in window_branch   # 在单实例锁之前分出去：窗口进程不占锁


def test_lines_are_split_and_bad_or_huge_lines_are_dropped():
    lines = window_link.Lines(limit=64)
    assert lines.feed(b'{"op":"a"}\n{"op"') == [{"op": "a"}]
    assert lines.feed(b':"b","x":1}\nnot json\n[1]\n{"no_op":1}\n') == [{"op": "b", "x": 1}]
    assert lines.feed(b"x" * 100) == []          # 太长：丢到下一个换行
    assert lines.feed(b'tail"}\n{"op":"c"}\n') == [{"op": "c"}]
    assert window_link.encode({"op": "x", "t": "泰"}) == '{"op":"x","t":"泰"}\n'.encode("utf-8")


# ── 管道两头（同一进程里真连一条本地管道） ──

class _Host:
    def __init__(self):
        _app()
        self.spawned = []
        self.requests = []
        self.notes = []
        self.links = []
        self.handled = []
        self.foreground = []
        self.host = window_link.WindowHost(on_request=self._request, on_notify=self.notes.append,
                                           lang=lambda: "th", spawn=self._spawn, foreground=self.foreground.append,
                                           name=f"PearnlyCCWindowTest-{time.monotonic_ns()}")

    def _request(self, message):
        self.requests.append(message)
        return json.dumps({"echo": message.get("method") or message["op"]})

    def _spawn(self, command, cwd, env):
        self.spawned.append((command, env[window_link.ENV_LANG]))
        self.env = env
        return 4000 + len(self.spawned)

    def connect(self, token=None):
        link = window_link.TrayLink(self.env[window_link.ENV_PIPE], token or self.env[window_link.ENV_TOKEN])
        link.set_handler(lambda message: (self.handled.append(message),
                                          "C:/picked" if message["op"] == "pick" else None)[1])
        assert link.connect_to_tray()
        self.links.append(link)
        return link


def test_opening_the_window_starts_the_window_process_once_and_hands_over_queued_commands():
    t = _Host()
    host = t.host
    host.open({"op": "show"})
    host.open({"op": "ask", "text": "หา"})   # 还在启动：只排着，不再拉起一个
    assert len(t.spawned) == 1 and t.spawned[0][1] == "th" and t.spawned[0][0][-1] == "--window"
    assert not host.connected
    t.connect()
    assert _until(lambda: len(t.handled) == 2)
    assert [m["op"] for m in t.handled] == ["show", "ask"] and t.handled[1]["text"] == "หา"
    assert host.connected
    host.open({"op": "open_settings", "section": "connections"})   # 连着：直接交过去
    assert _until(lambda: len(t.handled) == 3) and t.handled[2]["section"] == "connections"
    assert len(t.spawned) == 1
    # 拿到最前面的权利交给窗口进程：拉起时给新进程，之后每次打开给它报来的进程号
    import os

    assert t.foreground == [4001, os.getpid()]


def test_a_connection_with_the_wrong_token_is_dropped():
    t = _Host()
    t.host.open({"op": "show"})
    t.connect(token="wrong")
    _until(lambda: False, 0.3)
    assert not t.host.connected and t.handled == []


def test_the_chat_page_calls_the_bridge_in_the_tray_and_the_tray_asks_the_window_to_pick_a_folder():
    t = _Host()
    t.host.open({"op": "show"})
    link = t.connect()
    assert _until(lambda: t.host.connected)
    assert link.request("state", url="https://acme.pearnly.com/assistant") == json.dumps({"echo": "state"})
    assert t.host.url == "https://acme.pearnly.com/assistant"
    assert link.request("call", method="pair", args="{}", url="https://acme.pearnly.com/assistant") == \
        json.dumps({"echo": "pair"})
    assert [m["op"] for m in t.requests] == ["state", "call"]
    # 托盘这边请窗口弹选择框：窗口回选好的路径
    assert t.host.pick("เลือกโฟลเดอร์", "C:/Docs") == "C:/picked"
    pick = next(m for m in t.handled if m["op"] == "pick")
    assert pick["title"] == "เลือกโฟลเดอร์" and pick["start"] == "C:/Docs"


def test_window_status_and_notifications_reach_the_tray_and_a_closed_pipe_resets_the_state():
    t = _Host()
    t.host.open({"op": "show"})
    link = t.connect()
    assert _until(lambda: t.host.connected)
    link.send("status", visible=True, active=True, url="https://acme.pearnly.com/assistant")
    link.send("notify", title="CC", body="done", tag="tomo-reply", click="c1", reveal="")
    assert _until(lambda: t.notes and t.host.active)
    assert t.host.visible and t.notes[0]["click"] == "c1"
    link._socket.disconnectFromServer()
    assert _until(lambda: not t.host.connected)
    assert (t.host.visible, t.host.active, t.host.url) == (False, False, "")
    assert not t.host.send("changed")
    t.host._spawned_at = None
    t.host.open({"op": "show"})   # 窗口进程退了：再打开就重新拉起
    assert len(t.spawned) == 2


def test_a_second_window_process_is_told_to_quit_and_a_leaving_one_is_replaced_on_the_next_open():
    t = _Host()
    t.host.open({"op": "show"})
    first = t.connect()
    assert _until(lambda: t.host.connected)
    second = t.connect()   # 两次快速点开拉起了两个：后来的退出，它说的不算
    assert _until(lambda: [m["op"] for m in t.handled].count("quit") == 1)
    second.send("status", visible=True, active=True, url="https://evil.pearnly.com/assistant")
    _until(lambda: False, 0.2)
    assert not t.host.visible and t.host.url == ""
    first.send("bye")      # 窗口进程正在退出：马上当作已退出，再打开就重新拉起
    assert _until(lambda: not t.host.connected)
    t.host.open({"op": "show"})
    assert len(t.spawned) == 2


def test_a_window_process_that_died_before_connecting_is_started_again_without_replaying_old_commands():
    t = _Host()
    process = SimpleNamespace(pid=4001, code=None)
    process.poll = lambda: process.code
    t.host._spawn_process = lambda command, cwd, env: (t.spawned.append(command), setattr(t, "env", env), process)[2]
    t.host.open({"op": "ask", "text": "old question"})
    t.host.open({"op": "show"})          # 还在启动：不重拉
    assert len(t.spawned) == 1
    process.code = 1                     # 没连上就退了（崩溃、连不上托盘）
    t.host.open({"op": "show"})          # 不等 30 秒，马上重拉；早先的快捷提问不再重放
    assert len(t.spawned) == 2 and t.host._pending == [{"op": "show"}]


def test_a_late_hello_while_cc_is_quitting_is_told_to_quit():
    t = _Host()
    sent = []
    t.host.quit()
    t.host._adopt(SimpleNamespace(send=lambda op, **kw: sent.append(op), alive=True))
    assert sent == ["quit"] and not t.host.connected
    t.host.open({"op": "show"})          # 正在退出：不再拉起
    assert t.spawned == []


def test_the_packaged_tray_starts_the_window_as_a_fresh_instance(monkeypatch):
    t = _Host()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    t.host.open({"op": "show"})
    assert t.env["PYINSTALLER_RESET_ENVIRONMENT"] == "1" and t.spawned[0][0][-1] == "--window"


def test_closed_connections_are_released_in_the_tray():
    from PySide6.QtNetwork import QLocalSocket

    t = _Host()
    t.host.open({"op": "show"})
    link = t.connect()
    assert _until(lambda: t.host.connected)
    assert any(isinstance(c, QLocalSocket) for c in t.host._server.children())
    link._socket.disconnectFromServer()
    assert _until(lambda: not any(isinstance(c, QLocalSocket) for c in t.host._server.children()))


def test_a_second_window_process_quits_before_building_its_web_engine(monkeypatch):
    import os

    from src.companion import logger, window_main

    t = _Host()
    t.host.open({"op": "show"})
    t.connect()                                          # 已有一个窗口进程连着
    assert _until(lambda: t.host.connected)
    monkeypatch.setattr(logger, "setup_window_logging", lambda: None)
    monkeypatch.setattr(window_main, "WindowController", lambda *a, **k: pytest.fail("不该建窗口"))
    monkeypatch.setenv(window_link.ENV_PIPE, t.env[window_link.ENV_PIPE])
    monkeypatch.setenv(window_link.ENV_TOKEN, t.env[window_link.ENV_TOKEN])
    assert window_main.run() == 0                        # 托盘叫它退：不建网页内核
    assert window_link.ENV_TOKEN not in os.environ


def test_quitting_the_tray_tells_the_window_to_quit():
    t = _Host()
    t.host.open({"op": "show"})
    t.connect()
    assert _until(lambda: t.host.connected)
    t.host.quit()
    assert _until(lambda: any(m["op"] == "quit" for m in t.handled))


# ── 托盘这边接窗口转来的 ──

def test_the_tray_answers_window_requests_and_routes_notification_clicks():
    from src.companion.gui_quick import Reveal
    from src.companion.gui_tray import TrayApp

    app = TrayApp.__new__(TrayApp)
    bridge = SimpleNamespace(state=lambda: "S", call=lambda method, args: f"{method}:{args}")
    app._bridge = bridge
    assert app._on_window_request({"op": "state"}) == "S"
    assert app._on_window_request({"op": "call", "method": "pause", "args": "{}"}) == "pause:{}"

    notes, sent, opened = [], [], []
    app._notify = lambda title, body, on_click=None, tag="": notes.append((title, body, on_click, tag))
    connected = {"value": True}
    app._window = SimpleNamespace(send=lambda op, **kw: (sent.append((op, kw)), connected["value"])[1])
    app.open_tomo = lambda text=None: opened.append(text)
    app._on_window_notify({"title": "CC", "body": "x.pdf", "reveal": "C:/Users/a/Downloads/x.pdf"})
    assert isinstance(notes[-1][2], Reveal) and notes[-1][2].path == Path("C:/Users/a/Downloads/x.pdf")
    app._on_window_notify({"title": "T", "body": "B", "tag": "notice:pnotice_1", "click": "k1"})
    assert notes[-1][3] == "notice:pnotice_1"
    notes[-1][2]()
    assert sent == [("click", {"id": "k1"})] and opened == []
    connected["value"] = False   # 窗口进程已退出：点了就打开窗口
    notes[-1][2]()
    assert opened == [None]
    app._on_window_notify({"title": "CC", "body": "b"})
    assert notes[-1][2] is None


# ── 窗口进程 ──

class _Page(QObject):
    urlChanged = Signal(object)

    def __init__(self, url="https://acme.pearnly.com/assistant"):
        super().__init__()
        self._url = url
        self.busy = False
        self.scripts = []

    def url(self):
        return QUrl(self._url)

    def runJavaScript(self, script, world, callback):
        from PySide6.QtCore import QTimer

        self.scripts.append(script)
        QTimer.singleShot(0, lambda: callback(self.busy))


class _Window(QWidget):
    def __init__(self, *, lang, notify, bridge, on_close):
        super().__init__()
        self.lang = lang
        self.notify = notify
        self.bridge = bridge
        self.on_close = on_close
        self.page = _Page()
        self.calls = []
        self.closed_down = False

    def show_front(self):
        self.calls.append("front")
        self.show()

    def ask(self, text):
        self.calls.append(("ask", text))

    def open_conversation(self, url, session):
        self.calls.append(("conversation", url, session))

    def open_settings(self, section):
        self.calls.append(("settings", section))

    def window_action(self, action, args):
        self.calls.append(("window", action))
        return {"ok": True, "maximized": False}

    def shutdown(self):
        self.closed_down = True

    def close_like_the_user(self):
        self.hide()
        self.on_close()


class _Link(QObject):
    lost = Signal()

    def __init__(self):
        super().__init__()
        self.sent = []
        self.requests = []
        self.handler = None

    def set_handler(self, handler):
        self.handler = handler

    def send(self, op, **payload):
        self.sent.append((op, payload))
        return True

    def request(self, op, **payload):
        self.requests.append((op, payload))
        return json.dumps({"ok": True, "op": op})

    def close(self):
        self.sent.append(("closed", {}))


def _controller(monkeypatch=None):
    from src.companion import window_main

    _app()
    link = _Link()
    quits = []
    controller = window_main.WindowController(link, lang="zh", quit_app=lambda: quits.append(True),
                                              make_window=lambda **kwargs: _Window(**kwargs))
    return controller, link, quits


def test_closing_the_window_ends_the_window_process_unless_a_reply_is_running(monkeypatch):
    from src.companion import window_main

    monkeypatch.setattr(window_main, "BUSY_POLL_MS", 20)
    controller, link, quits = _controller()
    window = controller.window
    link.handler({"op": "show"})
    window.close_like_the_user()
    assert _until(lambda: quits)
    assert window.closed_down and link.sent[-2:] == [("bye", {}), ("closed", {})]
    assert window.page.scripts == [window_main.BUSY_SCRIPT]

    monkeypatch.setattr(window_main, "LINGER_GRACE_MS", 300)
    controller, link, quits = _controller()
    controller._linger.setInterval(20)
    window = controller.window
    link.handler({"op": "show"})
    window.page.busy = True
    window.close_like_the_user()
    _until(lambda: False, 0.2)
    assert quits == [] and controller._lingering   # 正在回复：藏着等
    link.handler({"op": "show"})                   # 又打开了：不再等着退出
    assert not controller._lingering and window.isVisible()
    window.close_like_the_user()
    _until(lambda: controller._lingering)
    window.page.busy = False                       # 回复完了：再留一会儿让「回复完成」通知先到托盘，然后退出
    assert _until(lambda: not controller._lingering)
    window.notify("CC", "reply", lambda: None, tag="tomo-reply")
    assert quits == [] and link.sent[-1][0] == "notify"
    assert _until(lambda: quits)


def test_the_window_process_quits_when_the_tray_says_so_or_the_pipe_breaks():
    controller, link, quits = _controller()
    link.handler({"op": "quit"})
    assert quits == [True] and controller.window.closed_down
    controller, link, quits = _controller()
    link.lost.emit()
    assert quits == [True]


def test_tray_commands_drive_the_window_and_notifications_go_to_the_tray():
    from src.companion.gui_quick import Reveal

    controller, link, quits = _controller()
    window = controller.window
    link.handler({"op": "ask", "text": "hi"})
    link.handler({"op": "open_conversation", "url": "https://acme.pearnly.com/assistant?c=s-1", "session": "s-1"})
    link.handler({"op": "open_settings", "section": "connections"})
    link.handler({"op": "lang", "lang": "th"})
    assert window.calls == [("ask", "hi"), ("conversation", "https://acme.pearnly.com/assistant?c=s-1", "s-1"),
                            ("settings", "connections")]
    assert window.lang == "th"
    seen = []
    controller.bridge.changed.connect(lambda: seen.append("changed"))
    controller.bridge.done.connect(lambda job, result: seen.append((job, result)))
    link.handler({"op": "changed"})
    link.handler({"op": "done", "job": "j1", "result": "{}"})
    assert seen == ["changed", ("j1", "{}")]

    clicked = []
    window.notify("CC", "reply", lambda: clicked.append(True), tag="tomo-reply")
    op, note = link.sent[-1]
    assert op == "notify" and note["tag"] == "tomo-reply" and note["click"] and note["reveal"] == ""
    link.handler({"op": "click", "id": note["click"]})
    assert clicked == [True]
    link.handler({"op": "click", "id": "gone"})   # 记不得的：拿到前面
    assert window.calls[-1] == "front"
    window.notify("CC", "x.pdf", Reveal(Path("C:/d/x.pdf")))
    assert link.sent[-1][1]["reveal"] == str(Path("C:/d/x.pdf")) and link.sent[-1][1]["click"] == ""


def test_the_window_reports_whether_it_is_visible_and_in_front():
    controller, link, quits = _controller()
    window = controller.window
    window.show()
    assert _until(lambda: any(op == "status" and kw["visible"] for op, kw in link.sent))
    _until(lambda: False, 0.2)   # 显示、激活的事件都处理完
    status = [kw for op, kw in link.sent if op == "status"][-1]
    assert status["url"] == "https://acme.pearnly.com/assistant"
    count = len([1 for op, _ in link.sent if op == "status"])
    controller._report()   # 没变：不重复报
    assert len([1 for op, _ in link.sent if op == "status"]) == count
    window.hide()
    assert _until(lambda: [kw for op, kw in link.sent if op == "status"][-1]["visible"] is False)


def test_the_bridge_in_the_window_forwards_to_the_tray_only_on_the_chat_page():
    from src.companion import window_main

    controller, link, quits = _controller()
    bridge = controller.bridge
    assert json.loads(bridge.state()) == {"ok": True, "op": "state"}
    assert json.loads(bridge.call("pause", '{"id":"m1"}')) == {"ok": True, "op": "call"}
    assert link.requests[-1] == ("call", {"method": "pause", "args": '{"id":"m1"}',
                                          "url": "https://acme.pearnly.com/assistant",
                                          "timeout_ms": window_link.REQUEST_TIMEOUT_MS})
    # 会弹选择文件夹框的不设上限：人选得慢，页面也等到真结果（审查 10-18 第 1 条）
    for method in ("pick_roots", "pick_folder", "add_folder"):
        bridge.call(method, "{}")
        assert link.requests[-1][1]["method"] == method and link.requests[-1][1]["timeout_ms"] is None
    del link.requests[2:]
    assert json.loads(bridge.call("window", '{"action":"maximize"}')) == {"ok": True, "maximized": False}
    assert controller.window.calls[-1] == ("window", "maximize") and len(link.requests) == 2   # 窗口按钮不绕托盘
    controller.window.page._url = "https://fatcc.com/"
    assert bridge.state() == window_main.REFUSED and bridge.call("pause", "{}") == window_main.REFUSED
    assert len(link.requests) == 2


def test_the_chat_page_tells_the_desktop_whether_a_reply_is_running():
    page = (COMPANION.parent / "frontend/assistant/src/lib/desktop.ts").read_text(encoding="utf-8")
    assert "busy: () => runningRef.current" in page
    from src.companion import window_main

    assert "window.tomo.busy()" in window_main.BUSY_SCRIPT


_REAL_WINDOW = r"""
import sys, tempfile, time
from pathlib import Path
sys.path.insert(0, ".")
tmp = Path(tempfile.mkdtemp())
from src.companion.gui_font import qt_app
app = qt_app()
from PySide6.QtCore import QSettings
from PySide6.QtGui import QIcon
from src.companion import gui_tomo
gui_tomo.APPDATA_DIR = tmp
gui_tomo.QSettings = lambda *args: QSettings(str(tmp / "tomo.ini"), QSettings.Format.IniFormat)
gui_tomo.tomo_links.resume_url = lambda saved, env=None: "about:blank"
notes, closed = [], []
window = gui_tomo.TomoWindow(lang="zh", icon=QIcon(), notify=lambda *a, **k: notes.append(a), bridge=None,
                             on_close=lambda: closed.append(window.isVisible()))
def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents(); time.sleep(0.01)
window.show(); pump(0.5)
window.close(); pump(0.2)
window.show(); pump(0.2)
window.close(); pump(0.2)
window.shutdown()
print("RESULT", closed, len(notes), window.isVisible())
"""


def test_closing_the_real_cc_window_hides_it_and_hands_over_once_per_close():
    pytest.importorskip("PySide6.QtWebEngineWidgets")
    import os

    out = subprocess.run([sys.executable, "-c", _REAL_WINDOW], cwd=COMPANION, capture_output=True, text=True,
                         timeout=120, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    result = [line for line in out.stdout.splitlines() if line.startswith("RESULT")]
    assert result, out.stderr[-2000:]
    # 每次关窗口：先藏起来（交出去时已经看不见）再交给窗口进程；「CC 仍在后台运行」只提示第一次
    assert result[0] == "RESULT [False, False] 1 False"


def test_the_window_process_needs_the_tray_and_keeps_the_token_out_of_its_children(monkeypatch):
    from src.companion import logger, window_main

    _app()
    monkeypatch.setattr(logger, "setup_window_logging", lambda: None)
    monkeypatch.delenv(window_link.ENV_PIPE, raising=False)
    monkeypatch.delenv(window_link.ENV_TOKEN, raising=False)
    assert window_main.run() == 2   # 不是托盘拉起的
    monkeypatch.setenv(window_link.ENV_PIPE, f"PearnlyCCWindowNobody-{time.monotonic_ns()}")
    monkeypatch.setenv(window_link.ENV_TOKEN, "t0ken")
    assert window_main.run() == 1   # 托盘不在：连不上就退
    import os

    assert window_link.ENV_TOKEN not in os.environ and window_link.ENV_PIPE not in os.environ
