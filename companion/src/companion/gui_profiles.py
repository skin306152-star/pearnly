"""小助手主窗口：一个进程内所有 Express 连接（及各自网页可选的文件夹）的管理。"""

from __future__ import annotations

from typing import Callable, Mapping, Optional

import os

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import autostart
from .express_machine import apply_roots, detect_roots, machine_roots, roots_for_pick
from .folder_store import connection_key
from .gui_folders import FolderRootsBox
from .gui_pairing import open_pairing
from .gui_pairing_support import Probes
from .i18n import default_lang, normalize, persist_ui_lang, t
from .logger import get_logger
from .profile_store import ProfileRecord, ProfileStore
from .version import VERSION

log = get_logger(__name__)


def masked_token(token: str) -> str:
    value = str(token or "").strip()
    if len(value) <= 7:
        return "***"
    return f"{value[:3]}***{value[-4:]}"


def profile_status_key(record: ProfileRecord, state: Optional[tuple[str, str]]) -> str:
    if record.paused:
        return "profiles_paused"
    if state is None or state[0] == "starting":
        return "profiles_connecting"
    current = state[0] if state else "offline"
    if current == "idle":
        return "profiles_online"
    if current in {"working", "indexing"}:
        return "profiles_processing"
    if current in {"waiting_lock", "error"}:
        return "profiles_attention"
    return "profiles_offline"


_STATUS_COLORS = {
    "profiles_online": "#27ae60",
    "profiles_processing": "#b7950b",
    "profiles_attention": "#d35400",
    "profiles_offline": "#c0392b",
}


