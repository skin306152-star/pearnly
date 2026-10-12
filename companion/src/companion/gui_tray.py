# -*- coding: utf-8 -*-
"""系统托盘常驻(PySide6)· 后台线程跑 DBF 直写 poll loop,托盘显示连接/活动状态。

2026-10-10 起小助手并入 Tomo 桌面端：托盘左键、Ctrl+Alt+Space、再次打开程序都是打开 Tomo 窗口（gui_tomo.py）；
Express / MR.ERP 的连接与设置留在「连接与设置」主窗口，取任务、直写、文件夹录入照旧在后台跑，没配对也能单用 Tomo。

主线程 = Qt 事件循环 + 托盘图标(绿空闲/黄工作/红错误/灰离线)+ 右键菜单(状态/打开小助手/
退出;左键单击也打开小助手主窗口);后台 QThread = QueueClient poll → _handle_dbf_task 直写 → 周期心跳 +
夜间 PACK tick(复用 main 的核心,不重复直写逻辑)。worker 出状态/离线通过信号回主线程。
"""

from __future__ import annotations

import sys
import time
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QMessageBox, QSystemTrayIcon

from . import watchdog
from .config import CompanionConfig
from .auto_update import AutoUpdate
from .folder_worker import FolderIntake
from .gui_font import qt_app
from .gui_pairing_support import ProbeThread
from .gui_tray_workers import (
    PollWorker,
    UpdateWorker as _UpdateWorker,
)
from .i18n import MessageSpec, default_lang, normalize, t
from .logger import activity, get_logger


def asset_path(name: str) -> Path:
    """资源定位:frozen exe 走 PyInstaller 解包目录(_MEIPASS),否则 repo assets/。"""
    base = getattr(sys, "_MEIPASS", None)
    return (
        (Path(base) if base else Path(__file__).resolve().parents[2]) / "assets" / name
    )


log = get_logger(__name__)

_COLORS = {
    "idle": "#2ecc71",  # 绿:空闲已连
    "working": "#f1c40f",  # 黄:录入中
    "indexing": "#f1c40f",  # 黄:后台整理索引(让 Express 立即可查)
    "waiting_lock": "#e67e22",  # 橙黄:等 Express 释放账套(自动重试·非失败)
    "error": "#e74c3c",  # 红:失败需处理
    "offline": "#95a5a6",  # 灰:离线
}

# 走 st_<key> 翻译的状态(其余 key 原样显示)。
_LABELED_STATES = (
    "idle",
    "working",
    "indexing",
    "waiting_lock",
    "error",
    "offline",
    "connected",
)


_ICON_CACHE: dict = {}


def _dot_icon(color: str) -> QIcon:
    icon = _ICON_CACHE.get(color)
    if icon is None:
        pm = QPixmap(16, 16)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(color))
        p.setPen(Qt.NoPen)
        p.drawEllipse(2, 2, 12, 12)
        p.end()
        icon = QIcon(pm)
        _ICON_CACHE[color] = icon
    return icon


_BASE_PM: QPixmap | None = None


def pearnly_icon() -> QIcon:
    """Pearnly logo(任务栏/窗口图标 · 与 exe 内嵌图标一致)。"""
    return QIcon(_pearnly_pixmap())


def _pearnly_pixmap() -> QPixmap:
    global _BASE_PM
    if _BASE_PM is None:
        p = asset_path("cc.png")
        _BASE_PM = QPixmap(str(p)) if p.exists() else QPixmap()
    return _BASE_PM


def _status_icon(key: str) -> QIcon:
    """托盘图标 = CC 图标（带胡须原版）+ 右下角状态点(brand + 状态都在)。无资源时回落纯色点。"""
    icon = _ICON_CACHE.get("logo:" + key)
    if icon is not None:
        return icon
    base = _pearnly_pixmap()
    if base.isNull():
        return _dot_icon(_COLORS.get(key, "offline"))
    pm = base.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation).copy()
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(_COLORS.get(key, "offline")))
    p.setPen(QColor("#ffffff"))
    d = 22
    p.drawEllipse(pm.width() - d - 2, pm.height() - d - 2, d, d)
    p.end()
    icon = QIcon(pm)
    _ICON_CACHE["logo:" + key] = icon
    return icon



