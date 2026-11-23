# -*- coding: utf-8 -*-
"""「授权登录」的原生小窗（托盘进程，Qt）：用户本人输一次用户名和密码，存进这台电脑的 Windows 凭据管理器
（``credentials``），之后 CC 自动登录。不经过网页（页面拿不到密码）、不写文件、不进日志。只在点「授权登录」时导入。

- Express：只存本机（``ask_and_store``）；
- MR.ERP（ADR-026 第 7 条）：``ask`` 只收下这一次输入、交给调用方（``mrerp_login``）——先经机器通道交给站点，站点收下了再存
  本机，两处是同一次输入。

谁来弹（11-23 用户：小窗落在 CC 后面看不到）：桥在托盘进程，托盘不是前台进程，Windows 不许它抢前台。所以托盘里一律经管道请
CC 窗口进程弹（``window_link.WindowHost.login``，同选文件夹），以 CC 窗口为父、模态、弹出时置前；``ask_and_store`` 收调用方给的
``ask``（桥传它的 ``ask_login``）。"""
from __future__ import annotations

from typing import Callable, Optional, Tuple

from . import credentials

# 收用户名和密码的那一下：(界面语言, 标题词条, 预填用户名) → (用户名, 密码) 或 None（取消）
Ask = Callable[[str, str, str], Optional[Tuple[str, str]]]


def ask(lang: str = "zh", parent=None, *, title_key: str = "op_login_title",
        user: str = "") -> Optional[Tuple[str, str]]:
    """弹小窗收用户名和密码；取消或有一项空着回 None。密码框用完即清。有父窗口时模态于它；弹出时置前、取得焦点。"""
    from PySide6.QtCore import Qt
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
    # 没装 Qt 自带译文：标准按钮的字按界面语言写，不然各语言都是英文
    buttons.button(QDialogButtonBox.StandardButton.Save).setText(t("btn_save", lang))
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(t("btn_cancel", lang))
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)
    if parent is not None:
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
    # 弹出时置前（同一进程里的父窗口在前台时才拿得到焦点：托盘里经窗口进程弹，见模块说明）
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    (user_box if not user else password).setFocus()
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted or not user_box.text() or not password.text():
            return None
        return user_box.text(), password.text()
    finally:
        password.clear()
        dialog.deleteLater()


def local_ask(parent=None) -> Ask:
    """在本进程弹（以 ``parent`` 为父）的 ``Ask``：CC 窗口进程、单进程与测试用。"""
    return lambda lang, title_key, user: ask(lang, parent, title_key=title_key, user=user)


def ask_and_store(lang: str = "zh", parent=None, *, asker: Optional[Ask] = None) -> bool:
    found = (asker or local_ask(parent))(lang, "op_login_title", "")
    if found is None:
        return False
    credentials.store(credentials.target("default"), *found)
    return True


def forget() -> bool:
    return credentials.forget(credentials.target("default"))


def stored() -> bool:
    return credentials.exists(credentials.target("default"))


def stored_user() -> str:
    """已授权的 Express 账户名（不是秘密；页面显示「谁的名义」用，直写的录入人见 ``writer_id``）；没授权回空串。"""
    return credentials.user_name(credentials.target("default"))
