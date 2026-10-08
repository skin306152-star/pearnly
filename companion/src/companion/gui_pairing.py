# -*- coding: utf-8 -*-
"""添加成员 / 更换密钥：只填网页「连接 Express」给的配对码（2026-10-08）。

Express 账套是这台电脑的全局设置（主窗口顶部，`express_machine`），这里不再检测、不再选择：
配对时直接用主窗口传进来的数据根，读出账套名单随配对上报，成员的内部默认账套与科目照旧自动取。
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from . import pairing
from .account_probe import probe_account_sets, probe_default_accounts
from .express_machine import default_account
from .gui_pairing_support import Probes
from .i18n import MessageSpec, StatusSpec, default_lang, normalize, t
from .logger import get_logger
from .profile_store import ProfileStore, ProfileStoreError

log = get_logger(__name__)


class PairingDialog(QDialog):
    def __init__(
        self,
        parent=None,
        lang: Optional[str] = None,
        profile_id: Optional[str] = None,
        profile_store: Optional[ProfileStore] = None,
        roots: Optional[List[str]] = None,
    ) -> None:
        super().__init__(parent)
        self.result_info = None
        self._store = profile_store or ProfileStore.load()
        self._profile_id = profile_id
        self._roots = [str(root) for root in (roots or []) if str(root or "").strip()]
        if profile_id is None:
            self._cfg = self._store.new_runtime_config()
        else:
            self._cfg = next(
                (
                    cfg
                    for cfg in self._store.runtime_configs(include_paused=True)
                    if cfg.profile_id == profile_id
                ),
                None,
            )
            if self._cfg is None:
                raise ProfileStoreError("profile not found")
        # 更换密钥：沿用这个成员原来的默认账套与人工改过的科目；添加成员从空白开始。
        self._prefill = bool(self._cfg.paired)
        self.lang = normalize(lang or (self._cfg.ui_lang or default_lang()))
        self._status_spec = StatusSpec(MessageSpec(""), True)
        self._pairing_in_progress = False
        self._probes = Probes()
        from .gui_tray import pearnly_icon

        self.setWindowIcon(pearnly_icon())
        # 普通窗口：可最小化、最大化、关闭，不强制置顶，拖边框随意调大小。
        self.setWindowFlags(
            Qt.Window | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint
        )
        self.setSizeGripEnabled(True)
        self.setMinimumWidth(420)
        self.resize(520, 150)
        self._build()

    def _t(self, key: str, **fmt) -> str:
        return t(key, self.lang, **fmt)

    def _build(self) -> None:
        self.setWindowTitle(self._t("win_title_edit" if self._prefill else "win_title_add"))
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.token = QLineEdit()
        self.token.setPlaceholderText(self._t("ph_code"))
        self.token.textChanged.connect(lambda _text: self._sync_pair_button())
        self.lbl_code = QLabel(self._t("lbl_code"))
        form.addRow(self.lbl_code, self.token)
        root.addLayout(form)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        root.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_btn = QPushButton(self._t("btn_cancel"))
        self.cancel_btn.clicked.connect(self.reject)
        # 添加成员 = 「配对」；更换密钥 = 「保存」。
        self.pair_btn = QPushButton(self._t("btn_save" if self._prefill else "btn_pair"))
        self.pair_btn.setDefault(True)
        self.pair_btn.clicked.connect(self._on_pair)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.pair_btn)
        root.addLayout(buttons)
        self._sync_pair_button()

    def _sync_pair_button(self) -> None:
        self.pair_btn.setEnabled(bool(self.token.text().strip()) and not self._pairing_in_progress)

    def _set_status(self, key: str, ok: bool = True, **fmt) -> None:
        self._status_spec = StatusSpec(MessageSpec(key, fmt), ok)
        self._render_status()

    def _set_pair_error(self, error_key: str, error_fmt=None) -> None:
        reason = MessageSpec(error_key or "pair_err_unexpected", error_fmt or {})
        self._status_spec = StatusSpec(MessageSpec("pair_fail", {"err": reason}), False)
        self._render_status()

    def _render_status(self) -> None:
        self.status.setStyleSheet("color:#666" if self._status_spec.ok else "color:#c0392b")
        self.status.setText(self._status_spec.message.render(self.lang))

    def _set_pairing_busy(self, busy: bool) -> None:
        self._pairing_in_progress = busy
        self.token.setEnabled(not busy)
        self.cancel_btn.setEnabled(not busy)
        self._sync_pair_button()

    def _spawn(self, kind: str, fn, on_ok, on_err=None) -> None:
        self._probes.spawn(kind, fn, on_ok, on_err)

    def closeEvent(self, e):  # noqa: N802 (Qt 命名)
        if self._pairing_in_progress:
            e.ignore()
            return
        self._probes.shutdown()
        super().closeEvent(e)

    def reject(self) -> None:
        if self._pairing_in_progress:
            return
        self._probes.shutdown()
        super().reject()

    def _prefill_account_index(self, sets) -> int:
        """内部默认账套（不显示，用户 2026-09-26：账套由网页第一步选）：已配过且还在 → 沿用；否则取第一个。"""
        want = (self._cfg.account_set or self._cfg.account_dir or "").strip() if self._prefill else ""
        for i, acc in enumerate(sets):
            if want and str(getattr(acc, "path", "") or "").strip() == want:
                return i
        return 0 if sets else -1

    def _auto_mapping(self, chosen, defaults: dict) -> dict:
        """科目映射全自动:同一账套重配沿用存值(保住人工改过的码),换账套用其 ISINFO 默认。

        存值只在「本机上次配对的就是这个账套」时可信(比 account_set PATH 唯一键)。
        """
        want = (self._cfg.account_set or self._cfg.account_dir or "").strip()
        same_set = bool(
            self._prefill
            and want
            and str(getattr(chosen, "path", "") or "").strip() == want
        )
        out = {}
        for key in pairing.ACCOUNT_MAP_KEYS:
            stored = (getattr(self._cfg, key, "") or "").strip() if same_set else ""
            out[key] = stored or str((defaults or {}).get(key, "") or "")
        return out

    def _pair_work(self, token: str, roots: List[str], autostart: bool, lang: str):
        """后台：读这台电脑的账套名单 → 定内部默认账套与科目 → 配对（连 Pearnly 校验密钥并上报账套）。"""
        sets = probe_account_sets(roots[0])
        index = self._prefill_account_index(sets)
        chosen = sets[index] if index >= 0 else None
        if chosen is None:
            chosen = default_account(sets)
        if chosen is None:
            return None
        try:
            defaults = probe_default_accounts(chosen.path) if chosen.path else {}
        except Exception:
            defaults = {}
        return pairing.pair(
            token=token,
            chosen=chosen,
            all_sets=sets,
            express_root=roots[0],
            enable_autostart=autostart,
            mapping=self._auto_mapping(chosen, defaults),
            authorized_roots=list(roots),
            lang=lang,
            config=self._cfg,
            profile_store=self._store,
        )

    def _on_pair(self) -> None:
        token = self.token.text()
        if not token.strip():
            return
        roots = list(self._roots)
        if not roots:
            self._set_status("browse_hint", ok=False)
            return
        # 开机启动在主窗口里设（全局一项）；配对沿用那里的选择，首次配对默认开。
        autostart = bool(getattr(self._store.global_settings, "autostart", True))
        lang = self.lang
        self._set_pairing_busy(True)
        self._set_status("pairing", ok=True)

        def done(res):
            self._set_pairing_busy(False)
            if res is None:
                self._set_status("status_reg_error", ok=False)
            elif res.ok:
                # 不弹「配对成功」框：回到主窗口就能看到这个成员的状态。
                self.result_info = res
                self.accept()
            else:
                self._set_pair_error(res.error_key, res.error_fmt)

        def fail(e):
            log.error("配对后台任务异常: %r", e)
            self._set_pairing_busy(False)
            self._set_pair_error("pair_err_unexpected")

        try:
            self._spawn("pair", lambda: self._pair_work(token, roots, autostart, lang), done, fail)
        except Exception:
            log.exception("启动配对后台任务失败")
            self._set_pairing_busy(False)
            self._set_pair_error("pair_err_unexpected")


_OPEN_DIALOG: Optional[PairingDialog] = None


def open_pairing(
    parent=None,
    *,
    lang: Optional[str] = None,
    profile_id: Optional[str] = None,
    profile_store: Optional[ProfileStore] = None,
    roots: Optional[List[str]] = None,
) -> bool:
    """打开进程内唯一配对窗并返回是否配对成功。"""
    global _OPEN_DIALOG
    if _OPEN_DIALOG is not None:
        try:
            if _OPEN_DIALOG.isVisible():
                _OPEN_DIALOG.raise_()
                _OPEN_DIALOG.activateWindow()
                return False
        except RuntimeError:
            pass  # 旧 Qt 对象已被回收 → 当作没开着
        _OPEN_DIALOG = None
    if QApplication.instance() is None:
        QApplication([])
    _OPEN_DIALOG = PairingDialog(
        parent, lang=lang, profile_id=profile_id, profile_store=profile_store, roots=roots
    )
    try:
        return _OPEN_DIALOG.exec() == QDialog.Accepted
    finally:
        _OPEN_DIALOG = None
