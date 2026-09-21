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
    return _local_transaction(_confirm, name, expected_revision, request_key)
