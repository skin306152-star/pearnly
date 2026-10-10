# -*- coding: utf-8 -*-
"""Tomo 桌面窗口（同 Claude 桌面端）：自己的窗口里打开 Tomo 对话页，外加快捷输入条。

- 窗口里是网页版同一个对话页（入口 ai.pearnly.com → 登录 → 公司自己的 /assistant），登录状态存在本机，
  下次打开直接回到上次的对话页；只开 Pearnly 自己的 https 页面，别的链接交给系统浏览器（tomo_links.py）。
- 关窗口只是藏到托盘，后台的回复、Express 推送照常；回复完成时对话页发网页通知，这里转成系统通知，点一下回到窗口。
- 对话页里下载的文件存进「下载」文件夹，完成后通知，点一下在资源管理器里选中它。
- 快捷输入条（Ctrl+Alt+Space，tomo_hotkey.py）：输入一句回车，Tomo 窗口开新对话发出去。
- 网页内核只在第一次打开 Tomo 时加载（开机自启时不占内存），之后一直留着，通知才能在窗口关着时到达。
- 没有「文件 / 视图」菜单栏（同 Claude 桌面端）：设置在对话页左下角菜单里，「此电脑」一栏经 tomo_bridge 读写本机；
  快捷键 Ctrl+N 新对话、Ctrl+, 设置、Ctrl+R 重新加载、Ctrl+= / Ctrl+- / Ctrl+0 缩放。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QFile, QIODevice, QSettings, QStandardPaths, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEngineDownloadRequest,
    QWebEnginePage,
    QWebEnginePermission,
    QWebEngineProfile,
    QWebEngineScript,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
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

        folder = APPDATA_DIR / "tomo"
        self.profile = QWebEngineProfile("tomo", self)
        self.profile.setPersistentStoragePath(str(folder / "web"))
        self.profile.setCachePath(str(folder / "cache"))
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        self.profile.setNotificationPresenter(self._present)
        self.profile.downloadRequested.connect(self._download)

        self.view = QWebEngineView(self)
        self.page = TomoPage(self.profile, self.view)
        self.view.setPage(self.page)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.page.urlChanged.connect(self._remember)
        self.page.loadFinished.connect(self._loaded)
        self.setCentralWidget(self.view)
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
