# -*- coding: utf-8 -*-
"""每日一次的后台地址刷新（泰国凌晨 4 点那一小时，见 hooks 的两条 cron）。

为什么单独、低频：

· 全国地址是**租户可编辑**的字典（列表页有增/改/删），所以不能只拉一次永久用；
· 但一次完整读取只要几十个请求、约 8 秒（换成列表端点后），**没有任何理由频繁刷**；
· 高频全量重扫会长时间占着第三方账号 —— 那正是旧做法的病根（每 5 分钟一次，
  把用户的实时操作和采集互相挡住）。

所以：一天一次，落在凌晨泰国时间 4:00–5:00；这一小时内排两次（4:00 与 4:30），
第二次是**失败重试**——若第一次已经成功，本任务因数据足够新而直接跳过，不重复读。

时区：Frappe 的 cron 按**站点时区**求值（本站 Asia/Bangkok，见 System Settings），
所以 `0 4 * * *` 就是泰国凌晨 4 点，不依赖宿主容器时区。

管理员仍可在连接页手动触发（本任务不改变该入口）；本任务只负责"没人管也会更新"。
"""
import time

import frappe

from pearnly_dms.snapshots import manual, store

ADAPTER = "mrerp_dms"
ENDPOINT = "Pearnly ERP Endpoint"
ADMIN_ROLES = ("DMS Admin", "System Manager")

#: 多久算"今天已经刷过"。取 20 小时：凌晨成功一次后，4:30 的第二枪会跳过；
#: 若第一次失败（数据仍是昨天或更早），第二枪照常重试。
FRESH_SECONDS = 20 * 3600


def _primary_endpoint():
    """本站启用的共享主连接（与连接页同一判据：没有 owner_user）。"""
    rows = frappe.get_all(ENDPOINT, filters={"adapter": ADAPTER, "owner_user": ["is", "not set"],
                                             "enabled": 1},
                          pluck="name", limit_page_length=2)
    if len(rows) != 1:
        return None
    return rows[0]


def run():
    """排一次地址读取；数据够新就跳过。**不在这里登录第三方。**"""
    endpoint = _primary_endpoint()
    if not endpoint:
        return {"skipped": "no-connection"}
    current = manual.status(endpoint, "geo", reader="authority")
    if current.get("state") in ("queued", "running"):
        return {"skipped": "already-running", "request_id": current.get("request_id", "")}
    as_of = float(current.get("as_of") or 0)
    if as_of and time.time() - as_of < FRESH_SECONDS:
        return {"skipped": "fresh", "as_of": as_of}
    out = manual.request(endpoint, "geo", reader="authority", actor="Administrator")
    frappe.db.commit()
    return {"requested": out.get("request_id", ""), "state": out.get("state", "")}


__all__ = ["FRESH_SECONDS", "run"]
