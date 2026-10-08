# -*- coding: utf-8 -*-
"""主窗口里每条连接下的「网页可选的文件夹」：在本机给这条连接设允许读取的根目录。

这是安全边界，只能在这台电脑上设（网页上只能在这些根目录下选子文件夹）；否则能登录网页的人就能让办公室
电脑把任意文件传上云。具体哪个文件夹、什么时候跑、推到哪个账套，都在网页的「自动录入规则」里设。
添加、移除当场保存（2026-10-08 起不再有单独的文件夹窗口和「保存」按钮）。
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .folder_store import roots, set_roots
from .i18n import t
from .logger import get_logger

log = get_logger(__name__)


class FolderRootsBox(QWidget):
    def __init__(self, key: str, *, lang: str, on_changed: Optional[Callable[[], None]] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.key = key
        self.lang = lang
        self._on_changed = on_changed
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.label = QLabel()
        layout.addWidget(self.label)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.currentRowChanged.connect(lambda _row: self._sync_buttons())
        layout.addWidget(self.list)
        buttons = QHBoxLayout()
        self.add_btn = QPushButton()
        self.add_btn.clicked.connect(self._add)
        self.remove_btn = QPushButton()
        self.remove_btn.clicked.connect(self._remove)
        buttons.addWidget(self.add_btn)
        buttons.addWidget(self.remove_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.retranslate(lang)
        self._paint()

    def retranslate(self, lang: str) -> None:
        self.lang = lang
        self.label.setText(t("folders_label", lang))
        self.add_btn.setText(t("folders_add", lang))
        self.remove_btn.setText(t("folders_remove", lang))

    def paths(self) -> list[str]:
        return [row["path"] for row in roots(self.key)]

    def _paint(self) -> None:
        self.list.clear()
        for path in self.paths():
            self.list.addItem(path)
        # 列表只占刚好的行数，没有文件夹就不摆空框（不在窗口里留一大块空白）。
        count = self.list.count()
        self.list.setVisible(count > 0)
        self.remove_btn.setVisible(count > 0)
        if count:
            self.list.setFixedHeight(self.list.sizeHintForRow(0) * count + 2 * self.list.frameWidth())
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.remove_btn.setEnabled(self.list.currentRow() >= 0)

    def _save(self, paths: list[str]) -> bool:
        try:
            set_roots(self.key, paths)
        except ValueError as error:
            QMessageBox.warning(self, t("folders_title", self.lang),
                                t("folders_overlap", self.lang, path=str(error)))
            return False
        except OSError:
            log.exception("保存允许读取的文件夹失败")
            QMessageBox.warning(self, t("folders_title", self.lang), t("profiles_error", self.lang))
            return False
        self._paint()
        if self._on_changed is not None:
            self._on_changed()
        return True

    def _add(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, t("folders_pick", self.lang))
        current = self.paths()
        if folder and folder not in current:
            self._save(current + [folder])

    def _remove(self) -> None:
        row = self.list.currentRow()
        current = self.paths()
        if 0 <= row < len(current):
            self._save(current[:row] + current[row + 1:])
