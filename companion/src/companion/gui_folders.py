# -*- coding: utf-8 -*-
"""托盘「自动录入文件夹…」：在本机给每条连接设一次允许读取的根目录。

这是安全边界，只能在这台电脑上设（网页上只能在这些根目录下选子文件夹）；否则能登录网页的人就能让办公室
电脑把任意文件传上云。具体哪个文件夹、什么时候跑、推到哪个账套，都在网页的「自动录入规则」里设。
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .folder_store import connection_key, roots, set_all_roots
from .i18n import t


class FolderRootsDialog(QDialog):
    def __init__(self, configs: Iterable, *, lang: str, on_saved: Optional[Callable[[], None]] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.lang = lang
        self._on_saved = on_saved
        self._configs = [cfg for cfg in configs if connection_key(cfg)]
        self._paths: dict[str, list[str]] = {connection_key(cfg): [row["path"] for row in roots(connection_key(cfg))]
                                             for cfg in self._configs}
        self.setWindowTitle(t("folders_title", lang))
        self.setMinimumSize(560, 360)
        layout = QVBoxLayout(self)
        hint = QLabel(t("folders_hint", lang))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.profile = QComboBox()
        for cfg in self._configs:
            label = str(getattr(cfg, "pearnly_account", "") or getattr(cfg, "endpoint_name", "") or cfg.profile_id)
            self.profile.addItem(label, connection_key(cfg))
        self.profile.currentIndexChanged.connect(lambda _index: self._paint())
        layout.addWidget(self.profile)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        add = QPushButton(t("folders_add", lang))
        add.clicked.connect(self._add)
        remove = QPushButton(t("folders_remove", lang))
        remove.clicked.connect(self._remove)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        save = QPushButton(t("folders_save", lang))
        save.setDefault(True)
        save.clicked.connect(self._save)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        if not self._configs:
            hint.setText(t("folders_no_connection", lang))
            add.setEnabled(False)
            save.setEnabled(False)
        self._paint()

    def _key(self) -> str:
        return self.profile.currentData() or ""

    def _paint(self) -> None:
        self.list.clear()
        for path in self._paths.get(self._key(), []):
            self.list.addItem(path)

    def _add(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, t("folders_pick", self.lang))
        if folder and folder not in self._paths.setdefault(self._key(), []):
            self._paths[self._key()].append(folder)
            self._paint()

    def _remove(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            del self._paths[self._key()][row]
            self._paint()

    def _save(self) -> None:
        try:
            set_all_roots(self._paths)
        except ValueError as error:
            QMessageBox.warning(self, t("folders_title", self.lang), t("folders_overlap", self.lang, path=str(error)))
            return
        if self._on_saved:
            self._on_saved()
        self.accept()
