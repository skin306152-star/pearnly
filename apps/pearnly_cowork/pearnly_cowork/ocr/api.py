"""Native Desk-ready invoice RPCs; no frontend or external order writer."""
import frappe
from pearnly_core.execution.errors import Deferred, QueueFull
from pearnly_cowork.ocr.execution import service
from pearnly_cowork.ocr.engine.errors import EngineError
from pearnly_cowork.ocr.table_inputs import TableInputError
from pearnly_cowork.roundtrip.contracts import RoundtripParseError


@frappe.whitelist(methods=["POST"])
def submit(source_file, request_key, direction=None, posting_kind=None, payment=None,
           connection=None, account_set_key=None):
    for attempt in range(3):
        try:
            result = service.submit(source_file, request_key, direction=direction,
                                    posting_kind=posting_kind, payment=payment,
                                    connection=connection, account_set_key=account_set_key)
            return {"accepted": True, **result}
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            if attempt == 2:
                raise
        except (Deferred, QueueFull) as exc:
            frappe.db.rollback()
            return {"accepted": False, "code": exc.reason if isinstance(exc, Deferred) else "queue_full"}
        except (TableInputError, EngineError, RoundtripParseError) as exc:
            # Structured admission failures carry a translatable code only;
            # source cells, parser exceptions and configuration values stay private.
            frappe.db.rollback()
            return {"accepted": False, "code": exc.code}


@frappe.whitelist()
def detail(name):
    return service.detail(name)


@frappe.whitelist(methods=["POST"])
def retry_billing(name):
    return service.retry_billing(name)
