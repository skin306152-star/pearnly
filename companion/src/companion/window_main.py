# -*- coding: utf-8 -*-
"""CC 窗口进程（``companion.exe --window``，2026-10-18 轻量化）：只在打开 CC 窗口时由托盘拉起，关窗口就退出。

网页内核（QtWebEngine、QtWebEngineProcess）只活在这个进程里，托盘进程不加载它（window_link.py 有两边怎么说话）：

- 对话页的桥（tomoDesktop：state / call / changed / done）由这里的 ``RemoteBridge`` 接住，转给托盘里照旧的
  tomo_bridge.TomoBridge 应答；窗口按钮（最小化 / 最大化 / 关闭 / 拖动 / 缩放）就在本进程做，不绕托盘。
- 托盘请弹的选择文件夹框在这里弹，以 CC 窗口为父、模态（同原来）。
- 窗口里要弹的系统通知（回复完成、对话提醒、下载完成、第一次关窗）交给托盘弹（托盘按 ``notice:<编号>`` 去重）；
  点了托盘再转回来执行（窗口进程已退出时托盘直接打开窗口；下载完成的「在文件夹中显示」托盘自己做）。
- 关窗口：正在回复（页面 ``window.tomo.busy()``）就先藏起来等回复完、通知弹出后再退；否则马上退。旧版页面没有
  ``busy`` 就当没在回复。托盘退出、管道断了也退出。
- 窗口开着却没人看得见（锁屏、屏幕关了、被别的窗口完全挡住；最小化由 Qt 自己处理）时对话页停画（visibility_watch.py），
  原因随 ``status`` 报给托盘。
"""

from __future__ import annotations

import json
import os
import sys
from collections import OrderedDict
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QEventLoop, QObject, QTimer, Signal, Slot

from . import tomo_links, window_link
from .logger import get_logger
from .visibility_watch import VisibilityWatch

log = get_logger(__name__)

REFUSED = json.dumps({"ok": False, "refused": True})
FAILED = json.dumps({"ok": False})
BUSY_SCRIPT = "!!(window.tomo&&typeof window.tomo.busy==='function'&&window.tomo.busy())"
BUSY_POLL_MS = 3000          # 关窗口时正在回复：多久再问一次回复完了没有
BUSY_ANSWER_MS = 3000        # 页面这么久没回答「在不在回复」（加载中、卡住）：当没在回复
LINGER_LIMIT_MS = 30 * 60 * 1000   # 关窗口后等回复最多等这么久
LINGER_GRACE_MS = 1500       # 回复刚完：再留这么久，让页面发的「回复完成」通知先到托盘再退
FIRST_COMMAND_MS = 5000      # 连上托盘后等第一条命令最多这么久（托盘收下就马上交过来）
# 这几个会在托盘那边请窗口弹选择文件夹框，人选多久就等多久（不设上限，否则选得慢页面报失败、设置却已改了）
PICKER_METHODS = frozenset({"pick_roots", "pick_folder", "add_folder"})
ACTION_MEMORY = 50           # 转给托盘的通知，点了要做的事记最近几条


class RemoteBridge(QObject):
    """窗口进程里的 tomoDesktop：接口与 tomo_bridge.TomoBridge 一样（页面不用改），只在 CC 自己的对话页上应答。"""

    changed = Signal()
    done = Signal(str, str)
    open_settings = Signal(str)

    def __init__(self, link, *, window: Callable[[], object], page_url: Callable[[], str]) -> None:
        super().__init__()
        self._link = link
        self._window = window
        self._page_url = page_url

    def _allowed(self) -> bool:
        return tomo_links.is_chat(self._page_url())

    @Slot(result=str)
    def state(self) -> str:
        if not self._allowed():
            return REFUSED
        result = self._link.request("state", url=self._page_url())
        return result if isinstance(result, str) else FAILED

    @Slot(str, str, result=str)
    def call(self, method: str, args_json: str) -> str:
        if not self._allowed():
            return REFUSED
        if method == "window":
            # 对话页画的标题栏：就在本进程做（拖动、最大化要跟手，不绕托盘）
            try:
                args = json.loads(args_json or "{}")
            except ValueError:
                args = {}
            window = self._window()
            result = window.window_action(str(args.get("action") or ""), args) if window is not None else {"ok": False}
            return json.dumps(result, ensure_ascii=False)
        timeout = None if method in PICKER_METHODS else window_link.REQUEST_TIMEOUT_MS
        result = self._link.request("call", method=str(method), args=str(args_json or ""), url=self._page_url(),
                                    timeout_ms=timeout)
        return result if isinstance(result, str) else FAILED


