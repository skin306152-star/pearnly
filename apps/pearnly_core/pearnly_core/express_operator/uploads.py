"""实操任务里上传的资料（多产品共用，Code 版 CC 评估 2026-11-04 B1 / B2，主会话 Q9 批）：上传一份就在任务上记一行
（子表 ``Pearnly Express Operator Task Upload``：原件、文件名、大小、识别任务号、上传时刻、请求键），刷新、换设备、手机上都看得到
同一张卡片；状态不存，照产品原来的识别记录现读（产品声明的 ``uploads`` 钩子），整理成同一个形状（``shapes.upload_view``）。

- 只给任务的创建人本人（同 ``tasks.read``），上传还要过产品的权限（``products.allowed``，同 ``intake``）；
- 同一请求键再交（页面重试同一份）只记一行；上传照常、识别照常、扣费照常（产品口径「上传识别都扣费」），这里不查重不拦；
- 识别进度的实时提醒不在这里：识别排队、开始读、读到第几页、有结论，共用识别队列已推 ``pearnly_ocr_work``（只推提交人，
  ``reference`` 就是这里的 ``batch``），页面收到再读一次 ``read``。
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_core.express_operator import products, shapes, tasks

UPLOAD = "Pearnly Express Operator Task Upload"
KEY_MAX = 128
ROW_FIELDS = ("request_key", "source", "filename", "size", "batch")


def _utc(value) -> str:
    """库里的时间（站点时区的裸时间）→ RFC3339 UTC，精确到秒（同对话列表 `sessions._when` 的写法）：页面按浏览器时区显示。"""
    if not value:
        return ""
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).strip())
    moment = moment.replace(microsecond=0, tzinfo=ZoneInfo(frappe.utils.get_system_timezone())).astimezone(timezone.utc)
    return moment.isoformat().replace("+00:00", "Z")


def _row(row) -> dict:
    """子表一行给页面：上传时刻换成 RFC3339 UTC。"""
    return {**{key: row.get(key) for key in ROW_FIELDS}, "uploaded_at": _utc(row.get("uploaded_at"))}


def check(name, company, request_key) -> None:
    """上传前：是本人的这个任务、同一家公司、请求键成形、还没记满（先挡，挡下的不交识别、不扣费）。"""
    doc = tasks._own(name)
    key = str(request_key or "")
    if doc.company != str(company or "") or not 1 <= len(key) <= KEY_MAX:
        frappe.throw(_("Invalid request"))
    rows = doc.get("uploads") or []
    if len(rows) >= shapes.UPLOADS_MAX and not any(row.request_key == key for row in rows):
        frappe.throw(_("Invalid request"))


def record(name, request_key, filename, size, result) -> dict:
    """产品收下这一份后记一行（同一请求键已记过就更新那一行），回这一行此刻的样子（刚交识别：排队中）。"""
    doc = tasks._own(name, for_update=True)
    key = str(request_key or "")
    values = {"request_key": key, "source": str(result.get("source") or "")[:140],
              "filename": str(filename or "")[:255], "size": max(0, int(size or 0)),
              "batch": str(result.get("name") or result.get("batch") or "")[:140]}
    row = next((item for item in doc.get("uploads") or [] if item.request_key == key), None)
    if row is None:
        row = doc.append("uploads", {**values, "uploaded_at": now_datetime()})
    else:
        row.update(values)
    tasks._save(doc)
    return shapes.upload_view(_row(row), {"state": "queued", "page_count": result.get("page_count")}, doc.period or "")


def read(name, batches=None) -> dict:
    """``task_uploads``：这个任务里上传过的每一份，按上传先后。``batches`` 给了只现读这几份（页面只盯在途的几份，省得每次把
    整个任务的识别记录都读一遍），其余照样列出但不带状态（``state`` 空）。"""
    doc = tasks._own(name)
    rows = list(doc.get("uploads") or [])
    wanted = {str(item) for item in batches} if batches else None
    asked = [row.batch for row in rows if row.batch and (wanted is None or row.batch in wanted)]
    found = products.uploads(doc.company, doc.period or "", asked) if asked else {}
    out = []
    for row in rows:
        if wanted is not None and row.batch not in wanted:
            out.append({**shapes.upload_view(_row(row), {"state": "queued"}, doc.period or ""), "state": ""})
            continue
        out.append(shapes.upload_view(_row(row), found.get(row.batch) if row.batch else None, doc.period or ""))
    return {"task": doc.name, "period": doc.period or "", "uploads": out}


__all__ = ["UPLOAD", "check", "read", "record"]
