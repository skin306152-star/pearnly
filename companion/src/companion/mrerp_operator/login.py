# -*- coding: utf-8 -*-
"""MR.ERP 代录的本机登录（托盘进程；ADR-026 第 7 条、评估 §14.4）：``tomo_bridge`` 实操页「MR.ERP 登录」只存本机那一份走这里
（站点那份走 ``mrerp_authorize`` → ``companion.mrerp_login``）。小窗用 ``express_operator.login_dialog.ask``（同一个原生小窗，
标题 ``mrerp_login_title``），凭据目标名用 ``credentials.mrerp_target``（与第 10 步授权存的同一个名字，代录读得到）。
不经过网页（页面拿不到密码）、不写文件、不传服务器、不进日志；只在用到时导入。"""
from __future__ import annotations

from ..express_operator import credentials
from .session import credential_target


def ask_and_store(url: str, connection: str, lang: str = "zh", parent=None, *, asker=None) -> bool:
    """``asker``：谁来弹（``login_dialog.Ask``；托盘里是桥的 ``ask_login``，经 CC 窗口进程弹、置前）。"""
    from ..express_operator import login_dialog

    target = credential_target(url, connection)
    found = (asker or login_dialog.local_ask(parent))(lang, "mrerp_login_title", credentials.user_name(target))
    if found is None:
        return False
    credentials.store(target, *found)
    found = None
    return True


def stored() -> list:
    """这台电脑存了哪些 MR.ERP 连接的登录：只有连接号（``tomo_bridge`` 的 ``operator_state.mrerp_logins``）。"""
    names = (name for name, _note in credentials.targets(credentials.MRERP_PREFIX))
    return sorted({name.rsplit("/", 1)[1] for name in names if name.count("/") >= 3 and name.rsplit("/", 1)[1]})


def has(url: str, connection: str) -> bool:
    return credentials.exists(credential_target(url, connection))


def user_of(url: str, connection: str) -> str:
    """只回用户名（站点做账号租约用）；密码不出这个模块。"""
    return credentials.user_name(credential_target(url, connection))


def forget(url: str, connection: str) -> bool:
    """断开连接 / 移除电脑时一并删掉本机这一条。"""
    return credentials.forget(credential_target(url, connection))
