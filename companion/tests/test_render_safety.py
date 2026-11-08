"""CC 窗口画不出来时自动改用软件绘制（2026-11-08，老 Acer 一体机装好 CC 后窗口整片浅灰、登录页不出来）：

- 档位记在本机文件里，默认用显卡；画不出来换到下一档，最后一档不再换；读坏了按默认；
- 换档就是给网页内核的环境变量：Chromium 参数并进已有的，不覆盖；
- 判断画不出来：自己的页面加载成功、窗口看得见，几次截图都是一整片同一种颜色才算；有文字就算画出来了；
  看不见的时候、别的网站、加载失败都不判断；
- 窗口进程发现画不出来：记下一档、请托盘重开、自己退出；托盘等旧进程退出后重新拉起窗口进程；
- Qt 的警告写进 window.log（页面控制台不写，同一句只写一次）。
无 PySide6 则跳过。"""
import json
import time
import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, QUrl, Signal  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from src.companion import render_safety, window_link  # noqa: E402


def _app():
    return QApplication.instance() or QApplication([])


def _until(predicate, seconds=3.0):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    return predicate()


def _image(color="#fcfcfb", text=""):
    image = QImage(800, 600, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    if text:
        painter = QPainter(image)
        painter.drawText(360, 300, text)
        painter.end()
    return image


# ── 档位 ──

def test_the_level_defaults_to_the_graphics_card_and_steps_up_once(tmp_path):
    path = tmp_path / "tomo" / "render.json"
    now = lambda: 1700000000  # noqa: E731
    assert render_safety.load_level(path) == 0
    assert render_safety.escalate("blank", path, clock=now, version="1.12.19") == 1
    assert json.loads(path.read_text(encoding="utf-8")) == {"level": 1, "reason": "blank", "at": 1700000000,
                                                            "version": "1.12.19"}
    assert render_safety.load_level(path, version="1.12.19", clock=now) == 1
    assert render_safety.escalate("blank", path, clock=now, version="1.12.19") is None   # 已经是最后一档：不再换
    assert render_safety.load_level(path, version="1.12.19", clock=now) == 1
    path.write_text("not json", encoding="utf-8")
    assert render_safety.load_level(path) == 0
    path.write_text('{"level": 9}', encoding="utf-8")
    assert render_safety.load_level(path) == 0


def test_software_rendering_is_not_locked_in_forever(tmp_path):
    path = tmp_path / "render.json"
    day = 86400
    render_safety.escalate("blank", path, clock=lambda: 1000 * day, version="1.12.19")
    # 同一 1.12 系列、30 天内：照旧软件绘制（补丁版本升级不重新试）
    assert render_safety.load_level(path, version="1.12.25", clock=lambda: 1029 * day) == 1
    # 满 30 天、或升了 1.13：回到显卡重新试一次
    assert render_safety.load_level(path, version="1.12.19", clock=lambda: 1030 * day) == 0
    assert render_safety.load_level(path, version="1.13.0", clock=lambda: 1001 * day) == 0
    # 没记版本的旧记录（不会有，防坏文件）：重新试
    path.write_text('{"level": 1, "at": 0}', encoding="utf-8")
    assert render_safety.load_level(path, version="1.12.19", clock=lambda: 1) == 0
    # 重新试还是画不出来：从显卡档再升回软件绘制，重新记版本与时间
    assert render_safety.escalate("blank", path, current=0, clock=lambda: 1030 * day, version="1.13.0") == 1
    assert render_safety.load_level(path, version="1.13.0", clock=lambda: 1031 * day) == 1


def test_software_rendering_merges_the_chromium_flags_and_the_default_sets_nothing():
    env = {}
    assert render_safety.apply(0, env) == {} and env == {}
    env = {"QTWEBENGINE_CHROMIUM_FLAGS": "--foo --disable-gpu"}
    render_safety.apply(1, env)
    assert env["QTWEBENGINE_CHROMIUM_FLAGS"].split() == ["--foo", "--disable-gpu", "--disable-gpu-compositing"]
    assert env["QSG_RHI_PREFER_SOFTWARE_RENDERER"] == "1"
    env = {}
    render_safety.apply(5, env)   # 不认识的档位按默认
    assert env == {}


def test_a_picture_of_one_colour_is_blank_and_any_text_is_not():
    _app()
    assert render_safety.is_blank(_image())
    assert render_safety.is_blank(_image("#151515"))
    assert render_safety.is_blank(QImage())
    assert not render_safety.is_blank(_image(text="Sign in"))


# ── 盯页面 ──

class _Page(QObject):
    loadStarted = Signal()
    loadFinished = Signal(bool)

    def __init__(self, url="https://fatcc.com/", text=120):
        super().__init__()
        self._url = url
        self.text = text

    def url(self):
        return QUrl(self._url)

    def runJavaScript(self, script, world, callback):
        assert script == render_safety.TEXT_SCRIPT
        callback(self.text)


def _watch(page, picture, visible=True, probes=(10, 20, 30)):
    blanks = []
    state = {"picture": picture, "visible": visible}
    watch = render_safety.RenderWatch(page, grab=lambda: state["picture"], visible=lambda: state["visible"],
                                      own=lambda url: url.startswith("https://fatcc.com"),
                                      on_blank=blanks.append, probes=probes)
    return watch, blanks, state


def test_a_page_that_loaded_but_never_painted_is_reported_once():
    _app()
    page = _Page()
    watch, blanks, _state = _watch(page, _image())
    page.loadFinished.emit(True)
    assert _until(lambda: blanks)
    _until(lambda: False, 0.1)
    assert blanks == ["https://fatcc.com/"]


def test_a_painted_page_a_hidden_window_another_site_or_a_failed_load_is_not_reported():
    _app()
    page = _Page()
    watch, blanks, state = _watch(page, _image(text="Sign in"))
    page.loadFinished.emit(True)
    _until(lambda: False, 0.15)
    assert blanks == [] and watch._painted
    state["picture"] = _image()   # 画出来过：之后不再查
    page.loadFinished.emit(True)
    _until(lambda: False, 0.15)
    assert blanks == []

    for url, ok, visible in (("https://fatcc.com/", True, False), ("https://example.com/", True, True),
                             ("https://fatcc.com/", False, True)):
        page = _Page(url)
        watch, blanks, _state = _watch(page, _image(), visible=visible)
        page.loadFinished.emit(ok)
        _until(lambda: False, 0.15)
        assert blanks == [], (url, ok, visible)


def test_a_blank_page_without_any_text_yet_is_still_loading_not_broken():
    _app()
    for text in (0, None):
        page = _Page(text=text)
        watch, blanks, _state = _watch(page, _image())
        page.loadFinished.emit(True)
        _until(lambda: False, 0.15)
        assert blanks == [], text


def test_a_new_load_restarts_the_check():
    _app()
    page = _Page()
    watch, blanks, state = _watch(page, _image(), probes=(40, 80, 120))
    page.loadFinished.emit(True)
    _until(lambda: False, 0.06)
    page.loadStarted.emit()       # 中途又开始加载：之前的不算
    _until(lambda: False, 0.15)
    assert blanks == []


# ── 窗口进程与托盘 ──

class _Window(QWidget):
    def __init__(self, picture):
        super().__init__()
        self.page = _FakeChatPage()
        self.lang = "zh"
        self.view = SimpleNamespace(grab=lambda: SimpleNamespace(toImage=lambda: picture))
        self.closed_down = False

    def show_front(self):
        self.show()

    def shutdown(self):
        self.closed_down = True


class _FakeChatPage(QObject):
    urlChanged = Signal(object)
    visibleChanged = Signal(bool)
    loadStarted = Signal()
    loadFinished = Signal(bool)

    def __init__(self):
        super().__init__()
        self.visible = False

    def isVisible(self):
        return self.visible

    def setVisible(self, value):
        if bool(value) != self.visible:
            self.visible = bool(value)
            self.visibleChanged.emit(self.visible)

    def url(self):
        return QUrl("https://fatcc.com/")

    def runJavaScript(self, script, world, callback):
        callback(80 if script == render_safety.TEXT_SCRIPT else False)


class _Link(QObject):
    lost = Signal()

    def __init__(self):
        super().__init__()
        self.sent = []

    def set_handler(self, handler):
        self.handler = handler

    def send(self, op, **payload):
        self.sent.append(op)
        return True

    def close(self):
        self.sent.append("closed")


def _controller(tmp_path, picture, level=0):
    from src.companion import window_main

    _app()
    link = _Link()
    quits = []
    controller = window_main.WindowController(
        link, lang="zh", quit_app=lambda: quits.append(True), make_window=lambda **kwargs: _Window(picture),
        system=False, render_level=level, render_state=tmp_path / "render.json", probes=(10, 20, 30))
    return controller, link, quits


def test_a_blank_window_switches_to_software_rendering_and_asks_the_tray_to_reopen_it(tmp_path):
    controller, link, quits = _controller(tmp_path, _image())
    link.handler({"op": "show"})
    controller.window.page.loadFinished.emit(True)
    assert _until(lambda: quits)
    assert link.sent[-3:] == ["restart", "bye", "closed"] and controller.window.closed_down
    assert render_safety.load_level(tmp_path / "render.json") == 1


def test_a_blank_window_already_on_software_rendering_stays_open(tmp_path):
    render_safety.escalate("blank", tmp_path / "render.json")
    controller, link, quits = _controller(tmp_path, _image(), level=1)
    link.handler({"op": "show"})
    controller.window.page.loadFinished.emit(True)
    _until(lambda: False, 0.15)
    assert quits == [] and "restart" not in link.sent


def test_the_tray_starts_the_window_again_after_the_old_process_has_exited():
    _app()
    spawned = []
    alive = {"value": True}

    def spawn(command, cwd, env):
        spawned.append(env)
        return 4000 + len(spawned)

    host = window_link.WindowHost(on_request=lambda m: None, on_notify=lambda m: None, spawn=spawn,
                                  foreground=lambda pid: None, alive=lambda pid: alive["value"],
                                  name=f"PearnlyCCWindowTest-{uuid.uuid4().hex}")
    host.open({"op": "show"})
    link = window_link.TrayLink(spawned[0][window_link.ENV_PIPE], spawned[0][window_link.ENV_TOKEN])
    link.set_handler(lambda message: None)
    assert link.connect_to_tray()
    assert _until(lambda: host.connected)
    link.send("restart")
    link.send("bye")
    assert _until(lambda: not host.connected)
    _until(lambda: False, 0.5)
    assert len(spawned) == 1           # 旧进程还没退：先等
    alive["value"] = False
    assert _until(lambda: len(spawned) == 2)
    link.close()


def test_a_plain_bye_does_not_reopen_the_window():
    _app()
    spawned = []
    host = window_link.WindowHost(on_request=lambda m: None, on_notify=lambda m: None,
                                  spawn=lambda c, w, env: (spawned.append(env), 4001)[1],
                                  foreground=lambda pid: None, alive=lambda pid: False,
                                  name=f"PearnlyCCWindowTest-{uuid.uuid4().hex}")
    host.open({"op": "show"})
    link = window_link.TrayLink(spawned[0][window_link.ENV_PIPE], spawned[0][window_link.ENV_TOKEN])
    link.set_handler(lambda message: None)
    assert link.connect_to_tray() and _until(lambda: host.connected)
    link.send("bye")
    assert _until(lambda: not host.connected)
    _until(lambda: False, 0.4)
    assert len(spawned) == 1
    link.close()


# ── Qt 日志 ──

def test_qt_warnings_go_to_the_window_log_once_and_page_console_output_does_not(monkeypatch):
    records = []
    monkeypatch.setattr(render_safety, "_qt_logged", set())
    monkeypatch.setattr(render_safety.log, "log", lambda level, fmt, *args: records.append((level, fmt % args)))
    warning = SimpleNamespace(name="QtWarningMsg")
    render_safety.qt_message(warning, SimpleNamespace(category="qt.rhi.general"), "Failed to create D3D11 device")
    render_safety.qt_message(warning, SimpleNamespace(category="qt.rhi.general"), "Failed to create D3D11 device")
    render_safety.qt_message(warning, SimpleNamespace(category="js"), "console noise")
    render_safety.qt_message(SimpleNamespace(name="QtDebugMsg"), SimpleNamespace(category="qt.webenginecontext"),
                             "GPU: Intel(R) HD Graphics")
    assert records == [(30, "Qt[qt.rhi.general] Failed to create D3D11 device"),
                       (20, "Qt[qt.webenginecontext] GPU: Intel(R) HD Graphics")]


def test_the_window_process_sets_the_level_and_qt_logging_before_the_application_starts():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src" / "companion" / "window_main.py").read_text(encoding="utf-8")
    run = source.split("def run()", 1)[1]
    assert run.index("render_safety.apply(") < run.index("qt_app()")
    assert run.index("render_safety.install_qt_log()") < run.index("qt_app()")