def _window_open() -> bool:
    """有人正在用 Tomo 窗口（开着且在最前面）：自动更新先不装（只看已经打开过的模块，不为这个去导入界面）。"""
    window = getattr(sys.modules.get(__package__ + ".gui_tomo"), "OPEN_WINDOW", None)
    try:
        return bool(window is not None and window.isVisible() and window.isActiveWindow())
    except RuntimeError:
        return False  # Qt 对象已回收


MENU_TEXT_MAX = 48
# 弹过的对话提醒记多少条（按 notice:<编号> 去重：页面实时收到与文件夹同步带来的是同一条）
NOTICE_MEMORY = 500
NOTICE_TAG = "notice:"
# 刚弹出、还没点的带点击动作的通知（对话提醒等）这么久之内，「有新版本」不另弹通知——托盘点击只记得住最后一条的动作，
# 新版提示会把它冲掉；托盘菜单里照样有「更新到最新版」。
NOTICE_HOLD_SECONDS = 120


def _menu_text(text: str) -> str:
    """托盘菜单状态行：过长截断（完整内容在悬停提示里），不把菜单撑宽。"""
    value = " ".join(str(text or "").split())
    return value if len(value) <= MENU_TEXT_MAX else value[: MENU_TEXT_MAX - 1] + "…"