class ProfileManagerDialog(QDialog):
    """小助手主窗口（2026-10-08）。主次两层（用户 2026-10-08）：

    - 这台电脑（全局）：Express 账套（检测一次、「更换…」时同步到全部成员，见 `express_machine`）、开机启动；
    - 成员：每个网页账号一张卡片——状态、⋯（更换密钥 / 暂停 / 移除）、网页可选的文件夹。添加成员只填配对码。

    语言切换、版本、日志也只在这里一处。托盘左键或「打开小助手」打开。"""

    def __init__(
        self,
        parent=None,
        *,
        lang: Optional[str] = None,
        profile_store: Optional[ProfileStore] = None,
        profile_states: Optional[Mapping[str, tuple[str, str]]] = None,
        profile_state_provider: Optional[
            Callable[[], Mapping[str, tuple[str, str]]]
        ] = None,
        on_language_changed: Optional[Callable[[str], None]] = None,
        on_profiles_changed: Optional[Callable[[], None]] = None,
        on_folders_changed: Optional[Callable[[], None]] = None,
        update_status: Optional[Callable[[], tuple[str, str]]] = None,
        on_update: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._update_status = update_status
        self._on_update = on_update
        self._store = profile_store or ProfileStore.load()
        self.lang = normalize(
            lang or self._store.global_settings.ui_lang or default_lang()
        )
        self.changed = False
        self._on_language_changed = on_language_changed
        self._on_profiles_changed = on_profiles_changed
        self._on_folders_changed = on_folders_changed
        self._profile_states = dict(profile_states or {})
        self._profile_state_provider = profile_state_provider
        self._cards: dict[str, dict] = {}
        self._probes = Probes()
        self._roots: list[str] = machine_roots(self._store)
        self._roots_state = "ready"  # ready / scanning
        self.setWindowTitle(t("profiles_title", self.lang))
        # 普通窗口：可最小化、最大化、关闭，拖边框随意调大小（用户 2026-10-08）。
        self.setWindowFlags(
            Qt.Window | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint
        )
        self.setSizeGripEnabled(True)
        self.setMinimumSize(420, 300)
        self.resize(560, 420)
        self._build()
        self._paint_update()
        self._refresh()
        if not self._roots:
            self._detect_roots()
        self._live_refresh = QTimer(self)
        self._live_refresh.setInterval(750)
        self._live_refresh.timeout.connect(self._refresh_runtime_snapshot)
        self._live_refresh.start()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addStretch(1)
        self.lang_btn = QPushButton(t("lang_toggle", self.lang))
        self.lang_btn.setFlat(True)
        self.lang_btn.clicked.connect(self._toggle_lang)
        header.addWidget(self.lang_btn)
        layout.addLayout(header)

        # 这台电脑（全局）：Express 账套 + 开机启动
        self.machine_box = QGroupBox(t("sec_machine", self.lang))
        machine = QFormLayout(self.machine_box)
        self.lbl_express = QLabel(t("lbl_express", self.lang))
        self.express_value = QLabel()
        self.express_value.setWordWrap(True)
        self.express_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.change_btn = QPushButton(t("btn_browse", self.lang))
        self.change_btn.clicked.connect(self._change_roots)
        express_row = QHBoxLayout()
        express_row.addWidget(self.express_value, 1)
        express_row.addWidget(self.change_btn)
        machine.addRow(self.lbl_express, express_row)
        self.autostart = QCheckBox(t("chk_autostart", self.lang))
        self.autostart.setChecked(self._autostart_enabled())
        self.autostart.toggled.connect(self._toggle_autostart)
        machine.addRow(self.autostart)
        layout.addWidget(self.machine_box)

        # 成员
        members = QHBoxLayout()
        self.members_lbl = QLabel(t("sec_members", self.lang))
        members.addWidget(self.members_lbl)
        members.addStretch(1)
        self.add_btn = QPushButton(t("profiles_add", self.lang))
        self.add_btn.clicked.connect(self._add)
        members.addWidget(self.add_btn)
        layout.addLayout(members)

        self.empty = QLabel()
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        self.cards_host = QWidget()
        self.cards = QVBoxLayout(self.cards_host)
        self.cards.setContentsMargins(0, 0, 0, 0)
        self.cards.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.cards_host)
        layout.addWidget(scroll, 1)

        footer = QHBoxLayout()
        self.ver_lbl = QLabel("v" + VERSION)
        self.ver_lbl.setStyleSheet("color:#999")
        footer.addWidget(self.ver_lbl)
        # 更新：状态一行 + 带下载图标的按钮（平时「检查更新」，有新版「立即更新」）；托盘没接上时（首次配对）不显示。
        self.upd_lbl = QLabel()
        self.upd_lbl.setStyleSheet("color:#999")
        footer.addWidget(self.upd_lbl)
        self.upd_btn = QPushButton()
        self.upd_btn.setIcon(self.style().standardIcon(QStyle.SP_ArrowDown))
        self.upd_btn.setFlat(True)
        self.upd_btn.clicked.connect(self._update_clicked)
        footer.addWidget(self.upd_btn)
        footer.addStretch(1)
        self.logs_btn = QPushButton(t("tray_open_logs", self.lang))
        self.logs_btn.setFlat(True)
        self.logs_btn.clicked.connect(self._open_logs)
        footer.addWidget(self.logs_btn)
        layout.addLayout(footer)

    def _retranslate(self) -> None:
        self.setWindowTitle(t("profiles_title", self.lang))
        self.lang_btn.setText(t("lang_toggle", self.lang))
        self.machine_box.setTitle(t("sec_machine", self.lang))
        self.lbl_express.setText(t("lbl_express", self.lang))
        self.change_btn.setText(t("btn_browse", self.lang))
        self.members_lbl.setText(t("sec_members", self.lang))
        self.add_btn.setText(t("profiles_add", self.lang))
        self.autostart.setText(t("chk_autostart", self.lang))
        self.logs_btn.setText(t("tray_open_logs", self.lang))
        self._paint_update()
        self._refresh()

    def _toggle_lang(self) -> None:
        selected = "zh" if self.lang == "th" else "th"
        try:
            self.lang = persist_ui_lang(self._store, selected)
        except Exception:
            log.exception("保存界面语言失败")
            self._show_error()
            return
        self.changed = True
        self._retranslate()
        if self._on_language_changed is not None:
            self._on_language_changed(self.lang)

    def _sync_language_from_store(self) -> None:
        selected = normalize(self._store.global_settings.ui_lang or default_lang())
        if selected == self.lang:
            return
        self.lang = selected
        self.changed = True
        self._retranslate()
        if self._on_language_changed is not None:
            self._on_language_changed(self.lang)

    def _autostart_enabled(self) -> bool:
        try:
            return autostart.is_enabled()
        except Exception:  # 非 Windows（开发机）读不到注册表：按已存设置显示
            return bool(self._store.global_settings.autostart)

    @staticmethod
    def _fresh_store(store: ProfileStore) -> ProfileStore:
        return ProfileStore.load(store.path, protector=getattr(store, "_protector", None))

    def _toggle_autostart(self, checked: bool) -> None:
        try:
            autostart.enable() if checked else autostart.disable()
            self._store = self._fresh_store(self._store)
            cfg = self._store.new_runtime_config()
            cfg.update(autostart=bool(checked))
            self._store.save_profile(cfg)
        except Exception as exc:
            self.autostart.blockSignals(True)
            self.autostart.setChecked(not checked)
            self.autostart.blockSignals(False)
            self._show_error(exc)

    # ── 这台电脑的 Express 账套 ─────────────────────────────────────────────

    def _paint_roots(self, error_key: str = "") -> None:
        if self._roots_state == "scanning":
            text, ok = t("status_scanning", self.lang), True
        elif error_key or not self._roots:
            text, ok = t(error_key or "browse_hint", self.lang), False
        elif len(self._roots) > 1:
            text, ok = t("express_roots_more", self.lang, path=self._roots[0], n=len(self._roots)), True
        else:
            text, ok = self._roots[0], True
        self.express_value.setText(text)
        self.express_value.setStyleSheet("" if ok else "color:#c0392b")
        busy = self._roots_state == "scanning"
        self.change_btn.setEnabled(not busy)
        self.add_btn.setEnabled(bool(self._roots) and not busy)

    def _detect_roots(self) -> None:
        """还没有成员：后台自动找 Express（读共享盘上的登记表较慢，窗口先打开）。"""
        self._roots_state = "scanning"
        self._paint_roots()
        store = self._store

        def done(roots):
            self._roots_state = "ready"
            if not self._store.profiles:
                self._roots = list(roots or [])
            self._paint_roots()

        def fail(exc):
            log.error("自动探测 Express 数据根失败: %r", exc)
            self._roots_state = "ready"
            self._paint_roots()

        self._probes.spawn("roots", lambda: detect_roots(store), done, fail)

    def _change_roots(self) -> None:
        """「更换…」：选 Express 数据所在文件夹；有成员时同步到全部成员（服务端下次心跳拿到，不用重新配对）。"""
        picked = QFileDialog.getExistingDirectory(self, t("browse_title", self.lang))
        if not picked:
            return
        self._roots_state = "scanning"
        self._paint_roots()
        store = self._store

        def work():
            roots = roots_for_pick(picked)
            if roots:
                # 写之前从磁盘重读：小助手后台可能刚更新过成员（例如新发现的年度），不拿窗口里的旧副本盖回去。
                fresh = self._fresh_store(store)
                if fresh.profiles:
                    apply_roots(fresh, roots)
            return roots

        def done(roots):
            self._roots_state = "ready"
            if not roots:
                self._paint_roots("status_reg_error")
                return
            self._roots = list(roots)
            self._paint_roots()
            self._store = self._fresh_store(store)
            if self._store.profiles:
                self._profiles_changed()
                self._refresh()

        def fail(exc):
            log.error("更换 Express 数据根失败: %r", exc)
            self._roots_state = "ready"
            self._paint_roots("status_reg_error")

        self._probes.spawn("roots", work, done, fail)

    def done(self, result) -> None:  # noqa: D401 - QDialog 关窗
        self._probes.shutdown()
        super().done(result)

    # ── 更新 ───────────────────────────────────────────────────────────────

    def _paint_update(self) -> None:
        if self._update_status is None:
            self.upd_lbl.hide()
            self.upd_btn.hide()
            return
        try:
            state, version = self._update_status()
        except Exception:
            state, version = "none", ""
        self.upd_lbl.setText(t("upd_" + state, self.lang, ver=version) if state != "none" else "")
        busy = state in ("checking", "downloading", "installing")
        self.upd_btn.setText(t("upd_now" if state in ("found", "ready", "failed") else "upd_check", self.lang))
        self.upd_btn.setVisible(not busy)

    def _update_clicked(self) -> None:
        if self._on_update is not None:
            try:
                self._on_update()
            except Exception:
                log.exception("更新按钮处理失败")
        self._paint_update()

    def _open_logs(self) -> None:
        from .config import LOG_DIR

        try:
            os.startfile(str(LOG_DIR))
        except Exception:
            log.exception("打开日志目录失败")

    def _record(self, profile_id: str) -> Optional[ProfileRecord]:
        return next(
            (record for record in self._store.profiles if record.profile_id == profile_id),
            None,
        )

    def _refresh_runtime_snapshot(self) -> None:
        """Keep the open window aligned with worker state and persisted identity."""
        try:
            store = ProfileStore.load(self._store.path)
            states = (
                dict(self._profile_state_provider())
                if self._profile_state_provider is not None
                else self._profile_states
            )
        except Exception as exc:
            log.debug("刷新 Express 连接状态失败: %r", exc)
            return
        finally:
            self._paint_update()
        if store.profiles == self._store.profiles and states == self._profile_states:
            return
        rebuild = store.profiles != self._store.profiles
        self._store = store
        self._profile_states = states
        if rebuild:
            if store.profiles and self._roots_state != "scanning":
                self._roots = machine_roots(store)
                self._paint_roots()
            self._refresh()
        else:
            self._paint_states()

    def _refresh(self) -> None:
        """按已存连接重建卡片（只在连接增删改、换语言时；状态变化只刷状态行）。"""
        for card in self._cards.values():
            card["box"].setParent(None)
            card["box"].deleteLater()
        self._cards.clear()
        for record in self._store.profiles:
            self._cards[record.profile_id] = self._build_card(record)
            self.cards.insertWidget(self.cards.count() - 1, self._cards[record.profile_id]["box"])
        self.empty.setText(t("profiles_empty", self.lang))
        self.empty.setVisible(not self._cards)
        self._paint_states()
        self._paint_roots()

    def _build_card(self, record: ProfileRecord) -> dict:
        box = QGroupBox(str(getattr(record, "pearnly_account", "") or "").strip() or "-")
        inner = QVBoxLayout(box)
        top = QHBoxLayout()
        status = QLabel()
        top.addWidget(status)
        top.addStretch(1)
        more = QToolButton()
        more.setText("⋯")
        more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(more)
        menu.addAction(t("profiles_edit", self.lang), lambda pid=record.profile_id: self._edit(pid))
        pause = menu.addAction(
            t("profiles_resume" if record.paused else "profiles_pause", self.lang),
            lambda pid=record.profile_id: self._toggle_paused(pid),
        )
        menu.addSeparator()
        menu.addAction(t("profiles_remove", self.lang), lambda pid=record.profile_id: self._remove(pid))
        more.setMenu(menu)
        top.addWidget(more)
        inner.addLayout(top)
        try:
            key = connection_key(self._store.runtime_config(record.profile_id))
        except Exception:
            key = ""
        if key:
            inner.addWidget(FolderRootsBox(key, lang=self.lang, on_changed=self._folders_changed))
        return {"box": box, "status": status, "pause": pause, "record": record}

    def _paint_states(self) -> None:
        for profile_id, card in self._cards.items():
            key = profile_status_key(card["record"], self._profile_states.get(profile_id))
            card["status"].setText(t(key, self.lang))
            card["status"].setStyleSheet(f"color:{_STATUS_COLORS.get(key, '#7f8c8d')}")

    def _show_error(self, exc: Optional[Exception] = None) -> None:
        if exc is not None:
            log.error("更新 Express 连接失败: %r", exc)
        QMessageBox.warning(
            self, t("profiles_title", self.lang), t("profiles_error", self.lang)
        )

    def _profiles_changed(self) -> None:
        self.changed = True
        if self._on_profiles_changed is None:
            return
        try:
            self._on_profiles_changed()
        except Exception:
            log.exception("实时应用 Express 连接变更失败，将在关闭设置后重试")

    def _folders_changed(self) -> None:
        if self._on_folders_changed is None:
            return
        try:
            self._on_folders_changed()
        except Exception:
            log.exception("启动文件夹自动录入失败")

    def _add(self) -> None:
        try:
            saved = open_pairing(self, lang=self.lang, profile_store=self._store, roots=self._roots)
        except Exception as exc:
            self._show_error(exc)
            return
        finally:
            self._sync_language_from_store()
        if saved:
            self._profiles_changed()
            self._refresh()

    def _edit(self, profile_id: str) -> None:
        if self._record(profile_id) is None:
            return
        try:
            saved = open_pairing(
                self,
                lang=self.lang,
                profile_id=profile_id,
                profile_store=self._store,
                roots=self._roots,
            )
        except Exception as exc:
            self._show_error(exc)
            return
        finally:
            self._sync_language_from_store()
        if saved:
            self._profiles_changed()
            self._refresh()

    def _toggle_paused(self, profile_id: str) -> None:
        record = self._record(profile_id)
        if record is None:
            return
        try:
            self._store.set_paused(record.profile_id, not record.paused)
        except Exception as exc:
            self._show_error(exc)
            return
        self._profiles_changed()
        self._refresh()

    def _remove(self, profile_id: str) -> None:
        record = self._record(profile_id)
        if record is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle(t("profiles_remove_title", self.lang))
        box.setText(
            t(
                "profiles_remove_body",
                self.lang,
                company=record.pearnly_account or record.account_company or "-",
            )
        )
        remove_btn = box.addButton(
            t("profiles_remove", self.lang), QMessageBox.DestructiveRole
        )
        cancel_btn = box.addButton(t("btn_cancel", self.lang), QMessageBox.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.exec()
        if box.clickedButton() is not remove_btn:
            return
        try:
            self._store.remove_profile(record.profile_id)
        except Exception as exc:
            self._show_error(exc)
            return
        self._profiles_changed()
        self._refresh()


_OPEN_MANAGER: Optional[ProfileManagerDialog] = None


def open_profile_manager(
    parent=None,
    *,
    profile_states: Optional[Mapping[str, tuple[str, str]]] = None,
    profile_state_provider: Optional[
        Callable[[], Mapping[str, tuple[str, str]]]
    ] = None,
    on_language_changed: Optional[Callable[[str], None]] = None,
    on_profiles_changed: Optional[Callable[[], None]] = None,
    on_folders_changed: Optional[Callable[[], None]] = None,
    update_status: Optional[Callable[[], tuple[str, str]]] = None,
    on_update: Optional[Callable[[], None]] = None,
) -> bool:
    """Open the process-wide main window and report whether profiles changed."""
    global _OPEN_MANAGER
    if _OPEN_MANAGER is not None:
        try:
            if _OPEN_MANAGER.isVisible():
                _OPEN_MANAGER.raise_()
                _OPEN_MANAGER.activateWindow()
                return False
        except RuntimeError:
            pass
        _OPEN_MANAGER = None
    if QApplication.instance() is None:
        QApplication([])
    _OPEN_MANAGER = ProfileManagerDialog(
        parent,
        profile_states=profile_states,
        profile_state_provider=profile_state_provider,
        on_language_changed=on_language_changed,
        on_profiles_changed=on_profiles_changed,
        on_folders_changed=on_folders_changed,
        update_status=update_status,
        on_update=on_update,
    )
    try:
        _OPEN_MANAGER.exec()
        return _OPEN_MANAGER.changed
    finally:
        _OPEN_MANAGER = None
