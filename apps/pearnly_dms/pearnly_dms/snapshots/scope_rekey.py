# -*- coding: utf-8 -*-
"""一次性迁移（2026-09-27）：本地快照的缓存键去掉密码后，把现有资料挂到新键上。

不迁移的话，上线那一刻所有租户的缓存键都会变，四组资料等于全部作废、要重读一遍
（ris 客户组一次一个多小时）。这里按每条 DMS 连接当前的账号与密码算出旧键和新键，
把旧键下的所有行（四组资料、变更水位、读取请求、认证记号）原样改挂到新键。

幂等：旧键下已经没有行（迁过了）就什么都不做；新键下已有同名行时保留新行、不覆盖。
只动本地数据库，不登录 DMS。
"""
import frappe

from pearnly_core.endpoints import read_password
from pearnly_dms.snapshots import store


def execute():
    moved = 0
    for row in frappe.get_all("Pearnly ERP Endpoint", filters={"adapter": "mrerp_dms"},
                              fields=["name", "system_url", "username", "admin_username", "owner_user"]):
        for reader in ("authority", "sales"):
            for old, new in _keys(row, reader):
                moved += _rekey(old, new)
    if moved:
        frappe.db.commit()


def _keys(row, reader):
    admin_pw = read_password("Pearnly ERP Endpoint", row.name, "admin_password") if reader == "authority" else ""
    use_admin = bool(row.admin_username and admin_pw)
    username = row.admin_username if use_admin else row.username
    password = admin_pw if use_admin else read_password("Pearnly ERP Endpoint", row.name, "password")
    if not username or not password:
        return []
    sales = None
    if reader == "sales":
        modified = frappe.db.get_value("DMS Operator", {"user": row.owner_user}, "modified") if row.owner_user else None
        if not modified:
            return []
        sales = (row.name, row.owner_user, str(modified))
    old = store._scope_hash(store.scope_identity(row.system_url, username, reader, sales=sales, password=password))
    new = store._scope_hash(store.scope_identity(row.system_url, username, reader, sales=sales))
    return [(old, new)] if old != new else []


def _rekey(old, new):
    moved = 0
    for item in frappe.get_all(store.DT, filters={"scope_key": old}, fields=["name", "dataset"]):
        target = store._name(new, item.dataset)
        if frappe.db.exists(store.DT, target):
            continue
        frappe.db.sql(f"UPDATE `tab{store.DT}` SET name=%s, scope_key=%s WHERE name=%s",
                      (target, new, item.name))
        moved += 1
    return moved
