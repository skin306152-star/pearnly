"""Dedicated RPCs; there is no browser snapshot-install or credential-read API."""
import frappe
from frappe import _
from . import model, service


def _call(method, *args, **kwargs):
    # This RPC owns only its local transaction. Internal callers use service
    # directly; rolling back their larger transaction here would be incorrect.
    for attempt in range(3):
        try:
            return method(*args, **kwargs)
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            if attempt == 2:
                raise
        except model.ConnectionError as error:
            frappe.throw(_(error.code))


@frappe.whitelist(methods=["POST"])
def configure(workspace, adapter, request_key, label, config, client=None, users=None,
              name=None, expected_revision=None, enabled=True, credentials=None):
    return _call(service.configure, workspace, adapter, request_key, label, config, client,
                 users, name, expected_revision, enabled, credentials)


@frappe.whitelist()
def detail(name):
    return _call(service.detail, name)


@frappe.whitelist()
def targets(name):
    return _call(service.targets, name)


@frappe.whitelist()
def records(workspace, client=None, start=0, limit=50):
    return _call(service.records, workspace, client, start, limit)


@frappe.whitelist(methods=["POST"])
def request_refresh(name, request_key, account_set_key=None):
    return _call(service.request_refresh, name, request_key, account_set_key)


@frappe.whitelist()
def refresh_status(name, request_id):
    return _call(service.refresh_status, name, request_id)
