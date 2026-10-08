"""MR.ERP 导入菜单与主档的服务端读取：登录、进选定的账套、打开三个导入页存下菜单选项，再读主档的可用值。

为什么要读（2026-09-28，MR.ERP 推送假成功）：导入菜单号是各公司自己的，哪几个在当前年度关联了日记账
只在导入页上看得到（规则见 `targets/mrerp_menus.py`）。录入提交前某类单据有多个已关联菜单、又没选过时，
要把选项给人选一次；推送时每次也会读导入页核对，读到的同样存回来（`push/mrerp/runner.py`）。
主档（部门 / 员工 / 科目等默认值，规则见 `targets/mrerp_masters.py`）同一次登录里读，给同一个选择弹窗用。

只读：不上传、不导入，MR.ERP 里什么都不改。与推送、目录采集共用同一把账号锁（老 PHP 单账号单会话，
并发登录会互踢）；对外请求期间不持任何行锁（读凭据一笔提交、存结果一笔提交）。
"""
from __future__ import annotations

import logging

import frappe
from frappe.utils.password import get_decrypted_password
from pearnly_core.session_lock import SessionLockUnavailable, account_lock
from pearnly_cowork.targets import mrerp_menus
from . import access, model, mrerp_master_reader, service, state
from .mrerp_session import MrErpSession, SessionError, lock_key

logger = logging.getLogger(__name__)

LOCK_WAIT_SECONDS = 120
JOB_TIMEOUT_SECONDS = 240
_DEFAULT_URL = "https://www.mrerp4sme.com"


def dispatch(request):
    """`service.request_mrerp_menu` 的申请落定后排一个读取任务；同一连接同一账套只排一个，提交后可见。"""
    frappe.enqueue("pearnly_cowork.connections.mrerp_menu_reader.run", queue="short",
                   job_id=f"cowork-mrerp-menus::{request['connection']}::{request['account_set_key']}",
                   deduplicate=True, enqueue_after_commit=True, timeout=JOB_TIMEOUT_SECONDS,
                   connection=request["connection"], account_set_key=request["account_set_key"],
                   actor=request["actor"])


def run(connection, account_set_key, actor):
    """后台任务入口：按申请人当前的授权读凭据 → 不持锁读 MR.ERP → 存结果。永不抛到队列。"""
    frappe.set_user(actor)
    try:
        doc = state.read(state.CONNECTION, connection)
        access.require_connection(doc, actor=actor)
        if doc.adapter != "mrerp" or not doc.enabled:
            frappe.db.rollback()
            return
        config = model.parse(doc.config_json, {})
        lease = {"system_url": str(config.get("system_url") or _DEFAULT_URL).strip(),
                 **{key: get_decrypted_password(state.CONNECTION, doc.name, "credential_" + key,
                                                raise_exception=False) or "" for key in ("username", "password")}}
    except frappe.PermissionError:
        frappe.db.rollback()
        return
    frappe.db.commit()
    pages, fields, error, master_error = read(lease, account_set_key)
    service.observe_mrerp_menus(connection, account_set_key, pages, error=error)
    service.observe_mrerp_masters(connection, account_set_key, fields, error=error or master_error)
    frappe.db.commit()


def read(lease, account_set_key):
    """→ ({导入页: 选项 | None}, {字段: 可用值}, 错误码, 主档错误码)；登录 / 选账套失败时前两项是 None。"""
    comidyear, _, seldb = str(account_set_key or "").partition(":")
    if not lease.get("username") or not lease.get("password"):
        return None, None, "ERR_NO_CREDS", None
    try:
        with account_lock(lock_key(lease["system_url"], lease["username"]), timeout_sec=LOCK_WAIT_SECONDS):
            session = MrErpSession(lease["system_url"], lease["username"], lease["password"])
            try:
                first = next(iter(mrerp_menus.PAGES.items()))
                session.select(comidyear, seldb, probe=f"{first[0]}/formupload.php?idmenu={first[1]}")
                pages = {}
                for path, idmenu in mrerp_menus.PAGES.items():
                    page = session.request("GET", f"{path}/formupload.php?idmenu={idmenu}",
                                           headers={"Referer": f"{session.base}/login/mainmenu.php"})
                    pages[path] = mrerp_menus.parse_options(page.text or "")
                fields, master_error = mrerp_master_reader.read_for_choices(session.request, session.base)
                return pages, fields, None, master_error
            finally:
                session.close()
    except SessionLockUnavailable:
        return None, None, "ERR_TECHNICAL", None
    except SessionError as exc:
        logger.info("MR.ERP menu read failed: %s", exc)
        return None, None, exc.code, None
    except Exception:  # noqa: BLE001 — 契约：读取失败只落错误码
        logger.exception("MR.ERP menu read raised")
        return None, None, "ERR_UNEXPECTED", None
