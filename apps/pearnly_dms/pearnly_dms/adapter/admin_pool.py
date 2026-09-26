# -*- coding: utf-8 -*-
"""管理员（系统）账号池：多个操作员同时要用管理员会话时，各挑一个空闲的（2026-09-27）。

## 为什么

客户档写入、订车前复核、后台读取都借"管理员"会话，而本站所有操作员连接配的是**同一个**
管理员账号（通常就是老板自己的 DMS 账号）。DMS 一个账号同时只允许一处登录，所以这些
操作全在这一把账号锁上排队；几十人同时用时，排在后面的人会等满锁超时直接失败。

池子让系统多几个"座位"：在 DMS 后台正常新建几个账号，在连接页登记（先真登一次再存），
需要管理员会话时按轮转顺序试每个账号的锁，谁空用谁；都不空才排队等其中一个。

## 规则

* 连接自己配的管理员账号永远是池里的第一个成员 —— 池子是空的时，行为与改动前逐字节一致。
* 只登记本站这套 DMS 的账号；和销售账号同名的跳过（同一账号不能同时当两种会话）。
* 池里的账号需要与连接的管理员账号**同等权限**（能改客户档、能看全部主档），
  这由 DMS 那边建账号时决定；登记时的真登只能证明能登录，证明不了权限。
"""

from __future__ import annotations

import logging
from typing import List, NamedTuple, Optional

import frappe

logger = logging.getLogger(__name__)

DOCTYPE = "DMS System Account"
_ROTATE_KEY = "pearnly:dms:admin-pool:next"


class Account(NamedTuple):
    username: str
    password: str
    pooled: bool  # True = 来自池子（登记表），False = 连接自己配的管理员


def members(default_username: Optional[str], default_password: Optional[str],
            *, exclude: str = "") -> List[Account]:
    """池成员：连接自带的管理员在前，其后是登记表里启用的账号（去重、排除销售账号）。"""
    out: List[Account] = []
    seen = set()
    if default_username and default_password:
        out.append(Account(default_username, default_password, False))
        seen.add(default_username)
    try:
        rows = frappe.get_all(DOCTYPE, filters={"enabled": 1}, fields=["name", "username"],
                              order_by="creation asc")
    except Exception:
        rows = []  # 表还没迁移出来 / 读不到：退回单账号，不影响原有行为
    if rows:
        from pearnly_core.endpoints import read_password

        for row in rows:
            username = (row.username or "").strip()
            if not username or username in seen or username == exclude:
                continue
            password = read_password(DOCTYPE, row.name, "password")
            if not password:
                continue
            out.append(Account(username, password, True))
            seen.add(username)
    return out


def rotated(accounts: List[Account]) -> List[Account]:
    """按轮转起点排列，避免每次都先抢第一个账号。Redis 不可用就按原顺序。"""
    if len(accounts) < 2:
        return list(accounts)
    try:
        start = int(frappe.cache().incr(_ROTATE_KEY) or 0) % len(accounts)
    except Exception:
        start = 0
    return accounts[start:] + accounts[:start]


def note_status(username: str, status: str) -> None:
    """把登录结果记回登记表（只记池子里的账号；失败不影响业务）。"""
    try:
        name = frappe.db.get_value(DOCTYPE, {"username": username}, "name")
        if name:
            frappe.db.set_value(DOCTYPE, name, {"last_status": status[:140],
                                                "last_checked_at": frappe.utils.now_datetime()},
                                update_modified=False)
    except Exception:
        logger.debug("[dms admin pool] 状态回写失败", exc_info=True)
