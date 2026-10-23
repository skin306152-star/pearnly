"""人发起的推送在数据库冲突时整笔重来（10-20 lab：待处理页连着推、测试机 CC 桌面版同时领单写账，5 张直接报
「Record has changed since last read in table 'tabCowork Push Writer Lock'」，隔几秒再点都成）。

机制（整笔回滚、次数、退避、为什么不回保存点、只在调用方自己的事务边界上用）在共用的 `pearnly_core.transaction.conflicts`，
CC 桌面版的电脑侧短事务也经它。这里只定人推用尽时给什么：正式原因 ``push.native_lock_conflict``（已有词条，
「另一笔推送正在占用写入锁」），不带 SQL 原文。

重来不改语义：被回滚的那次什么都没留下（推送记录、确认回执、提交后的实时事件与 MR.ERP 执行任务都随事务撤掉，入队只在提交后），
重来照同一请求号走，推送记录按请求号唯一（`push.native.service.reserve` 的 ``request_digest``、记录名 ``CWP-<请求号>``），
确认按 ``confirmation_request`` 回放，同一张单不会多出一条记录、不会排两次。
"""
import frappe
from frappe import _

from pearnly_core.transaction import conflicts as shared

EXHAUSTED = "push.native_lock_conflict"


class Exhausted(frappe.ValidationError):
    """重来用尽仍冲突。是 ``ValidationError``：各入口原有的「失败记在票上」照常接住。"""


def retried(function, *args, **kwargs):
    """经 `transaction.conflicts.retried` 调 ``function``；用尽抛 :class:`Exhausted`（正式原因）。别的错误原样抛出、不重来。"""
    try:
        return shared.retried(function, *args, **kwargs)
    except shared.Exhausted:
        pass
    # 在 except 外抛：不把数据库原文挂在异常链上
    frappe.throw(_(EXHAUSTED), Exhausted)
