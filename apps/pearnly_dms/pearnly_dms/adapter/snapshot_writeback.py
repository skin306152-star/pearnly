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


def fill_customer(endpoint, customer_id):
    """客户索引里有、完整资料还没读到的客户：单独读这一条写回本地（约 1 秒）。成功返回 True。

    首次读全档期间（ris 客户详情约 69 分钟）操作员查到老客户时用，不等整组读完。"""
    from pearnly_dms.adapter.operations import run_logged_in

    scope = capture_scope(endpoint)
    if scope is None:
        return False

    def work(client, adapter):
        fields = read_customer_complete(client, str(customer_id))
        store.verified_customer(endpoint, str(customer_id), fields, expected_scope=scope)
        return {"ok": True}

    result = run_logged_in(endpoint, work, authoritative_read=True)
    if not result.get("ok"):
        logger.warning("DMS customer detail fill failed: %s", result.get("error_code"))
    return bool(result.get("ok"))


#: 一次查询里最多补读几条（相似姓名的候选也要各自的详情）。
FILL_LIMIT = 10


def with_customer_detail(endpoint, read):
    """`read()` 碰到"索引里有、详情还没读到"的客户时，补读那一条再做一次；补不到照旧报"资料读取中"。"""
    from pearnly_dms.snapshots.directory import CustomerDetailPending

    for _ in range(FILL_LIMIT):
        try:
            return read()
        except CustomerDetailPending as pending:
            if not fill_customer(endpoint, pending.customer_id):
                raise
    return read()


def sales(endpoint):
    """Compatibility only: a booking never starts a full employee scan."""
    return False
