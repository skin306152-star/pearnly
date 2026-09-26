"""Narrow internal projection of the authoritative push ledger.

Caller holds Connection -> History -> writer -> Record locks. No authorization
upgrade, review edit, commit or external IO; old issued receipts survive revoke.
"""
import frappe
from frappe import _

FIELDS = ('last_push_status', 'last_pushed_at', 'last_push_record', 'last_push_reason')


def mirror(row, timestamp, *, select=False):
    if row.status not in {'pending', 'success', 'manual'}:
        return
    found = frappe.db.sql("""SELECT workspace,client,last_push_record,last_push_status
        FROM `tabCowork OCR History` WHERE name=%s FOR UPDATE""", (row.history,), as_dict=True)
    if not found or (found[0].workspace, found[0].client or '') != (row.workspace, row.client or ''):
        frappe.throw(_('Not Permitted'), frappe.PermissionError)
    previous = found[0]
    # A late result for another account/connection must not replace the push
    # the user selected more recently. Its own ledger receipt remains visible.
    if not select and previous.last_push_record not in (None, '', row.name):
        return
    if previous.last_push_record == row.name and previous.last_push_status == row.status:
        return
    # 转人工的原因跟着状态走（列表状态图标悬浮看原因，用户 2026-09-26）；其它状态清掉旧原因。
    frappe.db.set_value('Cowork OCR History', row.history, {
        'last_push_status': row.status, 'last_pushed_at': timestamp,
        'last_push_record': row.name,
        'last_push_reason': (row.reason or '')[:500] if row.status == 'manual' else ''}, update_modified=False)


def record_failure(history, reason):
    """还没进账本就失败（目标不可用、目录证据失效、推送未开启……）：只记原因，不改推送状态。

    推送状态只由账本投影；这里写的原因让列表状态图标能显示「失败 + 原因」，下次推成功时由 mirror 清掉。
    调用方已核过这张票的读取范围。
    """
    frappe.db.set_value('Cowork OCR History', history, 'last_push_reason', str(reason or '')[:500],
                        update_modified=False)
