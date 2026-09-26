# -*- coding: utf-8 -*-
"""系统托盘常驻(PySide6)· 后台线程跑 DBF 直写 poll loop,托盘显示连接/活动状态。

主线程 = Qt 事件循环 + 托盘图标(绿空闲/黄工作/红错误/灰离线)+ 右键菜单(状态/打开日志/
重新配对/退出);后台 QThread = QueueClient poll → _handle_dbf_task 直写 → 周期心跳 +
夜间 PACK tick(复用 main 的核心,不重复直写逻辑)。worker 出状态/离线通过信号回主线程。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import watchdog
from .config import CompanionConfig
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
        p = asset_path("pearnly.png")
        _BASE_PM = QPixmap(str(p)) if p.exists() else QPixmap()
    return _BASE_PM


def _status_icon(key: str) -> QIcon:
    """托盘图标 = Pearnly logo + 右下角状态点(brand + 状态都在)。无资源时回落纯色点。"""
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



MENU_TEXT_MAX = 48


def _menu_text(text: str) -> str:
    """托盘菜单状态行：过长截断（完整内容在悬停提示里），不把菜单撑宽。"""
    value = " ".join(str(text or "").split())
    return value if len(value) <= MENU_TEXT_MAX else value[: MENU_TEXT_MAX - 1] + "…"

class TrayApp(QObject):
    worker_stopped = Signal(object)

    def __init__(self, configs, *, open_connections: bool = False) -> None:
        app = QApplication.instance() or QApplication([])
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
        self._settings_action = None
        self._logs_action = None
        self._quit_action = None
        self._profile_states: dict[str, tuple[str, object]] = {}
        self._last_keys: dict[str, str] = {}
        self._pending_update = None  # (version, url)
        self._update_worker = None  # 后台下载线程(存活期持引用防 GC)
        self._restart_pending = False
        self._quitting = False
        self.worker_stopped.connect(self._on_worker_finished)
        self._build_menu()
        self.tray.show()
        activity("log_started", lang=self.lang)
        self._heal = watchdog.HealBackoff()  # 自愈退避:防目标永久不可达时无限重启空转
        self.worker = self._spawn_worker()
        # 看门狗:主线程定时巡检 worker 存活时标,卡死即自愈重启。主线程 Qt 事件循环不受
        # worker(后台 QThread)卡死影响,QTimer 必按时触发 → 卡死能被稳稳兜住。
        self._watchdog = QTimer()
        self._watchdog.timeout.connect(self._watchdog_check)
        self._watchdog.start(15000)
        if open_connections:
            QTimer.singleShot(0, self._repair)

    def _spawn_worker(self) -> PollWorker:
        """建 poll worker、接好信号并启动(首次启动与自愈重启共用,避免两处接线漂移)。"""
        w = PollWorker(self.configs)
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
        # 「设置 / 重新配对」单一入口(旧版「打开设置」「重新配对」两项开的是同一个配对窗,冗余)。
        self._settings_action = menu.addAction(
            t("tray_open_settings", self.lang), self._repair
        )
        self._logs_action = menu.addAction(
            t("tray_open_logs", self.lang), self._open_logs
        )
        menu.addSeparator()
        self._quit_action = menu.addAction(t("tray_quit", self.lang), self._quit)
        self.tray.setContextMenu(menu)

    def _apply_language(self, lang: str) -> None:
        self.lang = normalize(lang)
        self._retranslate()

    def _retranslate(self) -> None:
        if self._settings_action:
            self._settings_action.setText(t("tray_open_settings", self.lang))
        if self._logs_action:
            self._logs_action.setText(t("tray_open_logs", self.lang))
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
            text = t("tray_starting", self.lang)
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
        if self._pending_update and self._pending_update[0] == version:
            return  # 同版本只提示一次
        self._pending_update = (version, url)
        if self._update_action:
            self._update_action.setText(t("tray_update_to", self.lang, ver=version))
            self._update_action.setVisible(True)
        activity("log_update_found", lang=self.lang, ver=version)
        self.tray.showMessage(
            t("update_avail_title", self.lang),
            t("update_avail_body", self.lang, ver=version),
            pearnly_icon(),
        )

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

    def _do_update(self) -> None:
        if not self._pending_update or self._update_worker is not None:
            return  # 已在更新中 → 忽略重复点(防再点又起一个下载)
        version, url = self._pending_update
        activity("log_updating", lang=self.lang, ver=version)
        if self._update_action:
            self._update_action.setEnabled(False)  # 下载期间禁用,避免重复触发
        self.tray.showMessage(
            t("update_avail_title", self.lang),
            t("update_downloading", self.lang),
            pearnly_icon(),
        )
        # 下载放后台线程 → UI 不卡死(61MB 主线程同步下载是「点了就卡」的根因)。
        from .updater import DOWNLOAD_SITE

        self._update_worker = _UpdateWorker(DOWNLOAD_SITE, url)
        self._update_worker.done.connect(self._on_update_downloaded)
        self._update_worker.failed.connect(self._on_update_failed)
        self._update_worker.start()

    def _on_update_downloaded(self, path: str) -> None:
        from .updater import run_installer_silent

        try:
            run_installer_silent(path)  # 安装器关本进程→装新→自启(本进程随之退出)
        except Exception:
            log.exception("自动更新启动安装器失败")
            self._on_update_failed()

    def _on_update_failed(self) -> None:
        self._update_worker = None
        if self._update_action:
            self._update_action.setEnabled(True)  # 放回,允许重试
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

    def _open_logs(self) -> None:
        from .config import LOG_DIR

        try:
            os.startfile(str(LOG_DIR))
        except Exception:
            log.exception("打开日志目录失败")

    def _repair(self) -> None:
        from .gui_profiles import open_profile_manager

        live = {"profiles": False, "pending": False}

        def language_changed(lang: str) -> None:
            self._apply_language(lang)
            live["pending"] = True

        def profiles_changed() -> None:
            self._reload_profiles()
            live["profiles"] = True
            live["pending"] = False

        changed = open_profile_manager(
            profile_states=dict(self._profile_states),
            profile_state_provider=lambda: dict(self._profile_states),
            on_language_changed=language_changed,
            on_profiles_changed=profiles_changed,
        )
        if changed and (not live["profiles"] or live["pending"]):
            self._reload_profiles()

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

    def _restart_worker(self) -> None:
        """协作式停旧 worker；finished 后才用最新 configs 启动唯一新 worker。"""
        if self._quitting:
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
        worker = self.worker
        if worker is not None and worker.isRunning():
            worker.stop(signal_offline=True)
            return
        self.worker = None
        self.tray.hide()
        self.app.quit()

    def run(self) -> int:
        return self.app.exec()
