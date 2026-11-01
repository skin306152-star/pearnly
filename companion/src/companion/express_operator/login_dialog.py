# -*- coding: utf-8 -*-
"""「授权登录」的原生小窗（托盘进程，Qt）：用户本人输一次 Express 用户名和密码，存进这台电脑的 Windows 凭据管理器
（``credentials``），之后 CC 自动登录。不经过网页（页面拿不到密码）、不写文件、不传服务器、不进日志。只在点「授权登录」时导入。"""
from __future__ import annotations

from . import credentials


def ask_and_store(lang: str = "zh", parent=None) -> bool:
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit

    from ..i18n import t

    dialog = QDialog(parent)
    dialog.setWindowTitle(t("op_login_title", lang))
    user, password = QLineEdit(dialog), QLineEdit(dialog)
    password.setEchoMode(QLineEdit.EchoMode.Password)
    form = QFormLayout(dialog)
    form.addRow(t("op_login_user", lang), user)
    form.addRow(t("op_login_password", lang), password)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, dialog)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted or not user.text() or not password.text():
            return False
        credentials.store(credentials.target("default"), user.text(), password.text())
        return True
    finally:
        password.clear()
        dialog.deleteLater()


def forget() -> bool:
    return credentials.forget(credentials.target("default"))


def stored() -> bool:
    return credentials.exists(credentials.target("default"))
