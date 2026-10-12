# -*- coding: utf-8 -*-
"""Tomo 桌面窗口（同 Claude 桌面端）：自己的窗口里打开 Tomo 对话页，外加快捷输入条。

- 窗口里是网页版同一个对话页（入口 fatcc.com → 登录 → 公司自己的 /assistant），登录状态存在本机，
  下次打开直接回到上次的对话页；只开 Pearnly 自己的 https 页面，别的链接交给系统浏览器（tomo_links.py）。
- 关窗口只是藏到托盘，后台的回复、Express 推送照常；回复完成时对话页发网页通知，这里转成系统通知，点一下回到窗口。
- 对话页里下载的文件存进「下载」文件夹，完成后通知，点一下在资源管理器里选中它。
- 快捷输入条（Ctrl+Alt+Space，tomo_hotkey.py）：输入一句回车，Tomo 窗口开新对话发出去。
- 网页内核只在第一次打开 Tomo 时加载（开机自启时不占内存），之后一直留着，通知才能在窗口关着时到达。
- 没有「文件 / 视图」菜单栏（同 Claude 桌面端）：设置在对话页左下角菜单里，「此电脑」一栏经 tomo_bridge 读写本机；
  快捷键 Ctrl+N 新对话、Ctrl+, 设置、Ctrl+R 重新加载、Ctrl+= / Ctrl+- / Ctrl+0 缩放。
- 没有系统标题栏（同 Claude 桌面端，用户 2026-10-12）：对话页自己在顶上画菜单 / 侧栏 / 后退 / 前进与最小化 / 最大化 / 关闭，
  按钮经 tomo_bridge 的 window 调这里的 `window_action`。登录页等别的页面没有那一排，由这里画一条简洁标题栏（_ChromeBar）；
  四周留几像素给系统原生的拉伸（最大化时去掉），贴边分屏照常。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QFile, QIODevice, QPoint, QSettings, QStandardPaths, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QKeySequence, QPalette, QShortcut
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEngineDownloadRequest,
    QWebEnginePage,
    QWebEnginePermission,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import tomo_links
from .config import APPDATA_DIR
from .i18n import t
from .logger import get_logger

log = get_logger(__name__)

GRANTED = (QWebEnginePermission.PermissionType.Notifications, QWebEnginePermission.PermissionType.ClipboardReadWrite)


def _channel_script() -> QWebEngineScript:
    """把 QWebChannel 的网页端接上，连好后在页面上放 window.tomoDesktop 并发 tomo-desktop 事件。"""
    source = QFile(":/qtwebchannel/qwebchannel.js")
    source.open(QIODevice.OpenModeFlag.ReadOnly)
    code = bytes(source.readAll().data()).decode("utf-8")
    source.close()
    script = QWebEngineScript()
    script.setName("tomo-desktop")
    script.setSourceCode(code + "\nnew QWebChannel(qt.webChannelTransport,function(c){window.tomoDesktop=c.objects.tomoDesktop;"
                         "window.dispatchEvent(new Event('tomo-desktop'))});")
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
    script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    script.setRunsOnSubFrames(False)
    return script


def reveal(path: Path) -> None:
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


class _Popup(QWebEnginePage):
    """target=_blank / window.open：交给系统浏览器，不在 Tomo 里开新窗口。"""

    def acceptNavigationRequest(self, url, _type, _main):
        if tomo_links.route(url.toString()) != "block":
            QDesktopServices.openUrl(url)
        self.deleteLater()
        return False


class TomoPage(QWebEnginePage):
    def __init__(self, profile, parent=None) -> None:
        super().__init__(profile, parent)
        self.permissionRequested.connect(self._permission)

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if not is_main_frame:
            return True   # 对话页自己的预览框
        target = tomo_links.route(url.toString())
        if target == "external":
            QDesktopServices.openUrl(url)
        return target == "load"

    def createWindow(self, _type):
        return _Popup(self.profile(), self)

    def _permission(self, permission) -> None:
        if permission.permissionType() in GRANTED and tomo_links.is_own(permission.origin().toString()):
            permission.grant()
        else:
            permission.deny()


OPEN_WINDOW = None   # 进程内唯一的 Tomo 窗口（gui_tray._window_open 看它是否正在用）

RESIZE_MARGIN = 4    # 无边框窗口四周留给系统拉伸的宽度（像素，最大化时为 0）
# 窗口底色跟对话页一致（frontend/assistant/src/globals.css 的 --cc-bg-app，浅 / 深各一个），拉伸边与登录页标题栏用它
APP_BG = {"light": "#fbfbfb", "dark": "#262624"}
APP_INK = {"light": "#1a1a1a", "dark": "#faf9f5"}


def _scheme() -> str:
    try:
        return "dark" if QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark else "light"
    except AttributeError:
        return "light"


def _edges(widget: QWidget, pos: QPoint):
    """鼠标在窗口哪条边 / 哪个角上（拉伸用）。"""
    m, w, h = RESIZE_MARGIN, widget.width(), widget.height()
    edges = Qt.Edge(0)
    if pos.x() <= m:
        edges |= Qt.Edge.LeftEdge
    if pos.x() >= w - m:
        edges |= Qt.Edge.RightEdge
    if pos.y() <= m:
        edges |= Qt.Edge.TopEdge
    if pos.y() >= h - m:
        edges |= Qt.Edge.BottomEdge
    return edges


_CURSORS = {
    Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor, Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
    Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor, Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
    Qt.Edge.LeftEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeFDiagCursor,
    Qt.Edge.RightEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeFDiagCursor,
    Qt.Edge.RightEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeBDiagCursor,
    Qt.Edge.LeftEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeBDiagCursor,
}


class _Frame(QWidget):
    """窗口内容外面一圈：鼠标在边上时给拉伸光标，按下交给系统拉伸（窗口没有系统边框）。"""

    def __init__(self, window: "TomoWindow") -> None:
        super().__init__(window)
        self._window = window
        self.setMouseTracking(True)

    def mouseMoveEvent(self, event) -> None:
        edges = Qt.Edge(0) if self._window.isMaximized() else _edges(self, event.position().toPoint())
        self.setCursor(_CURSORS.get(edges, Qt.CursorShape.ArrowCursor))

    def mousePressEvent(self, event) -> None:
        edges = _edges(self, event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and edges and not self._window.isMaximized():
            self._window.windowHandle().startSystemResize(edges)


class _ChromeBar(QWidget):
    """登录页等非对话页上的简洁标题栏（对话页自己画标题栏，进了对话页这条就藏起来）。"""

    def __init__(self, window: "TomoWindow", icon) -> None:
        super().__init__(window)
        self._window = window
        self.setFixedHeight(36)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 0, 0)
        row.setSpacing(8)
        mark = QLabel(self)
        mark.setPixmap(icon.pixmap(16, 16))
        row.addWidget(mark)
        self.title = QLabel("CC", self)
        row.addWidget(self.title)
        row.addStretch(1)
        self.buttons = []
        for glyph, action, name in (("\u2014", lambda: window.showMinimized(), "min"),
                                    ("\u2610", window.toggle_maximized, "max"),
                                    ("\u2715", window.close, "close")):
            button = QToolButton(self)
            button.setText(glyph)
            button.setObjectName(name)
            button.setFixedSize(46, 36)
            button.clicked.connect(action)
            row.addWidget(button)
            self.buttons.append(button)
        self.restyle()

    def restyle(self) -> None:
        scheme = _scheme()
        self.setStyleSheet(
            f"QWidget {{ background: {APP_BG[scheme]}; color: {APP_INK[scheme]}; }}"
            "QToolButton { border: none; font-size: 12px; }"
            "QToolButton:hover { background: rgba(127,127,127,0.18); }"
            "QToolButton#close:hover { background: #c42b1c; color: #ffffff; }")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._window.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, event) -> None:
        self._window.toggle_maximized()


class TomoWindow(QMainWindow):
    def __init__(self, *, lang: str, icon, notify, bridge=None) -> None:
        super().__init__()
        global OPEN_WINDOW
        OPEN_WINDOW = self
        self.lang = lang
        self._notify = notify          # notify(title, body, on_click)
        self._pending_ask: str | None = None
        self._pending_settings: str | None = None
        self._settings = QSettings("Pearnly", "Tomo")
        self.setWindowTitle("CC")
        self.setWindowIcon(icon)
        self.setMinimumSize(420, 520)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)

        folder = APPDATA_DIR / "tomo"
        self.profile = QWebEngineProfile("tomo", self)
        self.profile.setPersistentStoragePath(str(folder / "web"))
        self.profile.setCachePath(str(folder / "cache"))
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        self.profile.setNotificationPresenter(self._present)
        self.profile.downloadRequested.connect(self._download)
        # 结果面板里的 PDF 用内置查看器显示：Qt 的 PDF 查看器要开插件才工作，不开就当成下载、预览一片空白
        # （2026-10-10 测试：图片、表格能预览，PDF 空白）
        for attribute in (QWebEngineSettings.WebAttribute.PluginsEnabled, QWebEngineSettings.WebAttribute.PdfViewerEnabled):
            self.profile.settings().setAttribute(attribute, True)

        self.view = QWebEngineView(self)
        self.page = TomoPage(self.profile, self.view)
        self.view.setPage(self.page)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.page.urlChanged.connect(self._remember)
        self.page.urlChanged.connect(self._chrome_for)
        self.page.loadFinished.connect(self._loaded)
        self.frame = _Frame(self)
        column = QVBoxLayout(self.frame)
        column.setSpacing(0)
        self.chrome = _ChromeBar(self, icon)
        column.addWidget(self.chrome)
        column.addWidget(self.view, 1)
        self.setCentralWidget(self.frame)
        self._fit_frame()
        if bridge is not None:
            self.channel = QWebChannel(self.page)
            self.channel.registerObject("tomoDesktop", bridge)
            self.page.setWebChannel(self.channel)
            self.page.scripts().insert(_channel_script())
        self._shortcuts()

        if not self.restoreGeometry(self._settings.value("geometry", b"")):
            self.resize(1180, 800)
        self.page.setUrl(QUrl(tomo_links.resume_url(str(self._settings.value("last_url", "") or ""))))

    def _shortcuts(self) -> None:
        for keys, action in (("Ctrl+N", lambda: self.ask("")), ("Ctrl+,", lambda: self.open_settings("")),
                             ("Ctrl+R", self.view.reload), ("Ctrl+=", lambda: self._zoom(0.1)),
                             ("Ctrl++", lambda: self._zoom(0.1)), ("Ctrl+-", lambda: self._zoom(-0.1)),
                             ("Ctrl+0", lambda: self._zoom(0))):
            QShortcut(QKeySequence(keys), self, activated=action)

    # ── 无边框窗口（页面画的标题栏经 tomo_bridge 调这里） ──
    def window_action(self, action: str, args: dict) -> dict:
        if action == "minimize":
            self.showMinimized()
        elif action == "maximize":
            self.toggle_maximized()
        elif action == "close":
            self.close()
        elif action == "move":
            self.windowHandle().startSystemMove()
        elif action == "zoom":
            step = int(args.get("step") or 0)
            self._zoom(0.1 * step if step else 0)
        elif action != "state":
            return {"ok": False}
        return {"ok": True, "maximized": self.isMaximized()}

    def toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _fit_frame(self) -> None:
        margin = 0 if self.isMaximized() or self.isFullScreen() else RESIZE_MARGIN
        self.frame.layout().setContentsMargins(margin, margin, margin, margin)
        palette = self.frame.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(APP_BG[_scheme()]))
        self.frame.setAutoFillBackground(True)
        self.frame.setPalette(palette)
        self.chrome.restyle()

    def _chrome_for(self, url: QUrl) -> None:
        self.chrome.setVisible(not tomo_links.is_chat(url.toString()))

    def changeEvent(self, event) -> None:
        if event.type() in (QEvent.Type.WindowStateChange, QEvent.Type.ThemeChange, QEvent.Type.PaletteChange) \
                and hasattr(self, "chrome"):
            self._fit_frame()
        super().changeEvent(event)

    def _zoom(self, step: float) -> None:
        factor = 1.0 if not step else min(2.0, max(0.5, round(self.view.zoomFactor() + step, 2)))
        self.view.setZoomFactor(factor)
        self._settings.setValue("zoom", factor)

    def _context_menu(self, pos) -> None:
        """右键只留编辑类（剪切 / 复制 / 粘贴 / 全选 / 复制链接），不出「查看源码」「返回」这些浏览器项。"""
        request = self.view.lastContextMenuRequest()
        menu = self.view.createStandardContextMenu()
        keep = {self.page.action(a) for a in (QWebEnginePage.WebAction.Cut, QWebEnginePage.WebAction.Copy,
                                              QWebEnginePage.WebAction.Paste, QWebEnginePage.WebAction.SelectAll,
                                              QWebEnginePage.WebAction.CopyLinkToClipboard)}
        for action in menu.actions():
            if action not in keep and not action.isSeparator():
                menu.removeAction(action)
        if request is None or not any(a.isEnabled() and not a.isSeparator() for a in menu.actions()):
            return
        menu.exec(self.view.mapToGlobal(pos))

    # ── 打开 / 提问 ──
    def show_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def ask(self, text: str) -> None:
        """快捷输入条或「新对话」：拿到前面，开新对话（有文字就发出去）。"""
        self.show_front()
        if tomo_links.is_chat(self.page.url().toString()) and not self.page.isLoading():
            self.page.runJavaScript(tomo_links.ask_script(text))
        else:
            self._pending_ask = text   # 还在登录 / 加载：进了对话页再交过去

    def open_settings(self, section: str = "") -> None:
        """托盘「连接与设置」/ Ctrl+,：拿到前面并打开对话页的设置（section 为空 = 常规）。"""
        self.show_front()
        script = "(function f(n){if(window.tomo&&window.tomo.settings){window.tomo.settings(%s);return}" \
                 "if(n>0)setTimeout(function(){f(n-1)},250)})(40)" % json.dumps(section or "")
        if tomo_links.is_chat(self.page.url().toString()) and not self.page.isLoading():
            self.page.runJavaScript(script)
        else:
            self._pending_settings = script

    def _loaded(self, ok: bool) -> None:
        zoom = float(self._settings.value("zoom", 1.0) or 1.0)
        if abs(self.view.zoomFactor() - zoom) > 0.001:
            self.view.setZoomFactor(zoom)
        if ok and self._pending_ask is not None and tomo_links.is_chat(self.page.url().toString()):
            text, self._pending_ask = self._pending_ask, None
            self.page.runJavaScript(tomo_links.ask_script(text))
        if ok and self._pending_settings and tomo_links.is_chat(self.page.url().toString()):
            script, self._pending_settings = self._pending_settings, None
            self.page.runJavaScript(script)

    def _remember(self, url: QUrl) -> None:
        value = url.toString()
        if tomo_links.is_chat(value):
            self._settings.setValue("last_url", value)

    def closeEvent(self, event) -> None:
        """关窗口 = 藏到托盘（后台回复与 Express 推送照常）；第一次关时提示一次还在后台。"""
        self._settings.setValue("geometry", self.saveGeometry())
        event.ignore()
        self.hide()
        if not self._settings.value("told_background", False, type=bool):
            self._settings.setValue("told_background", True)
            self._notify("CC", t("tomo_still_running", self.lang), self.show_front)

    def shutdown(self) -> None:
        self._settings.setValue("geometry", self.saveGeometry())
        self.view.setPage(None)
        self.page.deleteLater()

    # ── 通知 / 下载 ──
    def _present(self, notification) -> None:
        self._last_notification = notification   # 留引用，点了才能回调网页

        def clicked(n=notification) -> None:
            self.show_front()
            n.click()

        self._notify(notification.title() or "CC", notification.message(), clicked)

    def _download(self, request: QWebEngineDownloadRequest) -> None:
        folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or Path.home())
        path = tomo_links.unique_path(folder, request.downloadFileName())
        request.setDownloadDirectory(str(path.parent))
        request.setDownloadFileName(path.name)
        request.accept()

        def finished(r=request, p=path) -> None:
            if r.state() == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
                self._notify("CC", t("tomo_downloaded", self.lang, name=p.name), lambda: reveal(p))
            elif r.state() == QWebEngineDownloadRequest.DownloadState.DownloadInterrupted:
                log.warning("Tomo 下载中断：%s", r.interruptReasonString())

        request.isFinishedChanged.connect(finished)


class QuickAsk(QWidget):
    """快捷输入条：屏幕上方居中的一条圆角输入框，回车交给 Tomo 窗口，Esc 或点别处就收起。"""

    submitted = Signal(str)

    def __init__(self, *, lang: str, icon) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        frame = QFrame(self)
        frame.setObjectName("quick")
        frame.setStyleSheet("#quick{background:#ffffff;border:1px solid #e5e5e5;border-radius:18px}"
                            "QLineEdit{border:0;background:transparent;font-size:17px;color:#1a1a18;padding:0}")
        shadow = QGraphicsDropShadowEffect(frame)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 8)
        shadow.setColor(Qt.GlobalColor.gray)
        frame.setGraphicsEffect(shadow)
        mark = QLabel(frame)
        mark.setPixmap(icon.pixmap(30, 30))
        self.input = QLineEdit(frame)
        self.input.setPlaceholderText(t("tomo_ask_placeholder", lang))
        self.input.returnPressed.connect(self._submit)
        row = QHBoxLayout(frame)
        row.setContentsMargins(16, 14, 18, 14)
        row.setSpacing(12)
        row.addWidget(mark)
        row.addWidget(self.input, 1)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)   # 给阴影留位置
        outer.addWidget(frame)
        self.resize(680, 112)

    def popup(self) -> None:
        from PySide6.QtGui import QCursor, QGuiApplication

        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(area.x() + (area.width() - self.width()) // 2, area.y() + int(area.height() * 0.24))
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def _submit(self) -> None:
        text = self.input.text().strip()
        self.input.clear()
        self.hide()
        if text:
            self.submitted.emit(text)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.input.clear()
            self.hide()
            return
        super().keyPressEvent(event)

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self.hide()
        super().changeEvent(event)
