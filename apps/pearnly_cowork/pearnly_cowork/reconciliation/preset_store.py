"""对账预设的存取（原生 DocType ``Cowork Recon Preset``）；规则口径见 ``presets``。

一个站点就是一个租户、只有一条工作区；预设在工作区内按「对账类型 + 范围 + 规则」唯一（范围见 ``presets.bank_scope``）。
能做这一类对账的人都能回答和修改（用户 2026-09-30 定），每条记下谁、何时、从哪个任务定的。只能经本模块读写：
通用的读、写、删、分享一律拒绝（``hooks.py``）。
"""
import hashlib
import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from pearnly_cowork.reconciliation import bank_recon_access, income_recon_access, presets, vat_check_access

DOCTYPE = "Cowork Recon Preset"
ACCESS = {"bank": bank_recon_access, "income": income_recon_access, "vat": vat_check_access}
_WRITE = object()


def deny(*args, **kwargs):
    frappe.throw(_("Not Permitted"), frappe.PermissionError)


def reject_share(doc, method=None):
    if doc.share_doctype == DOCTYPE:
        deny()


def _key(workspace, kind, scope, rule):
    return hashlib.sha256(json.dumps([workspace, kind, scope, rule], ensure_ascii=False).encode()).hexdigest()


def _require(workspace, kind):
    if kind not in ACCESS:
        deny()
    ACCESS[kind].require(workspace)


def rules(workspace, kind, scope):
    """这个范围已定的规则：``{规则: 值}``；没有范围（认不出账户）回空。读取不另查权限：调用方是已过权限的对账服务。"""
    if not scope:
        return {}
    rows = frappe.get_all(DOCTYPE, filters={"workspace": workspace, "recon_kind": kind, "scope": scope},
                          fields=["rule", "value"], ignore_permissions=True, limit_page_length=0)
    return {row.rule: row.value for row in rows if presets.valid(kind, row.rule, row.value)}


def under(workspace, kind, prefix):
    """某个前缀下全部范围已定的规则：``{范围: {规则: 值}}``（收入按公司税号取这家公司各科目的预设）。"""
    if not prefix:
        return {}
    rows = frappe.get_all(DOCTYPE, filters={"workspace": workspace, "recon_kind": kind, "scope": ["like", prefix + "%"]},
                          fields=["scope", "rule", "value"], ignore_permissions=True, limit_page_length=0)
    found = {}
    for row in rows:
        if row.scope.startswith(prefix) and presets.valid(kind, row.rule, row.value):
            found.setdefault(row.scope, {})[row.rule] = row.value
    return found


def remember(workspace, kind, scope, rule, value, task=""):
    """记下 / 改掉一条预设（同一范围同一规则只有一条）。"""
    _require(workspace, kind)
    if not scope or len(scope) > 140 or not presets.valid(kind, rule, value):
        frappe.throw(_("reconciliation.request_invalid"))
    name = _key(workspace, kind, scope, rule)
    frappe.db.get_value("Cowork Workspace", workspace, "name", for_update=True)
    doc = (frappe.get_doc(DOCTYPE, name, for_update=True) if frappe.db.exists(DOCTYPE, name)
           else frappe.get_doc({"doctype": DOCTYPE, "preset_key": name, "workspace": workspace, "recon_kind": kind,
                                "scope": scope, "rule": rule}))
    doc.update({"value": value, "set_by": frappe.session.user, "set_at": now_datetime()})
    if task:
        doc.source_task = str(task)[:140]
    _persist(doc)
    return name


def forget(workspace, kind, scope, rule):
    """删掉一条预设：以后同类情况重新问。"""
    _require(workspace, kind)
    name = _key(workspace, kind, scope, rule)
    if frappe.db.exists(DOCTYPE, name):
        frappe.db.get_value(DOCTYPE, name, "name", for_update=True)
        # 删除时框架重新取文档，对象上的标记带不过去：删除期间在本请求的标记上放同一个对象。
        frappe.flags.cowork_preset_delete = _WRITE
        try:
            frappe.delete_doc(DOCTYPE, name, ignore_permissions=True, force=True)
        finally:
            frappe.flags.cowork_preset_delete = None


def listing(workspace, kind):
    """这一类对账的全部预设，最近定的在前。"""
    _require(workspace, kind)
    rows = frappe.get_all(DOCTYPE, filters={"workspace": workspace, "recon_kind": kind},
                          fields=["scope", "rule", "value", "source_task", "set_by", "set_at"],
                          order_by="set_at desc", ignore_permissions=True, limit_page_length=500)
    return [{**row, "scope_label": presets.scope_label(row.scope), "set_at": str(row.set_at or ""),
             "set_by_name": frappe.utils.get_fullname(row.set_by) if row.set_by else ""}
            for row in rows if presets.valid(kind, row.rule, row.value)]


def _persist(doc):
    doc.flags.cowork_preset_write = _WRITE
    doc.flags.ignore_permissions = True
    doc.flags.ignore_version = True
    return doc.insert(ignore_permissions=True) if doc.is_new() else doc.save(ignore_permissions=True)


class ReconPresetDocument(Document):
    """只接受本模块的写入（对象标记伪造不了）；范围、规则、工作区写定后不改。"""

    def _require_service(self):
        if self.flags.get("cowork_preset_write") is not _WRITE:
            deny()

    def autoname(self):
        self._require_service()
        self.name = self.preset_key

    def before_insert(self):
        self._require_service()

    def before_validate(self):
        self._require_service()

    def validate(self):
        self._require_service()
        previous = self.get_doc_before_save()
        if previous and any(self.get(field) != previous.get(field)
                            for field in ("preset_key", "workspace", "recon_kind", "scope", "rule")):
            deny()

    def on_trash(self):
        if frappe.flags.get("cowork_preset_delete") is not _WRITE:
            deny()

    def before_rename(self, *args, **kwargs):
        deny()
