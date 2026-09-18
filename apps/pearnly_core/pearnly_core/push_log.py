# -*- coding: utf-8 -*-
"""推送日志的服务层（共用层）。

**这张表是推送状态的唯一来源。** 计划里写死的两条判据，这里就是落点：

1. HTTP 200 **不等于**推成功 —— 只有本表里 `status='success'` 才算
   （老产品的 `erp_push_logs` 同理）；
2. 业务表上的 `push_status` / `push_log_ref` 只是引用位，别拿它当结论。

状态取值照老产品：`success | failed | skipped_dup | pending | retrying | manual`。

租约（lease）是"同一条只推一次"的保证：老产品给 `pending` 的行加
`lease_owner` / `lease_expires_at`，过期才能被别的 worker 抢。
"""

import json

import frappe
from frappe import _
from frappe.utils import add_to_date, now

LOG = "Pearnly Push Log"

STATUSES = ("pending", "retrying", "success", "failed", "skipped_dup", "manual")

# 终态：到了这几个就不再重试
FINAL_STATUSES = ("success", "failed", "skipped_dup")


def record(
    status: str,
    *,
    endpoint: str | None = None,
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    business_key: str | None = None,
    trigger: str = "manual",
    http_status: int | None = None,
    request_body=None,
    response_body: str | None = None,
    error_msg: str | None = None,
    elapsed_ms: int | None = None,
    attempt: int = 1,
    actor: str | None = None,
) -> str:
    """落一条推送日志，返回它的名字。

    `request_body` 给 dict/list 会自动转 JSON —— 字段是 Code(JSON)，
    直接塞 dict 会被 Frappe 拒绝。
    """
    if status not in STATUSES:
        frappe.throw(_("推送状态只能是 {0}").format(" / ".join(STATUSES)))

    if request_body is not None and not isinstance(request_body, str):
        request_body = json.dumps(request_body, ensure_ascii=False, default=str)

    doc = frappe.get_doc({
        "doctype": LOG,
        "status": status,
        "endpoint": endpoint or None,
        "reference_doctype": reference_doctype or None,
        "reference_name": reference_name or None,
        "business_key": business_key or "",
        "trigger": trigger,
        "http_status": http_status,
        "request_body": request_body,
        "response_body": (response_body or "")[:20000],
        "error_msg": (error_msg or "")[:2000],
        "elapsed_ms": elapsed_ms,
        "attempt": attempt,
        "actor": actor or frappe.session.user,
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc.name


def claim_for_push(endpoint: str, *, owner: str, ttl_minutes: int = 10) -> list[str]:
    """抢一批待推送的行（带租约）。返回抢到的行名。

    规则照老产品：只有 `pending` / `retrying`、且租约为空或已过期的行可以被抢；
    抢的时候写 `lease_owner` + `lease_expires_at`，**同一条只会被一个 worker 拿到**。

    为什么必须有租约：外网推送不是原子的，两个 worker 同时推同一条，
    对端就会建两份档。
    """
    if not endpoint:
        frappe.throw(_("抢推送任务必须指定连接"))
    stamp = now()
    names = frappe.db.sql(
        f"""
        select name from `tab{LOG}`
        where endpoint = %s
          and status in ('pending', 'retrying')
          and (lease_expires_at is null or lease_expires_at < %s)
        order by creation asc
        limit 20
        """,
        (endpoint, stamp),
        pluck=True,
    )
    claimed = []
    for name in names:
        # 带条件的 update：只有还没被别人占（租约为空或已过期）的才会被改到。
        #
        # ⚠️ **不能用 `frappe.db.sql(update...)` 的返回值判成败**：它对非查询语句
        # 返回的是**空元组**，不是影响行数（实测）。用它判断的话 `if updated:` 永远
        # 为假，结果就是"一个都抢不到"——而且不报错，看着像没任务。
        # 改成**改完回读**：只有 lease_owner 确实变成我，才算抢到（update 是原子的，
        # 抢输的那个改不到任何行，回读出来还是别人的名字）。
        frappe.db.sql(
            f"""
            update `tab{LOG}`
            set lease_owner = %s, lease_expires_at = %s
            where name = %s
              and status in ('pending', 'retrying')
              and (lease_expires_at is null or lease_expires_at < %s)
            """,
            (owner, add_to_date(stamp, minutes=ttl_minutes), name, stamp),
        )
        if frappe.db.get_value(LOG, name, "lease_owner") == owner:
            claimed.append(name)
    frappe.db.commit()
    return claimed


def finish(name: str, status: str, *, http_status: int | None = None,
           response_body: str | None = None, error_msg: str | None = None,
           business_key: str | None = None) -> dict:
    """给一条推送收口：改状态、释放租约、按需记业务键。"""
    if status not in STATUSES:
        frappe.throw(_("推送状态只能是 {0}").format(" / ".join(STATUSES)))
    doc = frappe.get_doc(LOG, name)
    doc.status = status
    if http_status is not None:
        doc.http_status = http_status
    if response_body is not None:
        doc.response_body = response_body[:20000]
    if error_msg is not None:
        doc.error_msg = error_msg[:2000]
    if business_key is not None:
        doc.business_key = business_key
    # 租约一定释放：留着会让这条轮不到别人，要干等到过期
    doc.lease_owner = ""
    doc.lease_expires_at = None
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status}


def schedule_retry(name: str, *, delay_minutes: int = 5) -> dict:
    """失败后安排重试。超过上限就置成终态 failed，**不无限重试**。"""
    doc = frappe.get_doc(LOG, name)
    if (doc.retry_count or 0) >= (doc.max_retries or 0):
        doc.status = "failed"
        doc.next_retry_at = None
        doc.lease_owner = ""
        doc.lease_expires_at = None
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return {"name": name, "status": "failed", "exhausted": True}

    doc.retry_count = (doc.retry_count or 0) + 1
    doc.status = "retrying"
    doc.next_retry_at = add_to_date(now(), minutes=delay_minutes)
    doc.lease_owner = ""
    doc.lease_expires_at = None
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"name": name, "status": "retrying", "retry_count": doc.retry_count}


def status_of(reference_doctype: str, reference_name: str) -> dict:
    """某一单的推送结论。**界面和验证脚本都该问这个**，而不是看业务表上的字段。"""
    rows = frappe.get_all(
        LOG,
        filters={"reference_doctype": reference_doctype, "reference_name": reference_name},
        fields=["name", "status", "http_status", "error_msg", "creation", "business_key"],
        order_by="creation desc",
        limit_page_length=0,
    )
    latest = rows[0] if rows else None
    return {
        "reference_doctype": reference_doctype,
        "reference_name": reference_name,
        "count": len(rows),
        # "推成功了没有"只看这一条
        "pushed": bool(latest and latest["status"] == "success"),
        "latest_status": latest["status"] if latest else "",
        "latest": latest,
        "history": rows,
    }


@frappe.whitelist()
def api_list(**kwargs):
    kwargs.pop("cmd", None)
    limit = int(kwargs.pop("limit", 50) or 50)
    filters = {k: v for k, v in kwargs.items() if v not in (None, "")}
    return frappe.get_all(
        LOG,
        filters=filters,
        fields=[
            "name", "status", "endpoint", "trigger", "reference_doctype",
            "reference_name", "business_key", "http_status", "attempt",
            "retry_count", "error_msg", "creation",
        ],
        order_by="creation desc",
        limit_page_length=limit,
    )


@frappe.whitelist()
def api_status(reference_doctype: str, reference_name: str):
    return status_of(reference_doctype, reference_name)
