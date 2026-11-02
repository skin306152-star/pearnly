# -*- coding: utf-8 -*-
"""「授权登录」的原生小窗（托盘进程，Qt）：用户本人输一次用户名和密码，存进这台电脑的 Windows 凭据管理器
（``credentials``），之后 CC 自动登录。不经过网页（页面拿不到密码）、不写文件、不进日志。只在点「授权登录」时导入。

- Express：只存本机（``ask_and_store``）；
- MR.ERP（ADR-026 第 7 条）：``ask`` 只收下这一次输入、交给调用方（``mrerp_login``）——先经机器通道交给站点，站点收下了再存
  本机，两处是同一次输入。"""
from __future__ import annotations

from typing import Optional, Tuple

from . import credentials


def ask(lang: str = "zh", parent=None, *, title_key: str = "op_login_title",
        user: str = "") -> Optional[Tuple[str, str]]:
    """弹小窗收用户名和密码；取消或有一项空着回 None。密码框用完即清。"""
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit

    from ..i18n import t

    dialog = QDialog(parent)
    dialog.setWindowTitle(t(title_key, lang))
    user_box, password = QLineEdit(dialog), QLineEdit(dialog)
    user_box.setText(user or "")
    password.setEchoMode(QLineEdit.EchoMode.Password)
    form = QFormLayout(dialog)
    form.addRow(t("op_login_user", lang), user_box)
    form.addRow(t("op_login_password", lang), password)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, dialog)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted or not user_box.text() or not password.text():
            return None
        return user_box.text(), password.text()
    finally:
        password.clear()
        dialog.deleteLater()


def ask_and_store(lang: str = "zh", parent=None) -> bool:
    found = ask(lang, parent)
    if found is None:
        return False
    credentials.store(credentials.target("default"), *found)
    return True


def forget() -> bool:
    return credentials.forget(credentials.target("default"))


def stored() -> bool:
    return credentials.exists(credentials.target("default"))
