"""本地短事务撞上数据库冲突时整笔重来（Cowork 各域共用，不依赖任何业务域；10-20 从 `push.application.conflicts` 搬来）。

站点库 MariaDB 11.8 开着快照隔离：一笔事务先做过一致性读（建立读视图），之后加锁读 / 改的行在这之后被别的事务改过并提交，
就报 1020（``ER_CHECKREAD``）；互相等锁报 1213（``ER_LOCK_DEADLOCK``）。Frappe 把这两个码都抛成 ``frappe.QueryDeadlockError``
（`frappe.database.mariadb.database.is_deadlocked`，线上镜像核过）。

为什么整笔回滚而不是回到保存点：1213 时 InnoDB 已经回滚了整笔事务；1020 要的是新的读视图，回到保存点仍在原来那笔事务、读视图
不变，同一个加锁读会再撞一次。所以只在调用方自己的事务边界上用（白名单入口、机器方法、或每一步自己提交的编排），重来之前那一段
要么已提交、要么只是读；内部组合调用（定时任务、扫描在更大的事务里调用 service）不得经这里，免得把别人的改动一起回滚
（架构「RPC 只在无外部 IO 的本地事务发生数据库冲突时有界重试」）。被回滚的那次什么都没留下：整笔回滚也清掉了它排在提交后的
回调与入队（Frappe ``db.rollback`` 重置 ``after_commit``）。

次数与退避：一共 :data:`ATTEMPTS` 次，第 n 次重来前等 :data:`BACKOFF` [n-1] 秒、乘 0.5–1.5 的随机系数（两边同时撞不再同时重来）。
依据：3 租户 × 10 台压测（多电脑记录第 7 批后续 D9）领单服务端 p50 约 100 ms、p95 约 200 ms，写者锁行在一次领单 / 回执里只改一次；
先等 0.1–0.4 秒让对面那笔提交、错开同一台电脑「回执 → 紧接着领下一张」的连续两次改动，合计最多约 1 秒，短于 CC 桌面版 3 秒一次
的取单间隔。原先 3 次、不等，压测里 register 有过三连撞。用尽抛 :class:`Exhausted`，不带数据库原文，各入口按自己原有的失败形状接住。
等锁超时（1205，``QueryTimeoutError``）不在这里重来：已经等了整个锁等待时间。
"""
import random
import time

import frappe

ATTEMPTS = 4
BACKOFF = (0.1, 0.2, 0.4)
JITTER = (0.5, 1.5)


class Exhausted(Exception):
    """重来 :data:`ATTEMPTS` 次仍冲突。不带数据库原文、不挂原异常；各入口照自己原有的失败形状接住。"""


class RollbackFailed(Exception):
    """撞上冲突后整笔回滚本身失败：这笔事务是什么状态说不清，停下、不再重来。不挂原异常（机器方法的原异常帧里可能有
    回执密钥），各入口照自己原有的失败形状接住。"""


def retried(function, *args, **kwargs):
    """调 ``function``；撞上 1020 / 1213 时整笔回滚、退避后重来，最多 :data:`ATTEMPTS` 次，用尽抛 :class:`Exhausted`；
    回滚本身失败抛 :class:`RollbackFailed`、不再重来。别的错误原样抛出、不重来。"""
    messages = len(getattr(frappe.local, "message_log", None) or [])
    flagged = _error_message()
    for attempt in range(ATTEMPTS):
        try:
            return function(*args, **kwargs)
        except frappe.QueryDeadlockError:
            try:
                frappe.db.rollback()
                rolled_back = True
            except Exception:
                rolled_back = False
            _drop_messages(messages, flagged)  # 被回滚的那次弹过的话不带给重来的那次
        if not rolled_back:
            raise RollbackFailed()  # 在 except 外抛：不挂原异常
        if attempt < ATTEMPTS - 1:
            time.sleep(BACKOFF[attempt] * random.uniform(*JITTER))
    # 在 except 外抛：不把数据库原文挂在异常链上
    raise Exhausted()


_UNSET = object()


def _error_message():
    flags = getattr(frappe, "flags", None)
    return flags.get("error_message", _UNSET) if flags is not None else _UNSET


def _drop_messages(keep, flagged):
    """去掉被回滚那次留下的两路原生提示：``message_log`` 里它追加的几条，与 ``frappe.flags.error_message``（回复里的
    ``_error_message``）还原成调用之前的样子（机器方法的那一次里可能带着回执密钥，10-20 lab `queue-http` 夹具核过）。"""
    log = getattr(frappe.local, "message_log", None)
    if log is not None and len(log) > keep:
        del log[keep:]
    flags = getattr(frappe, "flags", None)
    if flags is None:
        return
    if flagged is _UNSET:
        flags.pop("error_message", None)
    else:
        flags["error_message"] = flagged
