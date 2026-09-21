"""Human management RPCs only; exp_ tokens do not authenticate any RPC."""
import frappe

from . import credentials


def _local_transaction(function, *args, **kwargs):
    # after_request applies no-store/no-cache/nosniff. Set on errors and replays too, so
    # the one-time plaintext response cannot become a reusable HTTP artifact.
    frappe.local.cowork_private_response = True
    for attempt in range(3):
        try:
            return function(*args, **kwargs)
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            if attempt == 2:
                raise


@frappe.whitelist(methods=['POST'])
def issue(connection, request_key, reset=False, expected_generation=None):
    return _local_transaction(credentials.issue, connection, request_key, reset, expected_generation)


@frappe.whitelist(methods=['POST'])
def revoke(connection, request_key, expected_generation, reason=''):
    return _local_transaction(credentials.revoke, connection, request_key, expected_generation, reason)


@frappe.whitelist(methods=['GET'])
def status(connection):
    return _local_transaction(credentials.status, connection)
