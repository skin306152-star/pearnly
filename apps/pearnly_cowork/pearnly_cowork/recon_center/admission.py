"""对账提交识别时等名额：每个租户同时待处理的识别任务有上限（共用层 tenant_pending，默认 50），名额在任务结束时释放。

对账一次要读的文件可能比上限多（小店一个月 70–100 张小票；2026-09-30 压测 V01 一次提交 72 张，超出上限整单报
「发票识别繁忙」，已读完的税报照片也白读了）。超出时先等已提交的读完、腾出名额再接着提交，不整单失败；等到时限
还没有名额才照旧报繁忙。等的时候不占进程（``waiting``）：本站点任何一份识别结束就按先来后到放一套等名额的对账接着跑。
"""
import frappe
from pearnly_core.execution.errors import QueueFull

from pearnly_cowork.recon_center import waiting

WAIT_SECONDS = 25 * 60


def when_room(create, deadline=None, key="room"):
    """``create()`` 碰到队列已满就等、再试；到 ``deadline``（epoch 秒，默认开始等后 ``WAIT_SECONDS``）还满就把 QueueFull
    抛出去。``key``：这一处等待的名字（同一套对账里每份文件各等各的，时限各自起算）。"""
    def evaluate(clock, now):
        try:
            return create()
        except QueueFull:
            limit = deadline if deadline is not None else clock.setdefault("entered", now) + WAIT_SECONDS
            if now > limit:
                raise
            frappe.db.commit()  # 结束读快照，看得到别的任务结束、名额释放
            raise waiting.Pending(due=limit, room=True)

    return waiting.settle(f"room:{key}", evaluate)