class WindowController(QObject):
    """建 CC 窗口、接托盘的命令、报窗口状态、关窗口时决定什么时候退。"""

    def __init__(self, link, *, lang: str, quit_app: Callable[[], None], make_window=None, system=None) -> None:
        super().__init__()
        self._link = link
        self._quit_app = quit_app
        self._actions: OrderedDict = OrderedDict()
        self._last_status: Optional[tuple] = None
        self._lingering = False
        self._leaving = False
        self._finished = False
        self._linger = QTimer(self)
        self._linger.setInterval(BUSY_POLL_MS)
        self._linger.timeout.connect(self._check_busy)
        self._linger_deadline = QTimer(self)
        self._linger_deadline.setSingleShot(True)
        self._linger_deadline.timeout.connect(self.finish)
        self.bridge = RemoteBridge(link, window=lambda: self.window, page_url=self._page_url)
        if make_window is None:
            from .gui_icon import pearnly_icon
            from .gui_tomo import TomoWindow

            def make_window(**kwargs):
                return TomoWindow(icon=pearnly_icon(), **kwargs)
        self.window = make_window(lang=lang, notify=self._notify, bridge=self.bridge, on_close=self._closed)
        # 状态变了回到事件循环再报（构造时第一次算出的「窗口关着」也是这样，不抢在窗口显示前发）
        self.visibility = VisibilityWatch(self.window, system=system,
                                          on_change=lambda _reason: QTimer.singleShot(0, self._report))
        self.window.installEventFilter(self)
        self.window.page.urlChanged.connect(lambda _url: self._report())
        link.set_handler(self.handle)
        link.lost.connect(self.finish)

    def _page_url(self) -> str:
        window = getattr(self, "window", None)
        return window.page.url().toString() if window is not None else ""

    # ── 托盘发来的 ──
    def handle(self, message: dict):
        op = message.get("op")
        window = self.window
        if op in ("show", "ask", "open_conversation", "open_settings"):
            self._stop_linger()
        if op in ("show", "ask", "open_conversation", "open_settings", "click"):
            # 有人要看：先当看得见（被挡住、停着画时，快捷提问等页面脚本里要等下一帧的事不卡到下一次重算）
            self.visibility.present()
        if op == "show":
            window.show_front()
        elif op == "ask":
            window.ask(str(message.get("text") or ""))
        elif op == "open_conversation":
            window.open_conversation(str(message.get("url") or ""), str(message.get("session") or ""))
        elif op == "open_settings":
            window.open_settings(str(message.get("section") or ""))
        elif op == "changed":
            self.bridge.changed.emit()
        elif op == "done":
            self.bridge.done.emit(str(message.get("job") or ""), str(message.get("result") or ""))
        elif op == "pick":
            return self._pick(str(message.get("title") or ""), str(message.get("start") or ""))
        elif op == "click":
            action = self._actions.pop(str(message.get("id") or ""), None)
            if action is not None:
                action()
            else:
                window.show_front()
        elif op == "lang":
            window.lang = str(message.get("lang") or window.lang)
        elif op == "quit":
            self.finish()
        return None

    def _pick(self, title: str, start: str) -> str:
        from PySide6.QtWidgets import QFileDialog

        return QFileDialog.getExistingDirectory(self.window, title, start) or ""

    # ── 交给托盘的 ──
    def _notify(self, title: str, body: str, on_click=None, tag: str = "") -> None:
        from .gui_quick import Reveal

        click, path = "", ""
        if isinstance(on_click, Reveal):
            path = str(on_click.path)
        elif on_click is not None:
            click = os.urandom(8).hex()
            self._actions[click] = on_click
            while len(self._actions) > ACTION_MEMORY:
                self._actions.popitem(last=False)
        self._link.send("notify", title=str(title or ""), body=str(body or ""), tag=str(tag or ""),
                        click=click, reveal=path)

    def _report(self) -> None:
        if self._finished:
            return   # 页面已经删了，排着的报告不再发
        window = self.window
        status = (bool(window.isVisible() and not window.isMinimized()), bool(window.isActiveWindow()), self._page_url(),
                  str(self.visibility.reason or ""))
        if status != self._last_status:
            self._last_status = status
            self._link.send("status", visible=status[0], active=status[1], url=status[2], paused=status[3])

    def eventFilter(self, watched, event) -> bool:
        if watched is self.window and event.type() in (QEvent.Type.Show, QEvent.Type.Hide,
                                                       QEvent.Type.WindowStateChange, QEvent.Type.ActivationChange):
            QTimer.singleShot(0, self._report)   # 事件处理完、状态落定后再报
        return False

    # ── 关窗口 ──
    def _closed(self) -> None:
        """窗口已藏起来（TomoWindow.closeEvent）：没在回复就退，在回复就等回复完。"""
        self._report()
        self._check_busy(first=True)

    def _check_busy(self, first: bool = False) -> None:
        if self._finished or self.window.isVisible():
            self._stop_linger()
            return
        answered = {"done": False}

        def answer(busy) -> None:
            if answered["done"]:
                return
            answered["done"] = True
            if self._finished or self.window.isVisible():
                return
            if busy is True:
                if not self._lingering:
                    self._lingering = True
                    self._linger.start()
                    self._linger_deadline.start(LINGER_LIMIT_MS)
                    log.info("关窗口时正在回复：等回复完再退出窗口进程")
                return
            if self._lingering:
                # 等着的回复刚完：页面这时才发「回复完成」通知（经网页内核另一条通道过来），再留一会儿让它先到托盘
                self._stop_linger()
                if not self._leaving:
                    self._leaving = True
                    QTimer.singleShot(LINGER_GRACE_MS, self._leave)
                return
            self.finish()

        QTimer.singleShot(BUSY_ANSWER_MS, lambda: answer(None))
        self.window.page.runJavaScript(BUSY_SCRIPT, 0, answer)

    def _leave(self) -> None:
        self._leaving = False
        if not self._finished and not self.window.isVisible():
            self.finish()

    def _stop_linger(self) -> None:
        self._lingering = False
        self._linger.stop()
        self._linger_deadline.stop()

    def finish(self) -> None:
        """退出窗口进程（网页内核随之释放）。"""
        if self._finished:
            return
        self._finished = True
        self._stop_linger()
        self.visibility.close()
        try:
            self.window.shutdown()
        except Exception:
            log.exception("关闭 CC 窗口失败")
        self._link.send("bye")
        self._link.close()
        self._quit_app()


