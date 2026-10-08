"""Small Qt and root-selection helpers for the Express pairing dialog."""

from __future__ import annotations

import ntpath
import re
from pathlib import Path
from typing import List

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .account_probe import AccountSet
from .express_locate import sccomp_company_count, sccomp_path

_DEFAULT_MIN_COMPANIES = 10


class NoScrollComboBox(QComboBox):
    """Ignore wheel changes while collapsed so users cannot switch accounts by accident."""

    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


class ProbeThread(QThread):
    ok = Signal(object)
    err = Signal(object)

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            self.ok.emit(self._fn())
        except Exception as exc:  # noqa: BLE001 - the GUI renders a safe error
            self.err.emit(exc)


def account_choice_label(account: AccountSet) -> str:
    name = (account.name or account.code or "?").strip()
    tax_tail = f" · {account.tax_id[-4:]}" if account.tax_id else ""
    return f"{name}{tax_tail}"


def norm_root(path) -> str:
    return str(path or "").lower().rstrip("\\/")


def same_root(left, right) -> bool:
    return bool(left) and bool(right) and norm_root(left) == norm_root(right)


def year_key(root: str) -> int:
    years = re.findall(r"\d{2}", Path(root).name)
    return max((int(year) for year in years), default=-1)


def root_identity(root: str):
    try:
        stat = sccomp_path(root).stat()
        return "sig", stat.st_size, int(stat.st_mtime)
    except OSError:
        return "path", norm_root(root)


def rank_roots(candidates: List[str]):
    seen, roots = set(), []
    for candidate in candidates:
        if not norm_root(candidate):
            continue
        company_count = sccomp_company_count(candidate)
        if company_count <= 0:
            continue
        identity = root_identity(candidate)
        if identity in seen:
            continue
        seen.add(identity)
        roots.append((candidate, company_count))
    roots.sort(key=lambda item: (year_key(item[0]), item[1]), reverse=True)
    return roots


def pick_default(roots) -> str:
    for root, company_count in roots:
        if company_count >= _DEFAULT_MIN_COMPANIES:
            return root
    return roots[0][0]


def profile_root_candidates(store, exclude_id: str = "") -> List[str]:
    """Reuse configured roots as candidates; rank_roots still validates SCCOMP."""
    roots = []
    for record in store.profiles:
        if record.profile_id == exclude_id:
            continue
        root = str(record.express_root or "").strip()
        if not root:
            root = ntpath.dirname(str(record.account_dir or "").rstrip("\\/"))
        if root:
            roots.append(root)
    return roots


def _compact_form() -> QFormLayout:
    form = QFormLayout()
    form.setContentsMargins(0, 0, 0, 0)
    form.setVerticalSpacing(6)
    form.setLabelAlignment(Qt.AlignLeft)
    form.setRowWrapPolicy(QFormLayout.DontWrapRows)
    return form


