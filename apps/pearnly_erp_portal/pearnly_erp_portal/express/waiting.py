"""排队中的单此刻是不是在等 Express 关闭（有人开着这本账套的 Express，11-12）——只读，给网页显示用。

CC 桌面版回报「等账套」时，共用写账件（`Pearnly CC Job`）留在排队中、原因记 ``ACCOUNT_BUSY_LOCKED``；ERP 单据上照旧是
「排队中」、``ledger_reason`` 是空的（`push.job_changed` 排队中时清空，不改）。这里在读的时候问共用通道
（`cc_desktop.jobs.express_wait_many`：上次回报超过 90 秒不算），给在等的行多带一个只读字段，页面据此显示现成那句
``express.error.ACCOUNT_BUSY_LOCKED``。不存任何东西，任务名不带给页面。
"""
import frappe

from pearnly_core.cc_desktop import jobs

from pearnly_erp_portal.express import contracts

SHOWN = "express_open"  # 行的只读字段 ``ledger_waiting`` / 弹窗的 ``waiting`` 的值
FIELD = "ledger_waiting"


def of_job(state, job, moment=None) -> str:
    """一张单（或它的 Express 作废那一件）：状态是排队中、写账件此刻在等 Express 关闭 → ``express_open``，否则空串。"""
    if state != contracts.QUEUED or not job:
        return ""
    return SHOWN if job in jobs.express_wait_many([job], moment) else ""


def mark(doctype, rows, key="name", moment=None):
    """列表的一页（行带 ``ledger_state``）：此刻在等 Express 关闭的行多带只读的 ``ledger_waiting``，别的行不带。

    只问这一页里「排队中」的那几张：单据的写账件名一次查回、共用通道一次查完，不逐行查。``key`` 是行里指向 ``doctype`` 单据的
    那一栏（收银小票的账本状态在所在的日汇总上：``express_summary``）。"""
    queued = [row for row in rows or () if row.get("ledger_state") == contracts.QUEUED and row.get(key)]
    if not queued:
        return rows
    names = sorted({row.get(key) for row in queued})
    job_of = {row.name: row.ledger_job for row in frappe.get_all(
        doctype, filters={"name": ["in", names], "ledger_state": contracts.QUEUED}, fields=["name", "ledger_job"],
        limit_page_length=len(names)) if row.ledger_job}
    found = jobs.express_wait_many(list(job_of.values()), moment) if job_of else {}
    for row in queued:
        if job_of.get(row.get(key)) in found:
            row[FIELD] = SHOWN
    return rows
