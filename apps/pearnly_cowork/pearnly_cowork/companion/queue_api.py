"""Two exact POST machine methods, with durable commit before returning data.

No external write is executed. The private HTTP capability is required even for
Administrator. Native Frappe's message envelope is retained; old Windows clients
need explicit transport adaptation and are not silently switched to these URLs.
"""
import frappe

from pearnly_core.transaction import conflicts

from pearnly_core.cc_desktop.wire import queue_contracts

from . import http_auth, transport


def _run(operation):
    context, body = http_auth.current_request(operation)
    service = transport.acknowledge if operation == 'ack' else transport.lease
    frappe.local.cowork_private_response = True
    try:
        # Only local pre-commit work may be retried (`transaction.conflicts`: 1020 / 1213, whole
        # rollback, backoff). The service rechecks its distinct current-dispatch or
        # historical-receipt capability on every attempt.
        result = conflicts.retried(service, context, body)
    except conflicts.RollbackFailed as error:
        # A failed rollback carries no original receipt traceback (which can contain its
        # decrypted attempt secret). Recovery is uncertain: stop, never run another lease/ACK.
        http_auth.raise_busy(error, stage=operation + '_rollback')
    except conflicts.Exhausted as error:
        http_auth.raise_busy(error, stage=operation)
    except frappe.AuthenticationError:
        raise
    except frappe.PermissionError:
        http_auth._deny_permission()
    except queue_contracts.QueueContractError as error:
        http_auth.reject_request(error.code)
    except frappe.ValidationError:
        http_auth.reject_request('companion.queue_conflict', conflict=True)
    except Exception as error:
        http_auth.raise_busy(error, stage=operation)
    # Frappe's final sync_database occurs outside its exception-handler try.
    # Commit inside this boundary so failures cannot emit an uncommitted job.
    # An after_commit failure may mean rows are already durable: never retry the
    # service or claim that rollback proves the write did not happen.
    try:
        frappe.db.commit()
    except Exception as error:
        http_auth.raise_busy(error, stage=operation + '_commit')
    http_auth.clear_private_messages()
    return result


@frappe.whitelist(methods=['POST'])
def lease(**ignored):
    return _run('lease')


@frappe.whitelist(methods=['POST'])
def acknowledge(**ignored):
    return _run('ack')
