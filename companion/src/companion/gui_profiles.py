"""Express Profile management for one shared Companion process."""

from __future__ import annotations

from typing import Callable, Mapping, Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .gui_pairing import open_pairing
from .i18n import default_lang, normalize, persist_ui_lang, t
from .logger import get_logger
from .profile_store import ProfileRecord, ProfileStore

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


def profile_item_text(
    record: ProfileRecord,
    lang: str,
    state: Optional[tuple[str, str]] = None,
    token: str = "",
) -> str:
    # 只显示连接人的 Pearnly 账号 + 状态；账套 / 年度由网页第一步选择，这里不显示（用户 2026-09-26）。
    label = t(profile_status_key(record, state), lang)
    owner = str(getattr(record, "pearnly_account", "") or "").strip() or "-"
    return f"{owner} · {label}"


class ProfileManagerDialog(QDialog):
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
    ) -> None:
        super().__init__(parent)
        self._store = profile_store or ProfileStore.load()
        self.lang = normalize(
            lang or self._store.global_settings.ui_lang or default_lang()
        )
        self.changed = False
        self._on_language_changed = on_language_changed
        self._on_profiles_changed = on_profiles_changed
        self._profile_states = dict(profile_states or {})
        self._profile_state_provider = profile_state_provider
        self.setWindowTitle(t("profiles_title", self.lang))
        self.setMinimumSize(520, 340)
        self._build()
        self._refresh()
        self._live_refresh = QTimer(self)
        self._live_refresh.setInterval(750)
        self._live_refresh.timeout.connect(self._refresh_runtime_snapshot)
        self._live_refresh.start()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addStretch(1)
        self.lang_lbl = QLabel(t("profiles_lang", self.lang))
        header.addWidget(self.lang_lbl)
        self.lang_btn = QPushButton(t("lang_toggle", self.lang))
        self.lang_btn.clicked.connect(self._toggle_lang)
        header.addWidget(self.lang_btn)
        layout.addLayout(header)

        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._selection_changed)
        self.list.itemDoubleClicked.connect(lambda _item: self._edit())
        layout.addWidget(self.list, 1)

        buttons = QHBoxLayout()
        self.add_btn = QPushButton(t("profiles_add", self.lang))
        self.edit_btn = QPushButton(t("profiles_edit", self.lang))
        self.pause_btn = QPushButton(t("profiles_pause", self.lang))
        self.remove_btn = QPushButton(t("profiles_remove", self.lang))
        self.add_btn.clicked.connect(self._add)
        self.edit_btn.clicked.connect(self._edit)
        self.pause_btn.clicked.connect(self._toggle_paused)
        self.remove_btn.clicked.connect(self._remove)
        for button in (
            self.add_btn,
            self.edit_btn,
            self.pause_btn,
            self.remove_btn,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    def _retranslate(self) -> None:
        self.setWindowTitle(t("profiles_title", self.lang))
        self.lang_lbl.setText(t("profiles_lang", self.lang))
        self.lang_btn.setText(t("lang_toggle", self.lang))
        self.add_btn.setText(t("profiles_add", self.lang))
        self.edit_btn.setText(t("profiles_edit", self.lang))
        self.pause_btn.setText(t("profiles_pause", self.lang))
        self.remove_btn.setText(t("profiles_remove", self.lang))
        self._refresh(self._selected_profile_id())

    def _selected_profile_id(self) -> Optional[str]:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

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

    def _selected_record(self) -> Optional[ProfileRecord]:
        item = self.list.currentItem()
        profile_id = item.data(Qt.UserRole) if item is not None else None
        return next(
            (
                record
                for record in self._store.profiles
                if record.profile_id == profile_id
            ),
            None,
        )

    def _refresh_runtime_snapshot(self) -> None:
        """Keep the open manager aligned with worker state and persisted identity."""
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
        if store.profiles == self._store.profiles and states == self._profile_states:
            return
        selected = self._selected_profile_id()
        self._store = store
        self._profile_states = states
        self._refresh(selected)

    def _refresh(self, select_id: Optional[str] = None) -> None:
        self.list.clear()
        selected_row = -1
        for row, record in enumerate(self._store.profiles):
            runtime = self._store.runtime_config(record.profile_id)
            item = QListWidgetItem(
                profile_item_text(
                    record,
                    self.lang,
                    self._profile_states.get(record.profile_id),
                    runtime.bearer_token,
                )
            )
            item.setData(Qt.UserRole, record.profile_id)
            self.list.addItem(item)
            if record.profile_id == select_id:
                selected_row = row
        if self.list.count():
            self.list.setCurrentRow(selected_row if selected_row >= 0 else 0)
        self._selection_changed()

    def _selection_changed(self, *_args) -> None:
        record = self._selected_record()
        enabled = record is not None
        self.edit_btn.setEnabled(enabled)
        self.pause_btn.setEnabled(enabled)
        self.remove_btn.setEnabled(enabled)
        if record is not None:
            key = "profiles_resume" if record.paused else "profiles_pause"
            self.pause_btn.setText(t(key, self.lang))

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

    def _add(self) -> None:
        try:
            saved = open_pairing(self, lang=self.lang, profile_store=self._store)
        except Exception as exc:
            self._show_error(exc)
            return
        finally:
            self._sync_language_from_store()
        if saved:
            self._profiles_changed()
            self._refresh(self._store.profiles[-1].profile_id)

    def _edit(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        try:
            saved = open_pairing(
                self,
                lang=self.lang,
                profile_id=record.profile_id,
                profile_store=self._store,
            )
        except Exception as exc:
            self._show_error(exc)
            return
        finally:
            self._sync_language_from_store()
        if saved:
            self._profiles_changed()
            self._refresh(record.profile_id)

    def _toggle_paused(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        try:
            self._store.set_paused(record.profile_id, not record.paused)
        except Exception as exc:
            self._show_error(exc)
            return
        self._profiles_changed()
        self._refresh(record.profile_id)

    def _remove(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle(t("profiles_remove_title", self.lang))
        box.setText(
            t(
                "profiles_remove_body",
                self.lang,
                company=record.account_company or "-",
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
) -> bool:
    """Open the process-wide manager and report whether profiles changed."""
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
    )
    try:
        _OPEN_MANAGER.exec()
        return _OPEN_MANAGER.changed
    finally:
        _OPEN_MANAGER = None
