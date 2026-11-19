"""结束调用方的读快照之前，核对调用方没有没提交的写（多产品共用，不依赖任何业务域；11-19 回滚审计）。

站点库开着快照隔离（见 `conflicts`）：一些函数开头先 ``frappe.db.rollback()``，丢掉调用方的旧快照，下一句从当前读开始
（作废允许卡、抢占、挂着等的结论、写账前置判定「只是看」……）。它们都假设「调用方此前只读」。调用方其实写过而没提交时，
这一下回滚把调用方的写整笔冲掉，而且不报错（11-19：点停止写下的停止时刻、任务上的 stop、结束引导，都被作废允许卡开头的回滚冲掉；
Cowork 判卡片前落到单据上的分组被「只是看」的回滚冲掉）。

所以这类函数一律先经这里：

- :func:`require_clean`：调用方有没提交的写（Frappe 自己数的 ``frappe.db.transaction_writes``）→ 记一条点名 ``where`` 的
  错误日志并抛 :class:`PendingWrites`；不回滚、也不替调用方提交（替它提交会把本该一起成败的一组写拆成半截落库）。调用方在自己的
  边界上显式提交之后再调。没有就什么都不做。
- :func:`fresh`：:func:`require_clean` 之后回滚，开新的读视图。

不在这里的回滚（``except`` 里撤销自己这一步、保存点、函数自己写过又有意撤销）照旧直接调，由 `tests/test_snapshot_reset.py`
登记在案：新加不在 ``except`` 里的裸回滚不收，先经这里或登记理由。
"""
import frappe

TITLE = "Snapshot reset with pending writes"


class PendingWrites(Exception):
    """调用方有没提交的写时要结束读快照：先在调用方自己的边界上提交。"""


def pending() -> int:
    """这笔事务里还没提交的写语句条数（Frappe 自己数的：提交 / 回滚清零，INSERT / UPDATE / DELETE 各加一）。"""
    return int(getattr(getattr(frappe, "db", None), "transaction_writes", 0) or 0)


def require_clean(where: str) -> None:
    """调用方有没提交的写：记一条点名 ``where`` 的错误日志、抛 :class:`PendingWrites`（不回滚、不提交）。"""
    count = pending()
    if not count:
        return
    message = (f"{where}: {count} uncommitted write(s) from the caller before a snapshot reset; "
               "commit them at the caller's own boundary first")
    try:
        frappe.log_error(title=TITLE, message=message)
    except Exception:  # noqa: BLE001 — 记不下日志也照样拒绝
        pass
    raise PendingWrites(message)


def fresh(where: str) -> None:
    """结束调用方的读快照（之后从当前读开始）；调用方有没提交的写就拒绝（:func:`require_clean`），不随回滚丢掉。"""
    require_clean(where)
    frappe.db.rollback()
