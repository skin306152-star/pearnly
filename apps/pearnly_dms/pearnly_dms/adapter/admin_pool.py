# -*- coding: utf-8 -*-
"""本系统专用的 DMS 账号：一个读档账号 + 若干改写账号（用户 2026-09-27 定）。

## 为什么

DMS 一个账号同时只能有一处会话（2026-09-27 测试机实测：页面/表单流程会以
「มีผู้เข้าใช้งานในระบบซ้ำ」把重复登录踢下线）。以前客户档写入、后台读取、白天同步全借连接
自己的管理员账号（通常是有人在网页上用的那个），系统一直占着它，网页上的人就会被踢；
几十人同时操作时也全在这一把账号锁上排队。

所以在 DMS 后台另建几个只给本系统用的账号，按用途分开（连接页上只有这两种，用户 2026-09-27）：

* **读档账号（一个）**：就是本站连 DMS 用的账号（`connections.api.set_read_account` 同时写进公司连接）。
  后台读取全用它：每分钟同步、整组重读、首次读全档、凌晨回读（`operations.background_read_account`）。
  没登记读档账号的老连接，白天不做每分钟同步，凌晨照旧用公司连接的账号读。
* **改写账号（若干）**：操作员路径上需要管理员会话的事（客户档建/改、权威读取）。登记了改写账号，
  就只用它们；按轮转顺序试锁，谁空用谁，都忙才排队等一个。一个都没登记时，用公司连接复制到操作员
  连接上的那组（即读档账号）。

读档账号不进改写池，操作员永远不会占到它；两类账号都要求在登记时真登并按用途试读（`account_probe`）。
"""

from __future__ import annotations

import logging
from typing import List, NamedTuple, Optional

import frappe

logger = logging.getLogger(__name__)

DOCTYPE = "DMS System Account"
READ, WRITE = "read", "write"
_ROTATE_KEY = "pearnly:dms:admin-pool:next"


class Account(NamedTuple):
    username: str
    password: str
    pooled: bool  # True = 来自池子（登记表），False = 连接自己配的管理员


def members(default_username: Optional[str], default_password: Optional[str],
            *, exclude: str = "") -> List[Account]:
    """需要管理员会话时可用的账号：登记了改写账号就只用它们，否则只用连接自己的管理员账号。"""
    pooled = _registered(WRITE, exclude=exclude)
    if pooled:
        return pooled
    if default_username and default_password and default_username != exclude:
        return [Account(default_username, default_password, False)]
    return []


def read_account(*, exclude: str = "") -> Optional[Account]:
    """登记且启用的读档账号；没有就是 None。"""
    accounts = _registered(READ, exclude=exclude)
    return accounts[0] if accounts else None


def _registered(purpose: str, *, exclude: str = "") -> List[Account]:
    try:
        rows = frappe.get_all(DOCTYPE, filters={"enabled": 1, "purpose": purpose},
                              fields=["name", "username"], order_by="creation asc")
    except Exception:
        rows = []  # 表或字段还没迁移出来 / 读不到：当作没登记，行为与改动前一致
    out: List[Account] = []
    if not rows:
        return out
    from pearnly_core.endpoints import read_password

    seen = set()
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
        logger.debug("[dms admin pool] status write-back failed", exc_info=True)
