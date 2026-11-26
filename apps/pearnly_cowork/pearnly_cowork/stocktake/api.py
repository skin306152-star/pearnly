"""Read-only native stocktake RPCs (the product pages use ``desk_api``).

建盘点 / 记数 / 关闭 / 报告 / 导出那几个原生写接口一直没有调用方，11-05 删了
（docs/work/2026-11-05-drop-dead-endpoints.md）；页面与 CC 都走 ``desk_api`` / ``service``。
"""
import frappe
from pearnly_core.transaction import conflicts
from pearnly_cowork.stocktake import service


def _transaction(function, *args, **kwargs):
    # MariaDB can report snapshot conflict 1020 even after waiting for a task
    # lock. An RPC owns only this local transaction and can restart it, retaining
    # the caller's request/operation ID. Internal workflows call service directly
    # and remain responsible for their larger transaction boundary.
    return conflicts.attempt(function, *args, **kwargs)  # 两种冲突都整笔重来，用尽回「请稍后重试」（B ①）


@frappe.whitelist()
def records(workspace, start=0, limit=50):
    return service.records(workspace, start, limit)


@frappe.whitelist()
def detail(task_id):
    return _transaction(service.detail, task_id)


@frappe.whitelist()
def entries(task_id, start=0, limit=100):
    return _transaction(service.entries, task_id, start, limit)
