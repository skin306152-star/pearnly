# -*- coding: utf-8 -*-
"""MR.ERP 页面的网页内核驱动（ADR-026、评估 §14.2）。只在 MR.ERP 桌面子进程（``companion.exe --mrerp-desk``）和测试里导入：
托盘进程不加载网页内核。

- 每批一个**不落盘**的浏览器资料（``QWebEngineProfile()`` 无名 = off-the-record：Cookie、缓存只在内存，关窗即无），
  不和 CC 窗口、用户自己的浏览器共享登录；
- 一个 CC 自己的原生窗口放 MR.ERP 页面，**默认不显示**（不抢屏幕），点「查看」才弹出（``show``）；页面设为可见状态，收起也照常跑
  （Chromium 对看不见的页面会降速，第 0 步实测，待探路核对）；
- 脚本跑在**隔离脚本环境**（``ApplicationWorld``：页面自己的脚本看不到我们的脚本、我们也不调页面的函数），只经 DOM 填格、
  触发页面自己的 ``input / change / blur`` 事件、点按钮、读格子；
- 接管 ``alert / confirm / prompt``：不弹出、不卡住，文字记进 ``dialogs`` 交给配方规则；confirm 默认回「取消」（安全），
  批次线程可预先说下一次回什么（``confirm``）；
- 只许在本站（第一次 ``open`` 的网址的协议 + 主机 + 端口）里跳转，不开新窗口；
- 人在这个窗口里点、打字、滚轮（发到本窗口部件的 Qt 输入事件；我们自己只跑脚本、不发 Qt 输入事件）计数，交给 ``presence``；
- 所有网页内核调用在界面线程执行：批次线程经 ``UiCall`` 排进界面线程、等结果回来（小转交层，不新增常驻线程）；
- 页面控制台输出、填进去的值一律不记日志（登录页的密码只经管道交过来，用完不留）。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
from typing import Callable, Dict, Optional

from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

WORLD = QWebEngineScript.ScriptWorldId.ApplicationWorld
INPUT_EVENTS = frozenset({QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress, QEvent.Type.Wheel,
                          QEvent.Type.MouseButtonDblClick})
DIALOGS_KEPT = 20
WINDOW_TITLE = "MR.ERP"


def _origin(url: QUrl) -> tuple:
    return (url.scheme().lower(), url.host().lower(), url.port(-1))


class _Page(QWebEnginePage):
    def __init__(self, profile, parent, driver: "Driver"):
        super().__init__(profile, parent)
        self._driver = driver

    def javaScriptAlert(self, _origin, message):  # noqa: N802 — Qt 的名字
        self._driver._dialog("alert", message)

    def javaScriptConfirm(self, _origin, message):  # noqa: N802
        self._driver._dialog("confirm", message)
        return self._driver._confirm_answer()

    def javaScriptPrompt(self, _origin, message, _default):  # noqa: N802
        self._driver._dialog("prompt", message)
        return False, ""

    def javaScriptConsoleMessage(self, *_args):  # noqa: N802 — 页面控制台不进日志（可能带单据内容）
        return None

    def acceptNavigationRequest(self, url, _type, _main):  # noqa: N802
        return self._driver._allowed(url)

    def createWindow(self, _type):  # noqa: N802 — 不开新窗口（打印、附件预览之类一律不跟）
        return None


class _Inputs(QObject):
    """挂在 MR.ERP 窗口部件上的事件过滤：点、打字、滚轮都算有人在这个窗口里动（我们自己只跑脚本，不发 Qt 输入事件）。"""

    def __init__(self, driver: "Driver"):
        super().__init__(driver)
        self._driver = driver

    def eventFilter(self, _obj, event):  # noqa: N802 — Qt 的名字
        if event.type() in INPUT_EVENTS:
            self._driver._input()
        return False


class Driver(QObject):
    """界面线程里的 MR.ERP 页面。``handle(op, args, done)`` 是唯一入口（``UiCall`` 调），``done(dict)`` 回结果。"""

    def __init__(self, *, title: str = WINDOW_TITLE, keep_visible: bool = True):
        super().__init__()
        self.profile = QWebEngineProfile(self)  # 无名 = 不落盘
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        settings = self.profile.settings()
        for attribute in (QWebEngineSettings.WebAttribute.PluginsEnabled, QWebEngineSettings.WebAttribute.PdfViewerEnabled,
                          QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows):
            settings.setAttribute(attribute, False)
        self.window = QWidget()
        self.window.setWindowTitle(title)
        self.window.resize(1280, 800)
        self.view = QWebEngineView(self.window)
        layout = QVBoxLayout(self.window)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.page = _Page(self.profile, self.view, self)
        self.view.setPage(self.page)
        if keep_visible:
            # 窗口收起时页面也按「看得见」跑（计时器、网络不降速）；第 0 步实测收起后是否照跑，待探路核对
            self.page.setVisible(True)
        self.page.loadStarted.connect(self._started)
        self.page.loadFinished.connect(self._finished)
        self.loads = 0  # 完成过几次加载（含失败）：批次线程据此等「点了以后页面换了」
        self.loading = False
        self.last_ok = True
        self.dialogs: list = []
        self.human = 0  # 人在本窗口里的输入次数
        self.human_at = 0.0
        self._confirm = False
        self._base: Optional[tuple] = None
        self.closed = False
        # 人工输入只看这个窗口自己的部件（窗口本身、网页内核的渲染部件）：不挂整个应用的事件过滤（PySide 里那样会碰到正在
        # 析构的对象而崩溃），渲染部件换了（每次加载后核一次）就补挂
        self._inputs = _Inputs(self)
        self._watched: list = []
        self._watch(self.window)

    # ── 页面回调 ─────────────────────────────────────────────────────────
    def _started(self) -> None:
        self.loading = True

    def _finished(self, ok: bool) -> None:
        self.loading, self.last_ok = False, bool(ok)
        self.loads += 1
        self._watch(self.view.focusProxy())

    def _watch(self, widget) -> None:
        if widget is not None and not any(w is widget for w in self._watched):
            widget.installEventFilter(self._inputs)
            self._watched.append(widget)

    def _input(self) -> None:
        self.human += 1
        self.human_at = time.monotonic()

    def _dialog(self, kind: str, message: str) -> None:
        self.dialogs.append({"kind": kind, "text": str(message or "")[:500], "at": round(time.time(), 2)})
        del self.dialogs[:-DIALOGS_KEPT]

    def _confirm_answer(self) -> bool:
        answer, self._confirm = self._confirm, False  # 只管下一次；没说就回「取消」
        return answer

    def _allowed(self, url: QUrl) -> bool:
        if url.scheme() in ("about", "data") or self._base is None:
            return True
        return _origin(url) == self._base

    # ── 动作（界面线程）─────────────────────────────────────────────────
    def handle(self, op: str, args: Dict, done: Callable[[Dict], None]) -> None:
        if self.closed and op not in ("close", "quit"):
            done({"ok": False, "error": "closed"})
            return
        method = getattr(self, "op_" + op, None)
        if method is None:
            done({"ok": False, "error": f"unknown op {op[:30]}"})
            return
        method(args, done)

    def op_open(self, args: Dict, done) -> None:
        url = QUrl(str(args.get("url") or ""))
        if not url.isValid() or url.scheme() not in ("http", "https"):
            done({"ok": False, "error": "bad url"})
            return
        if args.get("base"):
            self._base = _origin(QUrl(str(args["base"])))
        elif self._base is None:
            self._base = _origin(url)
        if not self._allowed(url):
            done({"ok": False, "error": "off site"})
            return
        self.page.setUrl(url)
        done({"ok": True, "loads": self.loads})

    def op_js(self, args: Dict, done) -> None:
        """在隔离脚本环境里跑一段脚本（脚本自己 ``JSON.stringify`` 结果）。"""
        def back(result) -> None:
            try:
                value = json.loads(result) if isinstance(result, str) and result else result
            except ValueError:
                value = result
            done({"ok": True, "value": value})
        self.page.runJavaScript(str(args.get("code") or ""), WORLD, back)

    def op_state(self, args: Dict, done) -> None:
        dialogs = list(self.dialogs)
        if args.get("drain", True):
            self.dialogs.clear()
        idle = (time.monotonic() - self.human_at) * 1000 if self.human_at else None
        done({"ok": True, "url": self.page.url().toString(), "title": self.page.title(), "loads": self.loads,
              "loading": self.loading, "last_ok": self.last_ok, "dialogs": dialogs, "human": self.human,
              "human_idle_ms": None if idle is None else int(idle), "shown": self.window.isVisible()})

    def op_confirm(self, args: Dict, done) -> None:
        self._confirm = bool(args.get("answer"))
        done({"ok": True})

    def op_screenshot(self, args: Dict, done) -> None:
        pixmap = self.view.grab()
        size = args.get("size") or [1280, 800]
        if pixmap.isNull():
            done({"ok": False, "error": "no picture"})
            return
        pixmap = pixmap.scaled(int(size[0]), int(size[1]), Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buffer, "PNG")
        done({"ok": True, "png": base64.b64encode(bytes(data)).decode("ascii"),
              "size": [pixmap.width(), pixmap.height()]})

    def op_show(self, _args: Dict, done) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        done({"ok": True})

    def op_hide(self, _args: Dict, done) -> None:
        self.window.hide()
        done({"ok": True})

    def op_close(self, _args: Dict, done) -> None:
        """关窗、释放资料：先删页面再删资料（反过来网页内核会报资料还被页面用着）。"""
        if not self.closed:
            self.closed = True
            for widget in self._watched:
                try:
                    widget.removeEventFilter(self._inputs)
                except RuntimeError:  # 部件已经没了
                    pass
            self._watched.clear()
            self.view.setPage(None)
            self.page.deleteLater()
            self.window.hide()
            self.window.deleteLater()
            self.profile.deleteLater()
        done({"ok": True})

    def op_quit(self, _args: Dict, done) -> None:
        done({"ok": True})
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, app.quit)


class UiCall(QObject):
    """把批次线程（或管道线程）的一个动作排进界面线程执行、等它回结果。"""

    _go = Signal(object)

    def __init__(self, handle: Callable[[str, Dict, Callable[[Dict], None]], None]):
        super().__init__()
        self._handle = handle
        app = QApplication.instance()
        if app is not None and self.thread() is not app.thread():
            self.moveToThread(app.thread())  # 在批次线程里建的：交给界面线程，动作在那里执行
        self._go.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def call(self, op: str, args: Optional[Dict] = None, timeout: float = 30.0) -> Dict:
        box: Dict = {"event": threading.Event()}
        self._go.emit((op, dict(args or {}), box))
        if not box["event"].wait(max(0.1, float(timeout))):
            box["late"] = True
            return {"ok": False, "error": "timeout"}
        return box["result"]

    @Slot(object)
    def _run(self, item) -> None:
        op, args, box = item

        def done(result: Dict) -> None:
            if "result" not in box:
                box["result"] = dict(result or {"ok": False})
                box["event"].set()

        try:
            self._handle(op, args, done)
        except Exception as error:  # noqa: BLE001 — 回给批次线程，由它决定
            done({"ok": False, "error": f"{type(error).__name__}: {error}"[:300]})


class LocalDesk:
    """同一进程里的网页驱动（测试用：测试自己在主线程跑事件循环，批次在另一条线程）。接口同 ``web_desk.ProcessDesk``。"""

    def __init__(self, driver: Driver):
        self.driver = driver
        self.ui = UiCall(driver.handle)
        self.pid = os.getpid()

    def start(self) -> None:
        return None

    def call(self, op: str, timeout: float = 30.0, *, wait: Optional[float] = None, **args) -> Dict:
        return self.ui.call(op, args, timeout)

    def close(self) -> None:
        self.ui.call("close", {}, 10)

    @property
    def alive(self) -> bool:
        return not self.driver.closed


# ── 子进程入口（``companion.exe --mrerp-desk``）──────────────────────────────
def child_main() -> int:
    """托盘的批次线程拉起本进程：建 QApplication 与 MR.ERP 页面，在一条管道线程里收动作、排进界面线程执行、回结果。
    管道断了 / 收到 ``close`` / 等不到连接就退出；网页内核的渲染进程随之退出。"""
    from multiprocessing.connection import Listener

    from .web_desk import ENV_ADDRESS, ENV_FAMILY, ENV_KEY, CONNECT_WAIT_S

    # 读完就从环境里拿掉：网页内核的子进程（QtWebEngineProcess）会继承环境变量，口令不留给它们
    address, family = os.environ.pop(ENV_ADDRESS, ""), os.environ.pop(ENV_FAMILY, "")
    key = os.environ.pop(ENV_KEY, "")
    if not address or not key:
        return 2
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setQuitOnLastWindowClosed(False)
    listener = Listener(address, family=family or None, authkey=bytes.fromhex(key))
    driver = Driver()
    ui = UiCall(driver.handle)

    def serve() -> None:
        conn = None
        try:
            conn = listener.accept()
            while True:
                try:
                    raw = conn.recv_bytes()
                except (EOFError, OSError):
                    break
                try:
                    msg = json.loads(raw.decode("utf-8"))
                    op = str(msg.get("op") or "")
                    result = ui.call(op, msg.get("args") or {}, float(msg.get("timeout") or 30))
                    conn.send_bytes(json.dumps({"id": msg.get("id"), **result}, ensure_ascii=False).encode("utf-8"))
                except (ValueError, OSError):
                    break
                if op == "close":
                    break
        except Exception:  # noqa: BLE001 — 连不上 / 口令不对：退出
            pass
        finally:
            for closer in (conn, listener):
                try:
                    if closer is not None:
                        closer.close()
                except Exception:  # noqa: BLE001
                    pass
            ui.call("close", {}, 10)
            ui.call("quit", {}, 5)

    thread = threading.Thread(target=serve, name="mrerp-desk-pipe", daemon=True)
    thread.start()
    # 托盘一直不来连、或连上后一直没开页面（批次线程出了意外）：过一会儿自己退出，不留进程
    QTimer.singleShot(int(CONNECT_WAIT_S * 1000 * 2), lambda: _quit_if_idle(driver))
    code = app.exec()
    return int(code or 0)


def _quit_if_idle(driver: Driver) -> None:
    """不能常驻：到点时页面一次都没打开过就退出。"""
    if driver.loads == 0 and not driver.loading:
        app = QApplication.instance()
        if app is not None:
            app.quit()
