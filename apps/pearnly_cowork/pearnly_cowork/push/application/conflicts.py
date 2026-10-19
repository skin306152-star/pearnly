"""人发起的推送在数据库冲突时整笔重来（10-20 lab：待处理页连着推、测试机 CC 桌面版同时领单写账，5 张直接报
「Record has changed since last read in table 'tabCowork Push Writer Lock'」，隔几秒再点都成）。

站点库 MariaDB 11.8 开着快照隔离：一笔事务先做过一致性读（建立读视图），之后加锁读 / 改的行在这之后被别的事务改过并提交
（CC 桌面版领单 / 回执改写者锁行的 ``holder``），就报 1020（``ER_CHECKREAD``）；互相等锁报 1213（``ER_LOCK_DEADLOCK``）。
Frappe 把这两个码都抛成 ``frappe.QueryDeadlockError``（`frappe.database.mariadb.database.is_deadlocked`，线上镜像核过）。

为什么整笔回滚而不是回到保存点：1213 时 InnoDB 已经回滚了整笔事务；1020 要的是新的读视图，回到保存点仍在原来那笔事务、读视图
不变，同一个加锁读会再撞一次。所以只在调用方自己的事务边界上用（白名单入口、或像 `assistant.tools._push_one` 那样每一步自己
提交的编排），重来之前那一段要么已提交、要么只是读；内部组合调用（定时任务、扫描在更大的事务里调用 service）不得经这里，
免得把别人的改动一起回滚（架构「RPC 只在无外部 IO 的本地事务发生数据库冲突时有界重试」）。

重来不改语义：被回滚的那次什么都没留下（推送记录、确认回执、提交后的实时事件与 MR.ERP 执行任务都随事务撤掉，入队只在提交后），
重来照同一请求号走，推送记录按请求号唯一（`push.native.service.reserve` 的 ``request_digest``、记录名 ``CWP-<请求号>``），
确认按 ``confirmation_request`` 回放，同一张单不会多出一条记录、不会排两次。

次数与退避：一共 :data:`ATTEMPTS` 次，第 n 次重来前等 :data:`BACKOFF` [n-1] 秒、乘 0.5–1.5 的随机系数（两个人同时点不再同时撞）。
依据：3 租户 × 10 台压测（多电脑记录第 7 批后续）领单服务端 p50 约 100 ms、p95 约 200 ms，写者锁行在一次领单 / 回执里只改一次；
先等 0.1–0.4 秒让对面那笔提交、错开同一台电脑「回执 → 紧接着领下一张」的连续两次改动，合计最多约 1 秒，短于 CC 桌面版 3 秒一次
的取单间隔。原先 3 次、不等，压测里 register 有过三连撞。用尽才交给人：正式原因 ``push.native_lock_conflict``（已有词条，
「另一笔推送正在占用写入锁」），不带 SQL 原文。等锁超时（1205，``QueryTimeoutError``）不在这里重来：已经等了整个锁等待时间。
"""
import random
import time

import frappe
from frappe import _

ATTEMPTS = 4
BACKOFF = (0.1, 0.2, 0.4)
JITTER = (0.5, 1.5)
EXHAUSTED = "push.native_lock_conflict"


class Exhausted(frappe.ValidationError):
    """重来 :data:`ATTEMPTS` 次仍冲突。是 ``ValidationError``：各入口原有的「失败记在票上」照常接住。"""


def retried(function, *args, **kwargs):
    """调 ``function``；撞上 1020 / 1213 时整笔回滚、退避后重来，最多 :data:`ATTEMPTS` 次，用尽抛 :class:`Exhausted`。
    别的错误原样抛出、不重来。"""
    messages = len(getattr(frappe.local, "message_log", None) or [])
    for attempt in range(ATTEMPTS):
        try:
            return function(*args, **kwargs)
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            _drop_messages(messages)  # 被回滚的那次弹过的话不带给重来的那次
        if attempt < ATTEMPTS - 1:
            time.sleep(BACKOFF[attempt] * random.uniform(*JITTER))
    # 在 except 外抛：不把数据库原文挂在异常链上
    frappe.throw(_(EXHAUSTED), Exhausted)


def _drop_messages(keep):
    log = getattr(frappe.local, "message_log", None)
    if log is not None and len(log) > keep:
        del log[keep:]
