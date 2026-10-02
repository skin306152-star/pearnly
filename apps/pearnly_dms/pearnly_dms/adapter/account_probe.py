# -*- coding: utf-8 -*-
"""登记系统账号前按用途真试一次（用户 2026-09-27）：能登录不等于权限够。

* 读档账号：要能看 DMS 使用记录（每分钟同步靠它）、客户列表和单个客户资料（补读被改的客户）。
* 改写账号：要能打开"新建客户"表单（建/改客户档都从这张表单开始）。
* 操作员账号（`login`，用户 2026-10-02）：**只试能不能登录**，登上立即登出，不读不写任何资料，
  也不留会话缓存 —— 那是操作员本人的账号，DMS 同一账号会互相挤下线。

只读、不写 DMS；缺哪项就把那一项的名字回给界面。登录失败、连不上照旧走统一的错误话术。
从不抛异常。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Tuple

logger = logging.getLogger(__name__)

#: 任何一家 DMS 都有的模块（使用记录里按它计数）。
_EVENT_MODULE = "ลูกค้า"


def _can_read_events(client) -> bool:
    from pearnly_dms.client import eventlog_reader

    eventlog_reader.count(client, _EVENT_MODULE)
    return True


def _can_list_customers(client) -> bool:
    from pearnly_dms.adapter import session_cache

    body = client._post_text(session_cache._PROBE_PATH, session_cache._PROBE_DATA)
    return bool(session_cache._ALIVE.match(body or ""))


def _can_read_a_customer(client) -> bool:
    rows = client.search_customers_detailed("1", limit=1)
    if not rows:
        return True  # 一个客户都没有的新公司：没有可试的，不算缺权限
    return bool(client.read_customer(rows[0]["customer_id"]).get("name"))


def _can_open_customer_form(client) -> bool:
    return bool(client.list_geo("provinces"))


CHECKS: Dict[str, List[Tuple[str, Callable[[Any], bool]]]] = {
    "read": [("events", _can_read_events), ("customers", _can_list_customers),
             ("customer", _can_read_a_customer)],
    "write": [("customer_form", _can_open_customer_form)],
    "login": [],
}


def probe(system_url: str, username: str, password: str, purpose: str) -> Dict[str, Any]:
    """`{"ok": True}` / `{"ok": False, "missing": [...]}` / `{"ok": False, "error_code", "error_friendly"}`。"""
    from pearnly_dms.adapter.session import (
        DmsAuthError,
        DmsTechnicalError,
        DmsUnreachableError,
        MrerpDmsAdapter,
    )
    from pearnly_dms.adapter import push
    from pearnly_dms.line.errors import friendly

    def fail(code):
        return {"ok": False, "error_code": code, "error_friendly": friendly(code)}

    def inspect(adapter):
        if not CHECKS[purpose]:
            return {"ok": True, "missing": []}  # 只试登录：登上即可，不碰任何资料
        client = adapter.client()
        missing = []
        for name, check in CHECKS[purpose]:
            try:
                if not check(client):
                    missing.append(name)
            except Exception:
                logger.info("[dms account probe] %s check failed for a %s account", name, purpose,
                            exc_info=True)
                missing.append(name)
        return {"ok": not missing, "missing": missing}

    try:
        adapter = MrerpDmsAdapter(system_url=system_url, username=username, password=password, headless=True)
        adapter.verify_password = True
        adapter.sign_out_on_exit = purpose == "login"
        result = push.logged_in_probe(adapter, inspect)
        return result if result is not None else fail("ERR_DMS_CONCURRENT_LOGIN")
    except DmsAuthError:
        return fail("ERR_DMS_AUTH")
    except DmsUnreachableError:
        return fail("ERR_DMS_UNREACHABLE")
    except DmsTechnicalError:
        return fail("ERR_DMS_TECHNICAL")
    except Exception:
        logger.exception("[dms account probe] unexpected failure")
        return fail("ERR_UNEXPECTED")
