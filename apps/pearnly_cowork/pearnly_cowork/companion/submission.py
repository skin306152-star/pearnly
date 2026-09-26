"""Explicit human confirmation; the only new action is a local pending record."""
import frappe
from frappe import _

from pearnly_cowork.push.application import service
from pearnly_cowork.push.application.api import _local_transaction
from . import credentials


def _confirm(name, expected_revision, request_key):
    connection = frappe.db.get_value('Cowork Push Record', name, 'connection')
    if not connection:
        frappe.throw(_('push.record_not_found'), frappe.DoesNotExistError)
    credential = credentials.for_confirmation(connection)
    return service.confirm(name, expected_revision, request_key, credential=credential)


@frappe.whitelist(methods=['POST'])
def confirm(name, expected_revision, request_key):
    """Express 确认发送。失败不抛给页面：原因记在票上，列表状态图标悬浮可看（用户 2026-09-26）。"""
    try:
        return _local_transaction(_confirm, name, expected_revision, request_key)
    except (frappe.ValidationError, frappe.PermissionError, frappe.AuthenticationError) as error:
        frappe.db.rollback()
        history = frappe.db.get_value('Cowork Push Record', name, 'history')
        if not history:
            raise
        return service.submission_failed(history, str(error))
