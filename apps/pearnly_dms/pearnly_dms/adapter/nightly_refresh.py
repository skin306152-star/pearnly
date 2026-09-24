# -*- coding: utf-8 -*-
"""每日一次的全量回读（泰国凌晨 4–5 点），作为"顺手同步"之外的第二层保险。

## 为什么要有它

日常增量靠"操作动作顺手读使用记录"（`adapter/change_sync`）。那条路有三处已知的
覆盖盲区（都写在 change_sync 的文档里）：批量导入可能只记一条汇总事件、使用记录有保留
窗口、我们缓存里的次要表有几个在日志里找不到同名模块。**盲区一旦命中，缓存会一直停在
旧值上，没有任何东西会来纠正它。** 这个任务就是那个"没人管也会自己纠一次"的兜底：
每天凌晨把四组整份重读一遍，把增量漏掉的东西按 DMS 现状重新对齐。

## 为什么是这个时段、这个频率

· 全国地址是租户可编辑的字典，客户/车辆/公司也是天天在动，所以"永久拉一次"不成立；
· 但一次完整读取只要几十个请求、十来秒（列表端点），**没有任何理由频繁刷**：
  高频全量重扫会长时间占着第三方账号，那正是旧做法的病根（每 4 分钟一次，
  把操作员的实时操作和采集互相挡住，线上为此把它停掉了）；
· 泰国车行凌晨 4–5 点没人用，四组跑完也就几十秒，占账号的时间最短。

只排**两枪**：4:00 与 4:45。第二枪是失败重试——每组自己的数据若已经够新会被跳过，
所以第一枪成功之后第二枪什么都不做，不会重复读。

## 多租户

我们"一个租户一个站点"，而 Frappe 的调度是**按站点**跑的：每个站点自己的 cron 只处理
本站点的主连接。所以不需要（也不应该）写一个跨租户的循环。

时区：Frappe 的 cron 按**站点时区**求值（本站 Asia/Bangkok），`0 4 * * *` 就是泰国凌晨 4 点，
不依赖宿主容器时区。若某个站点的时区被设错，这条会在白天跑 —— 那会占用操作员正在用的
第三方账号，所以 `run` 里还有一道"最近有人用过就先让开"的护栏（见 `_busy_with_people`）。

## 已知风险（如实写下来，不假装没有）

1. **两个站点共用同一个 DMS 账号**时，两个站点的 4:00 会撞在同一条账号锁上：
   后到的最多等 180 秒，等不到就失败——4:45 那一枪会补上，且失败的站点保留上一代数据，
   不会留下半份脏数据。
2. 对方 DMS 夜里若在做维护，这一晚的读取会失败并记下错误码；老数据继续可用。
3. 这是一次**只读**的全量重读：不写对方任何东西，也不改变我们已发布的代际语义
   （整代成功才替换，失败保留旧代）。
"""
from __future__ import annotations

import time

import frappe

from pearnly_dms.snapshots import manual

ADAPTER = "mrerp_dms"
ENDPOINT = "Pearnly ERP Endpoint"
#: 服务自身的动作以谁的名义登记（Frappe 的 Administrator 具备全部角色）。
SERVICE_ACTOR = "Administrator"

#: 多久算"今天已经读过了"。取 20 小时：凌晨成功一次后，4:45 的第二枪会跳过；
#: 若第一次失败（数据仍是昨天或更早），第二枪照常重试。
FRESH_SECONDS = 20 * 3600

#: 一次全量回读要覆盖的组（与绑定时建底座的那四组一致）。
READ_ORDER = ("vehicles", "customers", "company", "geo")

#: 还在读的状态：这几种就不必再排一次。
IN_FLIGHT = ("queued", "running", "waiting")

#: "刚刚还有人在用这条连接"的窗口。时段设错的护栏用它（见 `_busy_with_people`）。
PEOPLE_WINDOW_SECONDS = 900


def _primary_endpoint():
    """本站启用的共享主连接（与连接页同一判据：没有 owner_user、且启用）。"""
    rows = frappe.get_all(ENDPOINT, filters={"adapter": ADAPTER, "owner_user": ["is", "not set"],
                                             "enabled": 1},
                          pluck="name", limit_page_length=2)
    return rows[0] if len(rows) == 1 else None


def _busy_with_people(endpoint):
    """最近有人真用过这条连接吗？用过就先让开，等下一枪。

    正常情况（站点时区正确、凌晨 4 点）这里永远返回 False。它是防"时区被设错导致
    这条在白天跑"的护栏：宁可今天不补，也不去抢操作员正在用的那个第三方账号。
    """
    try:
        from pearnly_dms.config import pacing

        return bool(pacing.used_recently(endpoint, within=PEOPLE_WINDOW_SECONDS))
    except Exception:
        return False


def run():
    """排一次四组全量回读。**不在这里登录第三方**（真正的读取由既有采集器在队列里做）。

    返回一句话摘要（进 scheduler 日志），不做任何业务写入。
    """
    endpoint = _primary_endpoint()
    if not endpoint:
        return {"skipped": "no-connection"}
    if _busy_with_people(endpoint):
        return {"skipped": "in-use-by-operator"}

    requested, skipped = [], {}
    for dataset in READ_ORDER:
        status = manual.status(endpoint, dataset, reader="authority")
        if status.get("state") in IN_FLIGHT:
            skipped[dataset] = "already-running"
            continue
        as_of = float(status.get("as_of") or 0)
        if as_of and time.time() - as_of < FRESH_SECONDS:
            skipped[dataset] = "fresh"
            continue
        manual.request(endpoint, dataset, reader="authority", actor=SERVICE_ACTOR)
        requested.append(dataset)
    if not requested:
        frappe.db.commit()
        return {"skipped": "nothing-to-do", "detail": skipped}

    # 水位与全量读**同批对齐**：先记下"此刻读到哪里"，再让四组去读。
    # 顺序不能反 —— 反过来会把"读取期间发生的改动"整段跳过（seed 只记位置、不补数据）。
    # 这与绑定时的 `connections.snapshots.bootstrap` 是同一套顺序、同一个入口。
    frappe.enqueue("pearnly_dms.adapter.change_sync.seed", endpoint=endpoint,
                   actor=SERVICE_ACTOR, queue=manual.QUEUE, timeout=420,
                   job_id=f"dms-changes-seed-{endpoint}", deduplicate=True,
                   enqueue_after_commit=False)
    frappe.db.commit()
    return {"requested": requested, "detail": skipped}


__all__ = ["FRESH_SECONDS", "READ_ORDER", "run"]
