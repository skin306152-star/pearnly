"""后台任务里回话 / 写提示用的语言（三个产品共用）。

锁定版 Frappe 的 ``_()`` 在后台任务里**第一次**不带 lang 调用时，按排任务那一刻的会话用户（``frappe.local.job.user``）
重设 ``frappe.local.lang``（``frappe.utils.translations._``）。LINE webhook 排的任务是 Guest、调度器续跑的是 Administrator，
于是产品在任务开头设好的语言（手机上 LINE 的语言、提交时页面的语言）会被冲成站点默认语言——9-27 ERP 识别续跑后整张卡变泰文、
9-30 对账失败提示变泰文、11-13 收款凭证后那张卡变泰文，都是这一个原因。

设语言一律走这里：同时告诉 Frappe「这一任务的语言已定」（``job.lang_resolved``），不在任务里就只设语言。
"""
from contextlib import contextmanager

import frappe


def settle(language) -> None:
    """定下当前语言（后台任务里不再被 Frappe 按排任务的用户重设）。``language`` 为空只标「已定」、不改语言。"""
    if language:
        frappe.local.lang = language
    job = getattr(frappe.local, "job", None)
    if job is not None:
        job.lang_resolved = True


@contextmanager
def use(language):
    """在这一段里用 ``language``，出来还原原来的语言与「已定」标记。``language`` 为空就沿用当前语言。"""
    previous = getattr(frappe.local, "lang", None)
    job = getattr(frappe.local, "job", None)
    resolved = getattr(job, "lang_resolved", None) if job is not None else None
    settle(language)
    try:
        yield getattr(frappe.local, "lang", None)
    finally:
        if previous is not None:
            frappe.local.lang = previous
        if job is not None and resolved is not None:
            job.lang_resolved = resolved