def build_pairing_ui(dialog) -> None:
    """配对 / 修改连接窗只摆两行：配对码、Express 数据（检测到的数据根 + 「更换…」）。

    语言切换、版本号、开机启动都在主窗口（2026-10-08），这里不再重复。
    """
    root = QVBoxLayout(dialog)

    # 账套 / 年度由网页第一步选择（用户 2026-09-26）：介绍文案与账套区不再摆出来，
    # 控件仍挂在窗口上隐藏着——配对要用检测出的数据根和账套名单（内部默认账套、授权全部数据根）。
    hidden = []
    dialog.intro = QLabel(dialog._t("intro"))
    dialog.sec_connect = QLabel(dialog._t("sec_connect"))
    hidden += [dialog.intro, dialog.sec_connect]

    form = _compact_form()
    dialog.token = QLineEdit()
    dialog.token.setPlaceholderText(
        dialog._t("ph_code_keep" if dialog._prefill else "ph_code")
    )
    dialog.lbl_code = QLabel(dialog._t("lbl_code"))
    form.addRow(dialog.lbl_code, dialog.token)

    dialog.lbl_express = QLabel(dialog._t("lbl_express"))
    dialog.express_value = QLabel("")
    dialog.express_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
    dialog.browse_btn = QPushButton(dialog._t("btn_browse"))
    dialog.browse_btn.clicked.connect(dialog._browse_root)
    express_row = QHBoxLayout()
    express_row.addWidget(dialog.express_value, 1)
    express_row.addWidget(dialog.browse_btn)
    form.addRow(dialog.lbl_express, express_row)

    dialog.sec_account = QLabel(dialog._t("sec_account"))
    dialog.root_combo = NoScrollComboBox()
    dialog.root_combo.setPlaceholderText(dialog._t(dialog._root_placeholder_key))
    dialog.root_combo.currentIndexChanged.connect(dialog._on_root_changed)
    dialog.lbl_root = QLabel(dialog._t("lbl_root"))
    dialog.account = NoScrollComboBox()
    dialog.account.setPlaceholderText(dialog._t(dialog._account_placeholder_key))
    dialog.account.currentIndexChanged.connect(dialog._on_account_changed)
    dialog.lbl_acct = QLabel(dialog._t("lbl_acct"))
    dialog.help_acct = QLabel(dialog._t("help_acct"))
    dialog.all_roots = QCheckBox(dialog._t("chk_all_roots"))
    dialog.all_roots.setChecked(True)  # 检测到的数据根全部授权，目标由网页选
    hidden += [dialog.sec_account, dialog.root_combo, dialog.lbl_root, dialog.account,
               dialog.lbl_acct, dialog.help_acct, dialog.all_roots]
    for widget in hidden:
        widget.setParent(dialog)
        widget.setVisible(False)

    form_host = QWidget()
    form_host.setLayout(form)

    host = QWidget()
    host_layout = QVBoxLayout(host)
    host_layout.setContentsMargins(0, 0, 0, 0)
    host_layout.setSpacing(2)
    host_layout.addWidget(form_host)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)
    scroll.setWidget(host)
    dialog._scroll = scroll
    root.addWidget(scroll, 1)

    dialog.status = QLabel("")
    dialog.status.setWordWrap(True)
    root.addWidget(dialog.status)

    buttons = QHBoxLayout()
    buttons.addStretch(1)
    dialog.cancel_btn = QPushButton(dialog._t("btn_cancel"))
    dialog.cancel_btn.clicked.connect(dialog.reject)
    # 添加 = 「配对」；修改已有连接 = 「保存」。
    dialog.pair_btn = QPushButton(dialog._t("btn_save" if dialog._prefill else "btn_pair"))
    dialog.pair_btn.setDefault(True)
    dialog.pair_btn.clicked.connect(dialog._on_pair)
    buttons.addWidget(dialog.cancel_btn)
    buttons.addWidget(dialog.pair_btn)
    root.addLayout(buttons)


def fit_to_content(dialog) -> None:
    host = dialog._scroll.widget()
    host.layout().activate()
    if getattr(dialog, "_chrome_h", None) is None:
        dialog._chrome_h = max(
            0, dialog.sizeHint().height() - dialog._scroll.sizeHint().height()
        )
    try:
        available = QApplication.primaryScreen().availableGeometry().height()
    except Exception:
        available = 900
    target = min(
        host.sizeHint().height() + dialog._chrome_h,
        int(available * 0.94),
    )
    dialog.resize(max(dialog.width(), 580), target)


def set_pairing_busy(dialog, busy: bool) -> None:
    dialog._pairing_in_progress = busy
    enabled = not busy
    for widget in (
        dialog.cancel_btn,
        dialog.browse_btn,
        dialog.root_combo,
        dialog.account,
        dialog.all_roots,
        dialog.token,
    ):
        widget.setEnabled(enabled)
    # 账套名单还没读到时「配对」保持停用（点了也只会报「请选择 Express 文件夹」）。
    dialog.pair_btn.setEnabled(enabled and bool(getattr(dialog, "_sets", None)))


def shutdown_probes(dialog) -> None:
    dialog._alive = False
    for worker in list(dialog._probes):
        try:
            worker.wait(3000)
        except RuntimeError:
            pass
    dialog._probes.clear()