def _register_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        from .toast import AUMID

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(AUMID)
    except Exception:
        pass


def run() -> int:
    """``companion.exe --window`` 的入口：连上托盘（拉起时给的管道名与口令），建窗口，跑到窗口退出。"""
    from .logger import setup_window_logging

    # 读完就从环境里拿掉：网页内核的子进程（QtWebEngineProcess）会继承环境变量，口令不留给它们
    name, token = os.environ.pop(window_link.ENV_PIPE, ""), os.environ.pop(window_link.ENV_TOKEN, "")
    if not name or not token:
        return 2   # 不是托盘拉起的：CC 窗口只由托盘打开（再点一次桌面图标走单实例那条路）
    setup_window_logging()
    _register_app_user_model_id()   # 任务栏按钮与开始菜单的 CC 归在一起（同 main.APP_USER_MODEL_ID）
    from .gui_font import qt_app
    from .gui_icon import pearnly_icon

    app = qt_app()
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(pearnly_icon())
    link = window_link.TrayLink(name, token)
    # 先等托盘交来的第一条命令再建窗口：托盘若叫退（已经有一个窗口进程、或 CC 正在退出），不建网页内核直接退，
    # 免得两个进程同时打开同一份网页存储
    early: list = []
    waiting = QEventLoop()

    def keep(message: dict) -> None:
        early.append(message)
        waiting.quit()

    link.set_handler(keep)
    link.lost.connect(waiting.quit)
    if not link.connect_to_tray():
        log.warning("CC 窗口进程连不上托盘，退出")
        return 1
    QTimer.singleShot(FIRST_COMMAND_MS, waiting.quit)
    if not early and link.alive:
        waiting.exec()
    if not link.alive or any(message.get("op") == "quit" for message in early):
        log.info("托盘叫 CC 窗口进程退出（已有窗口或正在退出），不建窗口")
        link.close()
        return 0
    from .i18n import default_lang, normalize

    controller = WindowController(link, lang=normalize(os.environ.get(window_link.ENV_LANG, "") or default_lang()),
                                  quit_app=app.quit)
    for message in early or [{"op": "show"}]:
        controller.handle(message)
    log.info("CC 窗口进程已启动 · pid=%s", os.getpid())
    code = app.exec()
    del controller
    return code


if __name__ == "__main__":
    sys.exit(run())
