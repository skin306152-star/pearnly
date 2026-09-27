"""Local preparation only; no generic dispatch, ACK or worker RPC."""
import frappe

from pearnly_cowork.push import account_target
from pearnly_cowork.push.application import service


def _local_transaction(function, *args, **kwargs):
    """Only dedicated RPCs retry whole local transactions, with no external IO.

    MariaDB can report 1020 while replacing an older RR view with a current
    permission read. Internal orchestration must call service directly so this
    retry cannot roll back unrelated work in a larger caller transaction.
    """
    for attempt in range(3):
        try:
            return function(*args, **kwargs)
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            if attempt == 2:
                raise


@frappe.whitelist(methods=["POST"])
def prepare(history, connection, request_key, expected_history_version, expected_connection_revision,
            account_set_key=None, catalog_request_id=None, catalog_revision=None, posting_kind=None):
    return _local_transaction(service.prepare, history, connection, request_key, expected_history_version,
        expected_connection_revision, account_set_key, catalog_request_id, catalog_revision, posting_kind)


@frappe.whitelist()
def detail(name):
    return _local_transaction(service.detail, name)


@frappe.whitelist()
def records(connection, start=0, limit=50):
    return _local_transaction(service.records, connection, start, limit)


@frappe.whitelist(methods=["POST"])
def cancel_prepared(name, expected_revision):
    return _local_transaction(service.cancel_prepared, name, expected_revision)


@frappe.whitelist(methods=["POST"])
def confirm(name, expected_revision, request_key):
    """MR.ERP：确认发送（服务端直连执行）。Express 的确认走 `companion.submission.confirm`。"""
    return _local_transaction(service.confirm_server, name, expected_revision, request_key)


@frappe.whitelist(methods=["POST"])
def submit(history, connection, request_key, expected_history_version, expected_connection_revision,
           account_set_key=None, catalog_request_id=None, catalog_revision=None, posting_kind=None):
    """复核页「提交」一张票：失败不抛，原因记在票上（列表状态图标悬浮可看）。"""
    return _local_transaction(service.submit, history, connection, request_key, expected_history_version,
        expected_connection_revision, account_set_key, catalog_request_id, catalog_revision, posting_kind)


@frappe.whitelist(methods=["POST"])
def set_account(name, account_set_key, expected_version):
    """编辑页换一张票的推送账套（见 `push/account_target.py`）。"""
    return _local_transaction(account_target.set_account, name, account_set_key, expected_version)
