"""Dedicated service authorization; generic native CRUD never grants access."""
import frappe
from frappe import _
from pearnly_core.shell import entitlements
from pearnly_cowork.masters import workspaces
from . import model

TYPES = ("Cowork ERP Connection", "Cowork ERP Snapshot", "Cowork ERP Refresh")
# 多台电脑的账套登记、可达与账套资料（ADR-021，`pool`）：同一套只经本域写入的守卫。
POOL_TYPES = ("Cowork Express Account Set", "Cowork Express Reach", "Cowork Express Account Facts")
_TOKEN = object()


def deny():
    frappe.throw(_("Not Permitted"), frappe.PermissionError)


def native_permission(doc=None, ptype=None, user=None, **kwargs):
    deny()


def native_query(user=None):
    # All/Guest read ensures this is reached before the legacy shares-only path.
    deny()


def reject_share(doc, method=None):
    if doc.share_doctype in TYPES or doc.share_doctype in POOL_TYPES:
        deny()


def guard(doc):
    if doc.flags.cowork_connection_write is not _TOKEN:
        deny()


def write(doc):
    doc.flags.cowork_connection_write = _TOKEN
    doc.flags.ignore_permissions = True
    doc.flags.notify_update = False
    doc.flags.ignore_version = True
    return doc.insert(ignore_permissions=True) if doc.is_new() else doc.save(ignore_permissions=True)


def _current(query, values, lock=True):
    # Writers: current reads, including when the caller already established an RR view, held until commit so a
    # concurrent revocation cannot slip in before the write lands. Read-only views (`lock=False`, the connection
    # service's DTO views) read their own snapshot instead: MariaDB 11.8 snapshot isolation rejects a locking read of
    # a row changed after the view began (1020 on `tabUser` when `last_active` moved, testcowork 2026-10-15).
    return frappe.db.sql(query + (" LOCK IN SHARE MODE" if lock else ""), values, as_dict=True)


def roles(actor, *, lock=True):
    return {r.role for r in _current("SELECT role FROM `tabHas Role` WHERE parent=%s AND parenttype='User'", (actor,),
                                     lock)}


def require_actor(actor=None, *, management=False, lock=True):
    actor = actor or frappe.session.user
    feature = "master_data" if management else "intake"
    role = "Cowork Admin" if management else "Cowork Intake"
    found = _current("SELECT enabled FROM `tabUser` WHERE name=%s", (actor,), lock)
    if (not actor or actor == "Guest" or frappe.conf.get("pearnly_product") != "cowork"
            or feature not in entitlements() or not found or not found[0].enabled):
        deny()
    granted = roles(actor, lock=lock)
    if role not in granted and (management or "Cowork Admin" not in granted):
        deny()
    return actor


def require_scope(workspace, client=None, *, actor=None, lock=True):
    actor = actor or frappe.session.user
    rows = _current("SELECT enabled FROM `tabCowork Workspace` WHERE name=%s", (workspace,), lock)
    if not rows or not rows[0].enabled:
        deny()
    if client:
        rows = _current("SELECT enabled FROM `tabCowork Client` WHERE name=%s AND workspace=%s", (client, workspace),
                        lock)
        if not rows or not rows[0].enabled:
            deny()
    # 不按人分公司 / 客户（Cowork 不做公司隔离，用户 2026-09-26）；谁能用哪条连接看连接自己的名单。


def require_connection(doc, *, actor=None, management=False, enabled=True, lock=True):
    actor = require_actor(actor, management=management, lock=lock)
    require_scope(doc.workspace, doc.client, actor=actor, lock=lock)
    if enabled and not doc.enabled:
        frappe.throw(_("connection.disabled"), frappe.PermissionError)
    if not management:
        if actor not in model.parse(doc.allowed_users, []):
            deny()
    return actor


def usable_by(doc, actor):
    """``actor`` 此刻能不能用这条连接（录入或管理 + 使用名单，同 :func:`require_connection`）；不抛错。不加锁读自己的快照
    （MariaDB 11.8 快照隔离下只读请求的加锁读会撞 1020）。池里「能写的电脑」按它核签发人（`pool.writers`）。"""
    try:
        require_connection(doc, actor=actor, lock=False)
    except frappe.PermissionError:
        frappe.clear_messages()
        return False
    return True


def require_assignee(actor, workspace, client=None):
    found = _current("SELECT enabled FROM `tabUser` WHERE name=%s", (actor,))
    if (not actor or actor == "Guest" or not found or not found[0].enabled
            or not {"Cowork Intake", "Cowork Push Log", "Cowork Admin"}.intersection(roles(actor))):
        deny()
    require_scope(workspace, client, actor=actor)


def default_workspace() -> str:
    """本站点那一份内部归属（口径见 `pearnly_cowork.access.scope.default_workspace`）。

    连接域不能直接依赖 `access`（`docs/development/architecture-policy.json`），
    经允许依赖的 `masters`（归属行的写入方）取同一个实现。
    """
    return workspaces.site_workspace()
