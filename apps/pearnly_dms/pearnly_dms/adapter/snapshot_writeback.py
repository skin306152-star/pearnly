"""Best-effort cache of verified external writes, never a second business write."""
import logging

from pearnly_dms.adapter.admin_read import authoritative_read_session
from pearnly_dms.client.snapshot_reader import read_customer_complete
from pearnly_dms.snapshots import store

logger = logging.getLogger(__name__)


def capture_scope(endpoint):
    try:
        return store.scope(endpoint)
    except Exception:
        return None


def customer(endpoint, client, customer_id, *, expected_scope):
    # Capture credentials before readback, then fence publication. This helper is
    # called only after the writer's own verification succeeded.
    try:
        if expected_scope is None:
            return False
        with authoritative_read_session(client):
            fields = read_customer_complete(client, str(customer_id))
        store.verified_customer(endpoint, str(customer_id), fields, expected_scope=expected_scope)
        return True
    except Exception:
        # A committed external operation must not become a retryable write due
        # to an optional cache failure. Do not log customer data/exception HTML.
        logger.warning("DMS verified customer cache update failed")
        return False


def sales(endpoint):
    """Compatibility only: a booking never starts a full employee scan."""
    return False
