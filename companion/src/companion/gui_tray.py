# -*- coding: utf-8 -*-
"""系统托盘常驻(PySide6)· 后台线程跑 DBF 直写 poll loop,托盘显示连接/活动状态。

2026-10-10 起小助手并入 Tomo 桌面端：托盘左键、Ctrl+Alt+Space、再次打开程序都是打开 Tomo 窗口（gui_tomo.py）；
Express / MR.ERP 的连接与设置留在「连接与设置」主窗口，取任务、直写、文件夹录入照旧在后台跑，没配对也能单用 Tomo。

2026-10-18 轻量化（用户：「CC 桌面版必须要轻量设计并满足老机器能正常运行使用」）：本进程**不加载网页内核**。CC 窗口在
另一个进程里（``companion.exe --window``，window_main.py），要打开时由这里拉起、关了它就退出（window_link.WindowHost）；
对话页的桥仍在这里应答（tomo_bridge.py），窗口进程转过来。

主线程 = Qt 事件循环 + 托盘图标(绿空闲/黄工作/红错误/灰离线)+ 右键菜单(状态/打开小助手/
退出;左键单击也打开小助手主窗口);后台 QThread = QueueClient poll → _handle_dbf_task 直写 → 周期心跳 +
夜间 PACK tick(复用 main 的核心,不重复直写逻辑)。worker 出状态/离线通过信号回主线程。
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QMessageBox, QSystemTrayIcon

from . import toast, watchdog
from .gui_icon import asset_path, pearnly_icon, pearnly_pixmap as _pearnly_pixmap  # noqa: F401  （asset_path 旧入口）
from .config import CompanionConfig, member_active
from .auto_update import AutoUpdate
from .update_block import UpdateMemory, state_file
from .folder_worker import FolderIntake
from .request_worker import RequestWorker
from .gui_font import qt_app
from .gui_pairing_support import ProbeThread
from .gui_tray_workers import (
    PollWorker,
    UpdateWorker as _UpdateWorker,
)
from .i18n import MessageSpec, default_lang, normalize, t
from .logger import activity, get_logger


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



WINDOW_HOST = None   # 本进程的 CC 窗口管家（window_link.WindowHost，TrayApp 建）：_window_open 看窗口进程报来的状态


def _window_open() -> bool:
    """有人正在用 Tomo 窗口（开着且在最前面）：自动更新先不装。窗口在另一个进程里，看它报来的状态（window_link）。"""
    host = WINDOW_HOST
    return bool(host is not None and host.connected and host.visible and host.active)


MENU_TEXT_MAX = 48
# 弹过的对话提醒记多少条（按 notice:<编号> 去重：页面实时收到与文件夹同步带来的是同一条）
NOTICE_MEMORY = 500
NOTICE_TAG = "notice:"
# 刚弹出、还没点的带点击动作的通知（对话提醒等）这么久之内，「有新版本」不另弹通知——托盘点击只记得住最后一条的动作，
# 新版提示会把它冲掉；托盘菜单里照样有「更新到最新版」。
NOTICE_HOLD_SECONDS = 120


def _store_updates() -> bool:
    """以 MSIX 包身份运行（Microsoft Store 装的，package_identity.py）：更新交给商店。"""
    from . import package_identity

    return package_identity.packaged()


UPDATE_TAG = "update"  # 有新版 / 正在下载 / 更新失败：同一个 tag，通知中心里只留最新一条


def _menu_text(text: str) -> str:
    """托盘菜单状态行：过长截断（完整内容在悬停提示里），不把菜单撑宽。"""
    value = " ".join(str(text or "").split())
    return value if len(value) <= MENU_TEXT_MAX else value[: MENU_TEXT_MAX - 1] + "…"


class _ClickRelay(QObject):
    """点 Windows 应用通知的回调在系统线程上：那边只发信号，这里排队回界面线程再执行点击动作。"""

    clicked = Signal(object)

    def __init__(self, run) -> None:
        super().__init__()
        self._run = run
        self.clicked.connect(self._deliver, Qt.QueuedConnection)

    def _deliver(self, action) -> None:
        self._run(action)


class TrayApp(QObject):
    worker_stopped = Signal(object)
    folder_status = Signal(str, str)  # MR.ERP 的小助手成员（只读文件夹）：文件夹线程每次同步后报 idle / offline / auth（401）
    folder_notices = Signal(str, object)  # 文件夹同步带来的对话提醒：(站点, [提醒…])
    folder_update = Signal(str, str)  # 文件夹同步带来的新版本（只读文件夹的成员没有心跳）：(版本, 地址)
    folder_roots = Signal()  # 文件夹线程自动收回了经公司添加、没人用的根目录：设置页重取本机状态
    device_result = Signal(str, str)  # 认电脑说这把密钥不能再用（成员, superseded / claimed_elsewhere，device_link）

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
        # 安装结果记在本机（update_block.py）：被 Windows 拦下的版本重开 CC 也按退避再试，心跳如实上报。
        self._auto_update = AutoUpdate(
            busy=self._busy, stop_work=self._stop_for_update, work_stopped=self._work_stopped,
            resume_work=self._resume_after_update, start_download=self._start_download,
            install=self._install_update, memory=UpdateMemory(state_file()), exists=os.path.isfile)
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
        self._render_update_action()   # 上次被 Windows 拦下、还没装上：一开始菜单里就有「下载新版」
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        activity("log_started", lang=self.lang)
        # 从 Program Files 迁到用户目录安装后，开机自启要指向新位置（只改已经开着的，不替人打开）。
        try:
            from . import autostart

            if autostart.repoint():
                log.info("开机自启已改指向当前安装位置")
        except Exception:
            log.exception("核对开机自启位置失败（忽略）")
        self._heal = watchdog.HealBackoff()  # 自愈退避:防目标永久不可达时无限重启空转
        # 多台电脑（ADR-021）：这台电脑的设备密钥第一次运行就生成（DPAPI 存在本机配置里），各成员启动后各认一次电脑；
        # 服务端说哪把密钥不能再用，回到界面线程改本机成员、重启各线程（_on_device_result）。
        from . import device_link

        self._announcer = device_link.ANNOUNCER
        self.device_result.connect(self._on_device_result)
        self._announcer.on_result = lambda cfg, status: self.device_result.emit(
            str(getattr(cfg, "profile_id", "") or ""), status)
        device_link.device_key()
        self.worker = self._spawn_worker()
        # CC 开着时保持电脑唤醒（keep_awake.py，同 Claude 桌面版，用户 10-16）：有成员连着就不让系统空闲睡眠；不锁屏
        from .keep_awake import KeepAwake

        self._keep_awake = KeepAwake()
        # 两个开关是这台电脑的全局设置：托盘单独记着（启动 / 重读配置时取文件里的值，设置里改了由 _set_keep_awake 更新），
        # 不写进各成员的配置——取任务线程手上的配置副本存盘时只写它自己改过的全局项（profile_store.save_profile）
        self._awake_settings = (bool(getattr(self.cfg, "keep_awake", True)),
                                bool(getattr(self.cfg, "keep_awake_on_battery", False)))
        self._apply_keep_awake()
        # 文件夹自动录入：独立守护线程，只在本机给某条连接设了允许读取的根目录时才启动（folder_worker.py）。
        self.folder_status.connect(self._on_folder_status)
        self.folder_notices.connect(self._on_folder_notices)
        self.folder_update.connect(self._on_update_available)
        self.folder_roots.connect(self._on_folder_roots)
        self._folders = FolderIntake(lambda: self.configs, lang=lambda: self.lang,
                                     on_status=lambda profile_id, key: self.folder_status.emit(profile_id, key),
                                     on_notices=lambda site, items: self.folder_notices.emit(site, items),
                                     on_update=lambda version, url: self.folder_update.emit(version, url),
                                     on_roots_changed=self.folder_roots.emit,
                                     announce=self._announcer.announce)
        self._folders.start()
        # 按需请求（CC 对话里读账套、列 / 取已授权文件夹的文件）：又一条独立守护线程，与推送线、看门狗无关（request_worker.py）。
        self._requests = RequestWorker(lambda: self.configs)
        self._requests.start()
        # CC 窗口在另一个进程里，要打开时才拉起（本进程不加载网页内核，window_link.py）；桥在这里照旧应答（第一次用到才建）。
        # 快捷输入条、全局快捷键、「再打开一次」的管道一开始就在。
        global WINDOW_HOST
        from .window_link import WindowHost

        self._bridge = None
        self._window = WINDOW_HOST = WindowHost(on_request=self._on_window_request, on_notify=self._on_window_notify,
                                                lang=lambda: self.lang)
        self._message_action = None   # 点托盘通知时做什么（Tomo 的回复 / 下载通知 / 对话提醒才有）
        self._message_at = 0.0        # 那条带动作的通知什么时候弹的（单调时钟）
        self.tray.messageClicked.connect(self._on_message_clicked)
        self._toast_relay = _ClickRelay(self._on_toast_clicked)   # 在界面线程建：点击排队回这里
        from .gui_quick import QuickAsk
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
        announcer = getattr(self, "_announcer", None)
        w = PollWorker([cfg for cfg in self.configs if getattr(cfg, "kind", "express") != "folder"],
                       announce=announcer.announce if announcer is not None else None)
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
        # 有新版才显示的「更新到最新版」项(默认隐藏)；被 Windows 拦下时是「下载新版」（_render_update_action）。
        self._update_action = menu.addAction("")
        self._update_action.setVisible(False)
        self._update_action.triggered.connect(self._on_update_action)
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
        window = getattr(self, "_window", None)
        if window is not None:
            window.send("lang", lang=self.lang)

    def _retranslate(self) -> None:
        if getattr(self, "_tomo_action", None):
            self._tomo_action.setText(t("tomo_open", self.lang))
        if self._open_action:
            self._open_action.setText(t("tray_open", self.lang))
        if self._quit_action:
            self._quit_action.setText(t("tray_quit", self.lang))
        self._render_update_action()
        self._render_status()

    def _detail_text(self, detail: object) -> str:
        if isinstance(detail, MessageSpec):
            return detail.render(self.lang)
        return str(detail or "")

    def _render_status(self) -> None:
        if not self._profile_states:
            text = t("tray_starting" if self.configs else "tray_no_connection", self.lang)
            self._show_status("offline", f"{t('tray_tooltip', self.lang)} · {text}", text)
            return
        overall, display_profile, display_detail = self._aggregate_status()
        label = t("st_" + overall, self.lang) if overall in _LABELED_STATES else overall
        detail = self._detail_text(display_detail)
        text = f"{label} · {detail}" if detail else label
        if len([cfg for cfg in self.configs if not cfg.paused]) > 1 and display_profile:
            text = f"{self._profile_label(display_profile)} · {text}"
        self._show_status(overall, f"{t('tray_tooltip', self.lang)} · {text}", _menu_text(text))

    def _show_status(self, icon: str, tooltip: str, menu: str) -> None:
        """只在变了时才重画托盘图标 / 悬停提示 / 菜单状态行（取任务线程每 3 秒报一次「空闲」，2026-10-18 轻量化）。"""
        shown = getattr(self, "_shown_status", (None, None, None))
        if shown[0] != icon:
            self.tray.setIcon(_status_icon(icon))
        if shown[1] != tooltip:
            self.tray.setToolTip(tooltip)
        if shown[2] != menu and self._status_action:
            self._status_action.setText(menu)
        self._shown_status = (icon, tooltip, menu)

    def _on_update_available(self, version: str, url: str) -> None:
        if _store_updates():
            return  # Microsoft Store 版由商店更新，不下载、不启动自己的安装包
        if not self._auto_update.offer(version, url):
            return  # 同版本只提示一次
        self._pending_update = (version, url)
        self._render_update_action()
        activity("log_update_found", lang=self.lang, ver=version)
        if not self._holding_message():
            self._message_action = None   # 这条通知点了不做事（别沿用上一条 Tomo 通知的动作）
            self._pop(t("update_avail_title", self.lang), t("update_avail_body", self.lang, ver=version),
                      tag=UPDATE_TAG)
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
            self._pop(title, body)
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
        if self._quitting or self._updating or self._checking is not None or _store_updates():
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
        self._pop(t("update_avail_title", self.lang), t("update_downloading", self.lang), tag=UPDATE_TAG)
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
        elif state == "blocked":
            self._auto_timer.setInterval(15000)
            self._on_update_blocked()

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
        requests = getattr(self, "_requests", None)
        return (bool(folders is not None and folders.busy()) or bool(requests is not None and requests.busy())
                or _window_open())

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
        if getattr(self, "_requests", None) is not None:
            self._requests.stop()
        worker = self.worker
        if worker is not None and worker.isRunning():
            worker.stop(signal_offline=True)

    def _work_stopped(self) -> bool:
        worker = self.worker
        folders = getattr(self, "_folders", None)
        requests = getattr(self, "_requests", None)
        return ((worker is None or not worker.isRunning()) and not (folders is not None and folders.running())
                and not (requests is not None and requests.running()))

    def _resume_after_update(self) -> None:
        self._updating = False
        if self.worker is None:
            self.worker = self._spawn_worker()
        if getattr(self, "_folders", None) is not None:
            self._folders.start()
        if getattr(self, "_requests", None) is not None:
            self._requests.start()

    def _install_update(self, path: str):
        from .updater import run_installer_silent

        return run_installer_silent(path)  # 安装器关本进程→装新→自启(本进程随之退出)

    def _on_update_failed(self) -> None:
        if self._update_action:
            self._update_action.setEnabled(True)  # 放回,允许重试
        self._message_action = None   # 这条通知点了不做事（别沿用上一条 Tomo 通知的动作）
        self._pop(t("update_avail_title", self.lang), t("update_failed", self.lang), tag=UPDATE_TAG)

    def _on_update_blocked(self) -> None:
        """安装包被 Windows 拦下（auto_update / update_block）：菜单换成「下载新版」；同一版本只弹一次通知，点了打开官网下载。
        不绕开系统的拦截，只给出路（教程问题第 1 条）。刚弹的对话提醒还没点时先不弹（同「有新版本」，托盘气泡只记得住
        最后一条的动作），下次再被拦时补弹；菜单里照样有「下载新版」。"""
        version = self._auto_update.blocked()
        if self._update_action:
            self._update_action.setEnabled(True)
        self._render_update_action()
        if version:
            activity("log_update_blocked", lang=self.lang, ver=version)
        version = "" if self._holding_message() else self._auto_update.take_blocked_notice()
        if version:
            self._show(t("update_blocked", self.lang, ver=version), t("upd_download", self.lang), self._open_download,
                       tag=UPDATE_TAG)

    def _open_download(self) -> None:
        """「下载新版」：在系统浏览器打开官网的安装包下载（与官网、网页「下载 CC 桌面版」同一个地址）。"""
        from .updater import DOWNLOAD_PAGE

        QDesktopServices.openUrl(QUrl(DOWNLOAD_PAGE))

    def _on_update_action(self) -> None:
        if self._auto_update.state()[0] == "blocked":
            self._open_download()
        else:
            self._do_update()

    def _render_update_action(self) -> None:
        """托盘菜单的更新项：被 Windows 拦下 →「下载新版 vX」；有新版 →「更新到最新版 vX」；都没有就不显示。"""
        action = getattr(self, "_update_action", None)
        if not action:
            return
        state, version = self._auto_update.state()
        pending = getattr(self, "_pending_update", None)
        if state == "blocked":
            action.setText(t("tray_download_to", self.lang, ver=version))
        elif pending:
            action.setText(t("tray_update_to", self.lang, ver=pending[0]))
        action.setVisible(state == "blocked" or bool(pending))

    def _profile_label(self, profile_id: str) -> str:
        for cfg in self.configs:
            if cfg.profile_id == profile_id:
                # 连接人的 Pearnly 账号；不显示 Express 公司 / 账套（账套由网页第一步选择）。
                return str(getattr(cfg, "pearnly_account", "") or profile_id)
        return profile_id

    def _aggregate_status(self) -> tuple[str, str, object]:
        active_ids = [cfg.profile_id for cfg in self.configs if member_active(cfg)]
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

    def _on_folder_status(self, profile_id: str, key: str) -> None:
        """MR.ERP 的小助手成员（只读文件夹）的状态：``auth`` = 这把密钥服务端不认（401），同 Express 成员心跳回 401
        显示成离线 +「连接已失效，请在设置中重新连接」（gui_tray_workers.friendly_poll_error_spec）。"""
        if key == "auth":
            self._on_status(profile_id, "offline", MessageSpec("off_auth"))
        else:
            self._on_status(profile_id, key, "")

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

    # ── Tomo 桌面窗口（在窗口进程里，window_link / window_main） ──
    def open_tomo(self, text: str | None = None) -> None:
        self._window.open({"op": "show"} if text is None else {"op": "ask", "text": text})

    def open_conversation(self, site: str, session: str = "", view: str = "") -> None:
        """对话提醒点开：打开那段对话；没有对话按 ``view`` 开那一页（``company:<编号>`` = 这家公司的主档页，1.9.1 起），
        其余定时任务页；站点不是自家 https 地址就只打开窗口。"""
        from . import tomo_links

        url = tomo_links.conversation_url(site, session, view)
        if not url:
            self.open_tomo()
            return
        self._window.open({"op": "open_conversation", "url": url, "session": tomo_links.open_target(session, view)})

    def _on_folder_notices(self, site: str, items) -> None:
        """文件夹同步带来的对话提醒（folder_worker._notices 已校验形状），同一条只弹一次。一批只弹一条（托盘点击只记得住
        最后一条的动作，连弹几条会点错）：只有一条就点开那段对话（没有对话按它的 ``view``：属于某家公司的开那家公司的主档页）；
        不止一条合成一条——标题带条数与最新一条的标题，点开定时任务页。"""
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
                self._show(title, newest["text"],
                           lambda s=site, c=newest["session"], v=newest.get("view") or "": self.open_conversation(s, c, v),
                           tag=NOTICE_TAG + newest["id"])
            else:
                self._show(f"({len(fresh)}) {title}", newest["text"], lambda s=site: self.open_conversation(s, ""))
        except Exception:
            log.exception("对话提醒通知失败")

    def _notify(self, title: str, body: str, on_click=None, tag: str = "") -> None:
        # 对话提醒按编号只弹一次（页面实时收到、文件夹同步又带来）
        if str(tag or "").startswith(NOTICE_TAG) and not self._first_time(tag):
            return
        self._show(title, body, on_click, tag=tag)

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

    def _show(self, title: str, body: str, on_click=None, tag: str = "") -> None:
        self._message_action = on_click
        self._message_at = time.monotonic()
        self._pop(title, body, on_click=on_click, tag=tag, msecs=8000)

    def _pop(self, title: str, body: str, *, on_click=None, tag: str = "", msecs: int = 10000) -> None:
        """弹桌面通知：先发 Windows 应用通知（锁屏时也留在通知中心，2026-10-13 测试机实测托盘气泡不留），
        发不出来再用托盘气泡。应用通知各带各的点击动作：系统线程上只发信号，排队回界面线程执行（_ClickRelay）。"""
        hook = None
        if on_click is not None:
            relay = getattr(self, "_toast_relay", None)
            if relay is None:   # 测试里 TrayApp 不走 __init__：在这里（界面线程）现建
                relay = self._toast_relay = _ClickRelay(self._on_toast_clicked)
            hook = lambda emit=relay.clicked.emit, action=on_click: emit(action)  # noqa: E731
        if toast.show(title, body, tag, hook):
            return
        self.tray.showMessage(title, body, pearnly_icon(), msecs)

    def _on_toast_clicked(self, action) -> None:
        """点了 Windows 应用通知（已回到界面线程）：和点托盘气泡一样执行它的动作。"""
        if getattr(self, "_message_action", None) is action:
            self._message_action = None   # 点过了：「有新版本」不再为它压着
        try:
            action()
        except Exception:
            log.exception("通知点击动作失败")

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
        self._window.open({"op": "open_settings", "section": "connections"})

    # ── 窗口进程转来的 ──
    def _on_window_request(self, message: dict):
        """对话页经窗口进程调桥（state / call）：在这里照旧应答，返回桥的 JSON 字符串。"""
        bridge = self._bridge or self._make_bridge()
        if message.get("op") == "state":
            return bridge.state()
        return bridge.call(str(message.get("method") or ""), str(message.get("args") or ""))

    def _on_window_notify(self, message: dict) -> None:
        """窗口里要弹的系统通知：在这里弹（按 notice:<编号> 去重同前）；点了「在文件夹中显示」这里自己做，其余转回窗口。"""
        path = str(message.get("reveal") or "")
        click = str(message.get("click") or "")
        if path:
            from .gui_quick import Reveal

            action = Reveal(Path(path))
        elif click:
            action = lambda click=click: self._window_click(click)  # noqa: E731
        else:
            action = None
        self._notify(str(message.get("title") or "CC"), str(message.get("body") or ""), action,
                     tag=str(message.get("tag") or ""))

    def _window_click(self, click: str) -> None:
        """点了窗口转来的通知：窗口进程还在就交回去执行（点开那段对话等）；已经退了就打开窗口。"""
        if not self._window.send("click", id=click):
            self.open_tomo()

    def _make_bridge(self):
        from . import tomo_links
        from .desktop_settings import DesktopSettings
        from .tomo_bridge import TomoBridge

        from . import device_link

        settings = DesktopSettings(
            device=device_link.identity,
            states=lambda: dict(self._profile_states), update_status=self.update_status, on_update=self._update_now,
            on_download=self._open_download,
            on_profiles_changed=self._reload_profiles, on_folders_changed=self._start_folders,
            on_keep_awake=self._set_keep_awake,
            hotkey=lambda: bool(getattr(self._hotkey, "registered", False)),
            # 公司的文件夹（pick_folder）：新加了根目录马上快速同步一次、唤醒文件夹线程；设置页的旧授权清理行看最近一次
            # 服务端回的在用根目录；上限按服务端回的 max_roots（没收到过按 20）
            report_roots=self._folders.report_roots_now, wake_folders=self._folders.wake,
            roots_in_use=self._folders.roots_in_use, max_roots=self._folders.max_roots)
        host = self._window
        self._bridge = TomoBridge(
            settings,
            allowed=lambda: host.connected and tomo_links.is_chat(self._page_url()),
            window=lambda: None, set_lang=self._set_lang, page_url=self._page_url, pick=host.pick)
        # 桥的两个信号交给窗口进程里的页面（窗口没开就不发）
        self._bridge.changed.connect(lambda: host.send("changed"))
        self._bridge.done.connect(lambda job, result: host.send("done", job=job, result=result))
        return self._bridge

    def _page_url(self) -> str:
        """Tomo 窗口里当前页面的地址（窗口进程每次调桥都带上，状态变了也报）；窗口没开是空串。"""
        window = getattr(self, "_window", None)
        return window.url if window is not None and window.connected else ""

    def _on_folder_roots(self) -> None:
        """（界面线程）文件夹线程收回了根目录：对话页的设置重取本机状态。"""
        bridge = getattr(self, "_bridge", None)
        if bridge is not None:
            bridge.changed.emit()

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
        self._awake_settings = (bool(store.global_settings.keep_awake), bool(store.global_settings.keep_awake_on_battery))
        self._apply_keep_awake()   # 成员都断开 / 都暂停了马上放开，新连上马上拿住
        self._restart_worker()
        if getattr(self, "_folders", None) is not None:
            self._folders.start()
        if getattr(self, "_requests", None) is not None:
            self._requests.start()

    def _on_device_result(self, profile_id: str, status: str) -> None:
        """（界面线程）认电脑说这把密钥不能再用：本机丢掉它（同一条连接还有别的成员就删这个，否则标出来等对话页重新登记），
        重启各线程，对话页重取本机状态（据此重新登记）。"""
        from . import device_link
        from .profile_store import ProfileStore

        try:
            outcome = device_link.settle(ProfileStore.load(), profile_id, status)
            log.warning("这台电脑的一个成员不再用原密钥（%s → %s）· profile=%s", status, outcome, profile_id)
        except Exception:
            log.exception("改本机成员失败")
            return
        self._reload_profiles()
        bridge = getattr(self, "_bridge", None)
        if bridge is not None:
            bridge.changed.emit()

    def _apply_keep_awake(self) -> None:
        """（界面线程）按开关与成员拿住 / 放开：至少一个成员连着（配对有效：没暂停、密钥没被说不能再用）才保持。"""
        keeper = getattr(self, "_keep_awake", None)
        if keeper is None or self._quitting:
            return
        enabled, on_battery = getattr(self, "_awake_settings", (True, False))
        try:
            keeper.apply(enabled=enabled, on_battery=on_battery,
                         members=any(member_active(cfg) for cfg in self.configs))
        except Exception:
            log.exception("保持电脑唤醒失败（忽略）")

    def _set_keep_awake(self, enabled: bool, on_battery: bool) -> None:
        """（界面线程）设置里改了两个开关（已存进文件）：按新值拿住 / 放开。不改各成员配置——取任务线程的配置副本
        存盘时只写它自己改过的全局项，文件里的新值不会被旧副本盖回去（profile_store.save_profile）。"""
        self._awake_settings = (bool(enabled), bool(on_battery))
        self._apply_keep_awake()

    def _start_folders(self) -> None:
        """设置里添加 / 移除了文件夹、公司选了文件夹：文件夹线程没在跑就起来，在跑就让下一轮马上开始。"""
        if getattr(self, "_folders", None) is not None:
            self._folders.wake()

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
        if getattr(self, "_keep_awake", None) is not None:
            self._keep_awake.stop()   # 退出 CC 马上放开，系统按原电源设置睡眠
        if getattr(self, "_hotkey", None) is not None:
            self._hotkey.release()
        if getattr(self, "_bridge", None) is not None:
            self._bridge.shutdown()
        if getattr(self, "_window", None) is not None:
            self._window.quit()   # 窗口进程跟着退出
        if getattr(self, "_auto_timer", None) is not None:
            self._auto_timer.stop()
        if getattr(self, "_folders", None) is not None:
            self._folders.stop()
        if getattr(self, "_requests", None) is not None:
            self._requests.stop()
        worker = self.worker
        if worker is not None and worker.isRunning():
            worker.stop(signal_offline=True)
            return
        self.worker = None
        self.tray.hide()
        self.app.quit()

    def run(self) -> int:
        return self.app.exec()
