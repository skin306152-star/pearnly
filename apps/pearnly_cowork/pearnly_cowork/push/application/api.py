"""Local preparation only; no generic dispatch, ACK or worker RPC."""
import frappe

from pearnly_cowork.push.application import conflicts, service


def _local_transaction(function, *args, **kwargs):
    """Only dedicated RPCs retry whole local transactions, with no external IO.

    MariaDB can report 1020 while replacing an older RR view with a current
    permission read, or 1213 on a deadlock. Internal orchestration must call
    service directly so this retry cannot roll back unrelated work in a larger
    caller transaction. Attempts, backoff and the final reason: `conflicts`.
    """
    return conflicts.retried(function, *args, **kwargs)


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
           account_set_key=None, catalog_request_id=None, catalog_revision=None, posting_kind=None,
           confirm_duplicate=None):
    """复核页「提交」一张票：失败不抛，原因记在票上（列表状态图标悬浮可看）。

    `confirm_duplicate`：会计在复核页点了「仍然推送」（Express 已有同号不同内容 / 上一版还在）。
    与 CC 桌面版领单写账撞上写者锁、重来用尽时同样不抛：原因记在票上（`conflicts`）。"""
    try:
        return _local_transaction(service.submit, history, connection, request_key, expected_history_version,
            expected_connection_revision, account_set_key, catalog_request_id, catalog_revision, posting_kind,
            confirm_duplicate=_flag(confirm_duplicate))
    except conflicts.Exhausted as error:
        return service.submission_failed(history, str(error))


def _flag(value):
    return str(value or "").strip().lower() in {"1", "true", "yes"}


@frappe.whitelist()
def choices(history, connection, account_set_key=None, posting_kind=None):
    """提交前要人选一次的项（对方档 / 存货 / 费用科目 / 付款 / 抵进项 / 部门）与可选值。"""
    return _local_transaction(service.choices_view, history, connection, account_set_key, posting_kind)


@frappe.whitelist(methods=["POST"])
def choose_goods_posting(history, value, connection=None, account_set_key=None):
    """复核页对「商品单入账方式」选「走库存 / 记费用」：这家公司以后的商品单都这样（`push.application.goods_posting`）。"""
    from pearnly_cowork.push.application import goods_posting

    return _local_transaction(goods_posting.choose, history, value, connection, account_set_key)


@frappe.whitelist(methods=["POST"])
def save_choices(history, connection, account_set_key, items):
    """记下会计选的，这份账套里同类以后自动套用。"""
    return _local_transaction(service.save_choices, history, connection, account_set_key, items)


def save_document_choices(history, connection, account_set_key, items):
    """CC 记下用户为这张票选的（`assistant.tools.save_push_choices`）：同 `save_choices`，另把为这张票选的现 / 赊写成这张票自己的
    付款方式（同复核页改付款方式），同一家供应商的几张票可以各是各的。只给服务端调用，不开网页接口。"""
    return _local_transaction(service.save_choices, history, connection, account_set_key, items, document_payment=True)


@frappe.whitelist(methods=["POST"])
def resolve_unknown(name, entered, docnum=None):
    """结果不明的推送：会计在 ERP 里核对后标「已入账」（可填 ERP 单号）或「未入账」（之后可重推）。"""
    return _local_transaction(service.resolve_unknown, name, _flag(entered), docnum or None)
