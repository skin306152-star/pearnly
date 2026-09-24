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
    """Request a native employee refresh; True does not mean it has finished."""
    try:
        from pearnly_dms.snapshots.schedule import collectors, enqueue
        requested = False
        for collector in collectors():
            if collector["reader"] == "sales":
                enqueue(endpoint, collector)
                # enqueue_after_commit returns None even when successfully
                # registered. Native job deduplication happens at dispatch.
                requested = True
        return requested
    except Exception:
        logger.warning("DMS post-write sales refresh dispatch failed")
        return False