class TrayApp(QObject):
    worker_stopped = Signal(object)
    folder_status = Signal(str, str)  # MR.ERP 的小助手成员（只读文件夹）：文件夹线程每次同步后报 idle / offline
    folder_notices = Signal(str, object)  # 文件夹同步带来的对话提醒：(站点, [提醒…])
    folder_update = Signal(str, str)  # 文件夹同步带来的新版本（只读文件夹的成员没有心跳）：(版本, 地址)

    def __init__(self, configs, *, open_tomo: bool = False) -> None:
        app = qt_app()  # 泰文 Windows 上顺带修正界面字体（gui_font）
        super().__init__()
        if isinstance(configs, CompanionConfig):
            configs = [configs]
        self.configs = list(configs)
        self.cfg = self.configs[0] if self.configs else CompanionConfig()
        self.lang = normalize(getattr(self.cfg, "ui_lang", "") or default_lang())
        self.app = app
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setWindowIcon(pearnly_icon())  # 任务栏/窗口图标 = Pearnly logo
        self.tray = QSystemTrayIcon(_status_icon("offline"))
        self.tray.setToolTip(t("tray_tooltip", self.lang))
        self._status_action = None
        self._update_action = None
        self._open_action = None
        self._quit_action = None
        self._profile_states: dict[str, tuple[str, object]] = {}
        self._last_keys: dict[str, str] = {}
        self._pending_update = None  # (version, url)
        self._update_worker = None  # 后台下载线程(存活期持引用防 GC)
        # 强制自动更新（auto_update.py）：发现新版就下载，空闲时停下手上的活再静默安装。
        self._updating = False
        self._update_requested = False  # 有人点了「立即更新」：主窗口开着也照装
        self._auto_update = AutoUpdate(
            busy=self._busy, stop_work=self._stop_for_update, work_stopped=self._work_stopped,
            resume_work=self._resume_after_update, start_download=self._start_download,
            install=self._install_update)
        self._auto_timer = QTimer()
        self._auto_timer.timeout.connect(self._auto_update_tick)
        # 新版本由服务端随心跳回复带来（worker._offer_update，用户 2026-10-09：不要定时任务做更新检查）；
        # 这里只在有人点「检查更新」时查一次 latest.json。
        self._checking = None  # 在跑的检查线程（持引用防 GC）
        self._checked_latest = False  # 手动检查过、确实没有新版（界面显示「已是最新版本」）
        self._restart_pending = False
        self._quitting = False
        self.worker_stopped.connect(self._on_worker_finished)
        self._build_menu()
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        activity("log_started", lang=self.lang)
        # 常驻模式（always_on.py）：不让电脑睡眠；开机自启进来先锁屏（自动登录的桌面别人碰不到），之后照常干活
        from .always_on import KeepAwake, lock_screen

        self._keep_awake = KeepAwake()
        if getattr(self.cfg, "always_on", False):
            self._keep_awake.set(True)
            if not open_tomo:
                lock_screen()
        # 从 Program Files 迁到用户目录安装后，开机自启要指向新位置（只改已经开着的，不替人打开）。
        try:
            from . import autostart

            if autostart.repoint():
                log.info("开机自启已改指向当前安装位置")
        except Exception:
            log.exception("核对开机自启位置失败（忽略）")
        self._heal = watchdog.HealBackoff()  # 自愈退避:防目标永久不可达时无限重启空转
        self.worker = self._spawn_worker()
        # 文件夹自动录入：独立守护线程，只在本机给某条连接设了允许读取的根目录时才启动（folder_worker.py）。
        self.folder_status.connect(lambda profile_id, key: self._on_status(profile_id, key, ""))
        self.folder_notices.connect(self._on_folder_notices)
        self.folder_update.connect(self._on_update_available)
        self._folders = FolderIntake(lambda: self.configs, lang=lambda: self.lang,
                                     on_status=lambda profile_id, key: self.folder_status.emit(profile_id, key),
                                     on_notices=lambda site, items: self.folder_notices.emit(site, items),
                                     on_update=lambda version, url: self.folder_update.emit(version, url))
        self._folders.start()
        # Tomo：窗口第一次打开时才建（开机自启不加载网页内核）；快捷输入条、全局快捷键、「再打开一次」的管道一开始就在。
        self._tomo = None
        self._message_action = None   # 点托盘通知时做什么（Tomo 的回复 / 下载通知 / 对话提醒才有）
        self._message_at = 0.0        # 那条带动作的通知什么时候弹的（单调时钟）
        self.tray.messageClicked.connect(self._on_message_clicked)
        from .gui_tomo import QuickAsk
        from .tomo_hotkey import GlobalHotkey
        from . import instance_channel

        self._quick = QuickAsk(lang=self.lang, icon=pearnly_icon())
        self._quick.submitted.connect(lambda text: self.open_tomo(text))
        self._hotkey = GlobalHotkey()
        self._hotkey.pressed.connect(self._quick.popup)
        self._channel = instance_channel.listen(lambda: self.open_tomo())
        # 看门狗:主线程定时巡检 worker 存活时标,卡死即自愈重启。主线程 Qt 事件循环不受
        # worker(后台 QThread)卡死影响,QTimer 必按时触发 → 卡死能被稳稳兜住。
        self._watchdog = QTimer()
        self._watchdog.timeout.connect(self._watchdog_check)
        self._watchdog.start(15000)
        if open_tomo:
            QTimer.singleShot(0, self.open_tomo)

    def _spawn_worker(self) -> PollWorker:
        """建 poll worker、接好信号并启动(首次启动与自愈重启共用,避免两处接线漂移)。"""
        # MR.ERP 的小助手成员只读文件夹：不进取任务 / 心跳线程，状态由文件夹线程报（`folder_status`）。
        w = PollWorker([cfg for cfg in self.configs if getattr(cfg, "kind", "express") != "folder"])
        w.status.connect(self._on_status)
        w.update_available.connect(self._on_update_available)
        w.batch_done.connect(self._on_batch_done)
        w.busy_prompt.connect(self._on_busy_prompt)
        # finished originates on the worker thread. Relay through this QObject so
        # worker replacement and app.quit always execute on the GUI thread.
        w.finished.connect(lambda current=w: self.worker_stopped.emit(current))
        w.start()
        return w

    def _build_menu(self) -> None:
        menu = QMenu()
        self._status_action = menu.addAction(t("tray_starting", self.lang))
        self._status_action.setEnabled(False)
        # 有新版才显示的「更新到最新版」项(默认隐藏)。
        self._update_action = menu.addAction("")
        self._update_action.setVisible(False)
        self._update_action.triggered.connect(self._do_update)
        menu.addSeparator()
        self._tomo_action = menu.addAction(t("tomo_open", self.lang), self.open_tomo)
        font = self._tomo_action.font()
        font.setBold(True)
        self._tomo_action.setFont(font)
        # 连接、网页可选的文件夹、开机启动、日志都在「连接与设置」主窗口（2026-10-08）。
        self._open_action = menu.addAction(t("tray_open", self.lang), self._repair)
        menu.addSeparator()
        self._quit_action = menu.addAction(t("tray_quit", self.lang), self._quit)
        self.tray.setContextMenu(menu)

    def _apply_language(self, lang: str) -> None:
        self.lang = normalize(lang)
        self._retranslate()

    def _retranslate(self) -> None:
        if getattr(self, "_tomo_action", None):
            self._tomo_action.setText(t("tomo_open", self.lang))
        if self._open_action:
            self._open_action.setText(t("tray_open", self.lang))
        if self._quit_action:
            self._quit_action.setText(t("tray_quit", self.lang))
        if self._update_action and self._pending_update:
            self._update_action.setText(
                t("tray_update_to", self.lang, ver=self._pending_update[0])
            )
        self._render_status()

    def _detail_text(self, detail: object) -> str:
        if isinstance(detail, MessageSpec):
            return detail.render(self.lang)
        return str(detail or "")

    def _render_status(self) -> None:
        if not self._profile_states:
            text = t("tray_starting" if self.configs else "tray_no_connection", self.lang)
            self.tray.setIcon(_status_icon("offline"))
            self.tray.setToolTip(f"{t('tray_tooltip', self.lang)} · {text}")
            if self._status_action:
                self._status_action.setText(text)
            return
        overall, display_profile, display_detail = self._aggregate_status()
        self.tray.setIcon(_status_icon(overall))
        label = t("st_" + overall, self.lang) if overall in _LABELED_STATES else overall
        detail = self._detail_text(display_detail)
        text = f"{label} · {detail}" if detail else label
        if len([cfg for cfg in self.configs if not cfg.paused]) > 1 and display_profile:
            text = f"{self._profile_label(display_profile)} · {text}"
        self.tray.setToolTip(f"{t('tray_tooltip', self.lang)} · {text}")
        if self._status_action:
            self._status_action.setText(_menu_text(text))

    def _on_update_available(self, version: str, url: str) -> None:
        if not self._auto_update.offer(version, url):
            return  # 同版本只提示一次
        self._pending_update = (version, url)
        if self._update_action:
            self._update_action.setText(t("tray_update_to", self.lang, ver=version))
            self._update_action.setVisible(True)
        activity("log_update_found", lang=self.lang, ver=version)
        if not self._holding_message():
            self._message_action = None   # 这条通知点了不做事（别沿用上一条 Tomo 通知的动作）
            self.tray.showMessage(
                t("update_avail_title", self.lang),
                t("update_avail_body", self.lang, ver=version),
                pearnly_icon(),
            )
        if not self._auto_timer.isActive():
            self._auto_timer.start(15000)
        QTimer.singleShot(0, self._auto_update_tick)

    def _on_batch_done(self, profile_id: str, n_ok: int, n_fail: int, inv: str) -> None:
        """每批结束都弹原生通知(诚实三态),不再只在全成功时弹:
        全成功→「已录入,可查」· 部分失败→「N 成功 M 失败」· 全失败→「未成功,看日志」。"""
        try:
            if n_fail == 0:
                title = t("notif_recorded_title", self.lang)
                body = (
                    t("notif_recorded_one", self.lang, inv=inv)
                    if (n_ok == 1 and inv)
                    else t("notif_recorded_many", self.lang, n=n_ok)
                )
            elif n_ok > 0:
                title = t("notif_partial_title", self.lang)
                body = t("notif_partial_body", self.lang, ok=n_ok, fail=n_fail)
            else:
                title = t("notif_failed_title", self.lang)
                body = t("notif_failed_body", self.lang, fail=n_fail)
            if len([cfg for cfg in self.configs if not cfg.paused]) > 1:
                title = f"{self._profile_label(profile_id)} · {title}"
            self._message_action = None   # 这条通知点了不做事（别沿用上一条 Tomo 通知的动作）
            self.tray.showMessage(title, body, pearnly_icon())
        except Exception:
            log.exception("批次完成通知失败")

    def _on_busy_prompt(self, prompt) -> None:
        """工作线程探测到账套被占用 → 主线程弹原生弹窗(重试/终止);写回选择放行工作线程。

        弹窗为应用模态(挡住误操作),字体随系统;文案语言跟随小助手所选语言。
        """
        lang = getattr(prompt, "lang", "") or self.lang
        box = QMessageBox()
        box.setWindowIcon(pearnly_icon())
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(t("busy_title", lang))
        box.setText(t("busy_body", lang))
        retry_btn = box.addButton(t("busy_retry", lang), QMessageBox.AcceptRole)
        box.addButton(t("busy_abort", lang), QMessageBox.RejectRole)
        box.setDefaultButton(retry_btn)
        box.exec()
        prompt.choice = "retry" if box.clickedButton() is retry_btn else "abort"
        prompt.done.set()

    def _check_for_update(self, manual: bool = False) -> None:
        """后台查一次云端有没有新版；有就交给自动更新（下载 → 等空闲 → 安装）。同一时间只查一个。"""
        if self._quitting or self._updating or self._checking is not None:
            return
        from .updater import DOWNLOAD_SITE, check_update

        worker = ProbeThread(lambda: check_update(DOWNLOAD_SITE))
        self._checking = worker
        result: list = []
        worker.ok.connect(result.append)

        def finished() -> None:
            # 线程真正结束后才松引用（结果信号发出时线程还没退出，那时丢掉最后一个引用会崩）。
            self._checking = None
            update = result[0] if result else None
            if update:
                self._checked_latest = False
                self._on_update_available(str(update["version"]), str(update["url"]))
            elif manual and result:
                self._checked_latest = True

        worker.finished.connect(finished)
        worker.start()

    def update_status(self) -> tuple[str, str]:
        """主窗口底部显示的更新状态（真实流程状态，不估算）。"""
        if self._checking is not None and not self._auto_update.pending:
            return "checking", ""
        state, version = self._auto_update.state()
        if state == "none":
            return ("latest" if self._checked_latest else "none"), ""
        return state, version

    def _update_now(self) -> None:
        """主窗口的更新按钮：有新版就马上推进（仍等手上的录入做完才安装）；没有就立即检查一次。"""
        if self._auto_update.pending:
            self._do_update()
        else:
            self._check_for_update(manual=True)

    def _do_update(self) -> None:
        """手动点「更新」：和自动更新走同一条路（下载 → 等空闲 → 停稳 → 安装），不在写账套中途换版本。"""
        self._update_requested = True
        self._auto_update.retry_now()
        if self._update_action:
            self._update_action.setEnabled(False)
        self._message_action = None   # 这条通知点了不做事（别沿用上一条 Tomo 通知的动作）
        self.tray.showMessage(
            t("update_avail_title", self.lang),
            t("update_downloading", self.lang),
            pearnly_icon(),
        )
        self._auto_update_tick()

    def _auto_update_tick(self) -> None:
        if self._quitting:
            return
        state = self._auto_update.tick()
        if state == "stopping":
            self._auto_timer.setInterval(2000)  # 等两边停稳，勤看一点
        elif state == "failed":
            self._auto_timer.setInterval(15000)
            self._on_update_failed()

    def _busy(self) -> bool:
        """有连接正在推送、等账套锁或扫账套：不换版本。小助手的窗口开着（有人正在用）、文件夹线程正在逐份上传
        （一次运行或补传）也先不自动装，等窗口关了、这一轮跑完再装；有人点了「立即更新」就不等这两样（2026-10-08：
        自动更新别打断正在用的人；运行被叫停也只停在两份文件之间，下次启动同一时刻接着跑）。"""
        if any(state[0] in ("working", "waiting_lock", "indexing")
               for state in self._profile_states.values()):
            return True
        if getattr(self, "_update_requested", False):
            return False
        folders = getattr(self, "_folders", None)
        return bool(folders is not None and folders.busy()) or _window_open()

    def _start_download(self, version: str, url: str) -> None:
        from .updater import DOWNLOAD_SITE

        activity("log_updating", lang=self.lang, ver=version)
        # 下载放后台线程 → UI 不卡死(61MB 主线程同步下载是「点了就卡」的根因)。
        self._update_worker = _UpdateWorker(DOWNLOAD_SITE, url)
        self._update_worker.done.connect(self._on_update_downloaded)
        self._update_worker.failed.connect(self._on_update_download_failed)
        self._update_worker.start()

    def _on_update_downloaded(self, path: str) -> None:
        self._update_worker = None
        self._auto_update.downloaded(path)
        self._auto_update_tick()

    def _on_update_download_failed(self) -> None:
        self._update_worker = None
        self._auto_update.download_failed()
        self._on_update_failed()

    def _stop_for_update(self) -> None:
        """空闲了：让取任务线程与文件夹线程做完手上这一步就停，停稳再装（不再自愈重启）。"""
        self._updating = True
        if getattr(self, "_folders", None) is not None:
            self._folders.stop()
        worker = self.worker
        if worker is not None and worker.isRunning():
            worker.stop(signal_offline=True)

    def _work_stopped(self) -> bool:
        worker = self.worker
        folders = getattr(self, "_folders", None)
        return (worker is None or not worker.isRunning()) and not (folders is not None and folders.running())

    def _resume_after_update(self) -> None:
        self._updating = False
        if self.worker is None:
            self.worker = self._spawn_worker()
        if getattr(self, "_folders", None) is not None:
            self._folders.start()

    def _install_update(self, path: str):
        from .updater import run_installer_silent

        return run_installer_silent(path)  # 安装器关本进程→装新→自启(本进程随之退出)

    def _on_update_failed(self) -> None:
        if self._update_action:
            self._update_action.setEnabled(True)  # 放回,允许重试
        self._message_action = None   # 这条通知点了不做事（别沿用上一条 Tomo 通知的动作）
        self.tray.showMessage(
            t("update_avail_title", self.lang),
            t("update_failed", self.lang),
            pearnly_icon(),
        )

    def _profile_label(self, profile_id: str) -> str:
        for cfg in self.configs:
            if cfg.profile_id == profile_id:
                # 连接人的 Pearnly 账号；不显示 Express 公司 / 账套（账套由网页第一步选择）。
                return str(getattr(cfg, "pearnly_account", "") or profile_id)
        return profile_id

    def _aggregate_status(self) -> tuple[str, str, object]:
        active_ids = [cfg.profile_id for cfg in self.configs if not cfg.paused]
        states = {
            profile_id: self._profile_states.get(profile_id, ("offline", ""))
            for profile_id in active_ids
        }
        if not states:
            return "offline", "", ""
        for key in ("error", "waiting_lock", "working", "indexing", "idle"):
            for profile_id, state in states.items():
                if state[0] == key:
                    return key, profile_id, state[1]
        profile_id, state = next(iter(states.items()))
        return "offline", profile_id, state[1]

    def _on_status(self, profile_id: str, key: str, detail: object) -> None:
        self._profile_states[profile_id] = (key, detail)
        self._render_status()
        # 活动记录:仅状态切换时记(断线恢复 / 断开),避免每拍刷屏。首连不记「已连上」
        # (on_connect 是乐观信号未验证·靠启动行「已启动」表达·密钥坏时只记一条「断开」更诚实)。
        prev = self._last_keys.get(profile_id)
        if key == "idle" and prev == "offline":
            activity("log_connected", lang=self.lang)
        elif key == "offline" and prev not in (None, "offline"):
            activity(
                "log_offline",
                lang=self.lang,
                reason=self._detail_text(detail) or t("off_network", self.lang),
            )
        self._last_keys[profile_id] = key

    def _on_tray_activated(self, reason) -> None:
        """左键单击 / 双击托盘图标 = 打开 Tomo（右键仍是菜单）。"""
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_tomo()

    # ── Tomo 桌面窗口 ──
    def _tomo_window(self):
        if self._tomo is None:
            from .gui_tomo import TomoWindow

            self._tomo = TomoWindow(lang=self.lang, icon=pearnly_icon(), notify=self._notify, bridge=self._make_bridge())
        return self._tomo

    def open_tomo(self, text: str | None = None) -> None:
        window = self._tomo_window()
        if text is None:
            window.show_front()
        else:
            window.ask(text)

    def open_conversation(self, site: str, session: str = "") -> None:
        """对话提醒点开：打开那段对话（没有对话 = 定时任务页）；站点不是自家 https 地址就只打开窗口。"""
        from . import tomo_links

        url = tomo_links.conversation_url(site, session)
        if not url:
            self.open_tomo()
            return
        self._tomo_window().open_conversation(url, session)

    def _on_folder_notices(self, site: str, items) -> None:
        """文件夹同步带来的对话提醒（folder_worker._notices 已校验形状），同一条只弹一次。一批只弹一条（托盘点击只记得住
        最后一条的动作，连弹几条会点错）：只有一条就点开那段对话；不止一条合成一条——标题带条数与最新一条的标题，
        点开定时任务页。"""
        fresh = []
        for item in items or []:
            try:
                if self._first_time(NOTICE_TAG + item["id"]):
                    fresh.append(item)
            except Exception:
                log.exception("对话提醒通知失败")
        if not fresh:
            return
        try:
            newest = max(fresh, key=lambda item: item.get("created") or 0)
            title = newest["title"] or "CC"
            if len(fresh) == 1:
                self._show(title, newest["text"], lambda s=site, c=newest["session"]: self.open_conversation(s, c))
            else:
                self._show(f"({len(fresh)}) {title}", newest["text"], lambda s=site: self.open_conversation(s, ""))
        except Exception:
            log.exception("对话提醒通知失败")

    def _notify(self, title: str, body: str, on_click=None, tag: str = "") -> None:
        # 对话提醒按编号只弹一次（页面实时收到、文件夹同步又带来）
        if str(tag or "").startswith(NOTICE_TAG) and not self._first_time(tag):
            return
        self._show(title, body, on_click)

    def _first_time(self, tag: str) -> bool:
        """这条对话提醒还没弹过（记下）。测试里 TrayApp 不走 __init__，去重记忆这里现建。"""
        seen = getattr(self, "_seen_notices", None)
        if seen is None:
            seen = self._seen_notices = OrderedDict()
        if tag in seen:
            return False
        seen[tag] = True
        while len(seen) > NOTICE_MEMORY:
            seen.popitem(last=False)
        return True

    def _show(self, title: str, body: str, on_click=None) -> None:
        self._message_action = on_click
        self._message_at = time.monotonic()
        self.tray.showMessage(title, body, pearnly_icon(), 8000)

    def _holding_message(self) -> bool:
        """刚弹出（NOTICE_HOLD_SECONDS 内）、还没点的带点击动作的通知。"""
        return (getattr(self, "_message_action", None) is not None
                and time.monotonic() - getattr(self, "_message_at", 0.0) < NOTICE_HOLD_SECONDS)

    def _on_message_clicked(self) -> None:
        action, self._message_action = self._message_action, None
        if action is not None:
            action()

    def _repair(self) -> None:
        """「连接与设置」：打开 Tomo 并打开对话页设置的「连接」一栏（2026-10-10 起取代原小助手主窗口）。"""
        self.open_tomo()
        self._tomo.open_settings("connections")

    def _make_bridge(self):
        from . import tomo_links
        from .desktop_settings import DesktopSettings
        from .tomo_bridge import TomoBridge

        settings = DesktopSettings(
            states=lambda: dict(self._profile_states), update_status=self.update_status, on_update=self._update_now,
            on_profiles_changed=self._reload_profiles, on_folders_changed=self._start_folders,
            on_always_on=self._keep_awake.set,
            hotkey=lambda: bool(getattr(self._hotkey, "registered", False)))
        self._bridge = TomoBridge(
            settings,
            allowed=lambda: self._tomo is not None and tomo_links.is_chat(self._tomo.page.url().toString()),
            window=lambda: self._tomo, set_lang=self._set_lang)
        return self._bridge

    def _set_lang(self, lang: str) -> None:
        """对话页的语言（中文 / 泰文）同步给托盘与通知文案。"""
        from .i18n import persist_ui_lang
        from .profile_store import ProfileStore

        if normalize(lang) == self.lang:
            return
        try:
            persist_ui_lang(ProfileStore.load(), lang)
        except Exception:
            log.exception("保存界面语言失败")
        self._apply_language(lang)

    def _reload_profiles(self) -> None:
        """Reload persisted Profiles, then cooperatively replace the single worker."""
        from .profile_store import ProfileStore

        store = ProfileStore.load()
        self.configs = store.runtime_configs(include_paused=True)
        self.cfg = self.configs[0] if self.configs else store.new_runtime_config()
        self.lang = normalize(
            store.global_settings.ui_lang
            or getattr(self.cfg, "ui_lang", "")
            or default_lang()
        )
        self._profile_states.clear()
        self._last_keys.clear()
        self._retranslate()
        self._restart_worker()
        if getattr(self, "_folders", None) is not None:
            self._folders.start()

    def _start_folders(self) -> None:
        """主窗口里添加了文件夹：文件夹自动录入线程没在跑就起来（已在跑不重复）。"""
        if getattr(self, "_folders", None) is not None:
            self._folders.start()

    def _restart_worker(self) -> None:
        """协作式停旧 worker；finished 后才用最新 configs 启动唯一新 worker。"""
        if self._quitting or getattr(self, "_updating", False):
            return
        self._restart_pending = True
        old = self.worker
        if old is not None and old.isRunning():
            old.stop(signal_offline=False)
            return
        self.worker = None
        self._restart_pending = False
        self.worker = self._spawn_worker()

    def _on_worker_finished(self, worker) -> None:
        if worker is not self.worker:
            return
        self.worker = None
        if self._quitting:
            self.tray.hide()
            self.app.quit()
            return
        if getattr(self, "_updating", False):
            return  # 为更新停下：等安装器接手，不再起新的取任务线程
        if self._restart_pending:
            self._restart_pending = False
            self.worker = self._spawn_worker()

    def _watchdog_check(self) -> None:
        """主线程定时巡检:worker 卡死超阈值 → 退避范围内自愈重启,超限则停手改报错。"""
        w = self.worker
        if w is None or not w.isRunning() or w._stop:
            return
        heartbeat = max(
            [cfg.heartbeat_interval_s for cfg in self.configs if not cfg.paused] or [60]
        )
        limit = watchdog.stall_limit_seconds(heartbeat)
        stalled = w.stall_seconds()
        if not watchdog.is_stalled(stalled, limit):
            return
        if not self._heal.allowed():
            log.error("worker 反复卡死且自愈已达上限,停手改报错(请检查账套路径/网络)")
            profile_id = next(
                (cfg.profile_id for cfg in self.configs if not cfg.paused), "companion"
            )
            self._on_status(profile_id, "error", t("st_stalled_giveup", self.lang))
            return
        self._heal.record()
        log.error("侦测到 poll loop 卡死 %.0fs → 自愈重启 worker", stalled)
        activity("log_self_healed", lang=self.lang)
        self._restart_worker()

    def _quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._restart_pending = False
        activity("log_quit", lang=self.lang)
        self._watchdog.stop()
        if getattr(self, "_hotkey", None) is not None:
            self._hotkey.release()
        if getattr(self, "_bridge", None) is not None:
            self._bridge.shutdown()
        if getattr(self, "_tomo", None) is not None:
            self._tomo.shutdown()
        if getattr(self, "_auto_timer", None) is not None:
            self._auto_timer.stop()
        if getattr(self, "_folders", None) is not None:
            self._folders.stop()
        worker = self.worker
        if worker is not None and worker.isRunning():
            worker.stop(signal_offline=True)
            return
        self.worker = None
        self.tray.hide()
        self.app.quit()

    def run(self) -> int:
        return self.app.exec()
