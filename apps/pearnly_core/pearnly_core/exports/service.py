"""导出：小的当场给文件，大的排后台任务，做完推给本人下载（多产品共用）。

为什么：ERP 销售 / 采购 / 盘点、Cowork 对账 / 盘点 / 任务、账单明细的导出原来都是同步请求、没有时间上限，数据多就超过
网页进程 120 秒被掐断（2026-10-02 压测窗口转用户）。现在每个导出交两样东西：

- ``count``：数这次要导多少行，同时做和生成文件**同一套**的权限检查（没权限就在这里抛错）。只需要知道够不够阈值，
  可以数到阈值 + 1 就停。下载时再调一次，权限被收回的人拿不到已经生成好的文件。
- ``build``：生成文件，回 ``bytes`` 或 ``{"filename", "content"}``。后台跑时照样以发起人的身份、发起时的语言跑。

行数不超过 ``pearnly_export_sync_rows``（站点配置，默认 1000）就在这次请求里生成并直接回文件，与原来一样；超过就建一条
``Pearnly Export``、排进 default 队列（long 队列里有等识别的对账，最长 25 分钟，不能排在它后面），回 ``{"export_job"}``。
做完原生实时推送 ``pearnly_export`` 给本人，页面断线时由轮询兜底（``api.status``）。

文件按用户隔离：存在站点私有目录下的 ``pearnly-exports/``（不经 /files、/private/files，不建 File 记录），
名字是随机的 32 位十六进制；只有发起人通过 ``api.download`` 能拿，1 小时后过期，由定时清理删掉。
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime

DOCTYPE = "Pearnly Export"
EVENT = "pearnly_export"
FOLDER = "pearnly-exports"
DEFAULT_SYNC_ROWS = 1000
LIFETIME = timedelta(hours=1)
# 一个导出最多跑多久（后台任务时限）；没做完的到期一并清理。
TIMEOUT_SECONDS = 25 * 60
QUEUE = "default"


def sync_rows() -> int:
    try:
        value = int(frappe.conf.get("pearnly_export_sync_rows") or DEFAULT_SYNC_ROWS)
    except (TypeError, ValueError):
        value = DEFAULT_SYNC_ROWS
    return max(0, value)


def respond(filename, content, content_type=None):
    """把文件放进这次请求的原生下载响应。"""
    if isinstance(content, str):
        content = content.encode("utf-8")
    response = {"type": "download", "filename": filename, "filecontent": content,
                "display_content_as": "attachment"}
    if content_type:
        response["content_type"] = content_type
    frappe.local.response.update(response)


def _unpack(result, filename):
    if isinstance(result, dict):
        return result.get("filename") or filename, result.get("content")
    return filename, result


def deliver(*, count, build, args=None, filename="export.xlsx", content_type=None):
    """导出入口调用：数行（含权限检查）→ 小的当场回文件（返回 None），大的排任务（返回 ``{"export_job": 名}``）。"""
    args = dict(args or {})
    size = int(frappe.get_attr(count)(**args) or 0)
    if size <= sync_rows():
        name, content = _unpack(frappe.get_attr(build)(**args), filename)
        respond(name, content, content_type)
        return None
    return {"export_job": enqueue(count=count, build=build, args=args, filename=filename,
                                  content_type=content_type)}


def enqueue(*, count, build, args, filename, content_type=None):
    name = secrets.token_hex(16)
    frappe.get_doc({"doctype": DOCTYPE, "name": name, "user": frappe.session.user, "status": "queued",
                    "count_method": count, "build_method": build,
                    "arguments": json.dumps(args, ensure_ascii=False, default=str),
                    "lang": frappe.local.lang or "", "filename": filename, "content_type": content_type or "",
                    "expires_at": add_to_date(now_datetime(), seconds=TIMEOUT_SECONDS) + LIFETIME,
                    }).insert(ignore_permissions=True)
    frappe.enqueue("pearnly_core.exports.service.run", queue=QUEUE, timeout=TIMEOUT_SECONDS, job=name,
                   enqueue_after_commit=True)
    return name


def folder() -> str:
    return frappe.get_site_path("private", FOLDER)


def path_of(key: str) -> str:
    if not key or not all(char in "0123456789abcdef" for char in key):
        raise frappe.PermissionError
    return os.path.join(folder(), key)


def _publish(user, name, status):
    # 调用前已经提交：马上推（不等下一次提交，任务收尾时框架未必再提交）。
    frappe.publish_realtime(EVENT, {"job": name, "status": status}, user=user, after_commit=False)


def failure_text(error) -> str:
    """给人看的失败原因：业务上的拒绝（权限、找不到、校验）照原话；程序错误只给一句，细节记错误日志。"""
    if isinstance(error, (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError)):
        text = str(error).strip()
        if text:
            return text[:500]
    return _("The export could not be created.")


def run(job):
    """后台任务：以发起人的身份、发起时的语言生成文件，存好后推给本人。"""
    row = frappe.db.get_value(DOCTYPE, job, ["user", "status", "build_method", "arguments", "lang", "filename"],
                              as_dict=True)
    if not row or row.status != "queued":
        return
    frappe.db.set_value(DOCTYPE, job, "status", "running", update_modified=False)
    frappe.db.commit()
    frappe.set_user(row.user)
    if row.lang:
        frappe.local.lang = row.lang
    try:
        filename, content = _unpack(frappe.get_attr(row.build_method)(**json.loads(row.arguments or "{}")),
                                    row.filename)
        if isinstance(content, str):
            content = content.encode("utf-8")
        if not isinstance(content, (bytes, bytearray)):
            raise TypeError("export builder returned no file content")
        key = secrets.token_hex(16)
        os.makedirs(folder(), mode=0o700, exist_ok=True)
        descriptor = os.open(path_of(key), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
    except Exception as error:
        frappe.db.rollback()
        frappe.local.message_log = []
        if not isinstance(error, (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError)):
            frappe.log_error(title="Export failed", reference_doctype=DOCTYPE, reference_name=job)
        frappe.db.set_value(DOCTYPE, job, {"status": "failed", "error": failure_text(error),
                                           "expires_at": now_datetime() + LIFETIME}, update_modified=False)
        frappe.db.commit()
        _publish(row.user, job, "failed")
        return
    frappe.db.set_value(DOCTYPE, job, {"status": "done", "file_key": key, "filename": filename,
                                       "size": len(content), "expires_at": now_datetime() + LIFETIME},
                        update_modified=False)
    frappe.db.commit()
    _publish(row.user, job, "done")


def own(job):
    """本人的那条任务；别人的、没有的、过期的一律同一句拒绝（不透露有没有这条）。"""
    row = frappe.db.get_value(DOCTYPE, job, ["name", "user", "status", "count_method", "arguments", "filename",
                                             "content_type", "file_key", "error", "expires_at"], as_dict=True) \
        if isinstance(job, str) and job else None
    if not row or row.user != frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    if row.expires_at and get_datetime(row.expires_at) < now_datetime():
        frappe.throw(_("This file has expired. Export it again."), frappe.PermissionError)
    return row


def status(job):
    row = own(job)
    return {"job": row.name, "status": row.status, "error": (row.error or "") if row.status == "failed" else ""}


def download(job):
    row = own(job)
    if row.status != "done" or not row.file_key:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    # 下载时再查一次权限：生成后被收回权限的人拿不到。
    frappe.get_attr(row.count_method)(**json.loads(row.arguments or "{}"))
    try:
        with open(path_of(row.file_key), "rb") as handle:
            content = handle.read()
    except FileNotFoundError:
        frappe.throw(_("This file has expired. Export it again."), frappe.PermissionError)
    respond(row.filename, content, row.content_type or None)
    frappe.local.pearnly_export_response = True


def sweep():
    """定时：删掉过期的任务和文件（含没跑完的）。"""
    expired = frappe.get_all(DOCTYPE, filters={"expires_at": ("<", now_datetime())}, fields=["name", "file_key"],
                             limit_page_length=500)
    for row in expired:
        if row.file_key:
            try:
                os.remove(path_of(row.file_key))
            except (FileNotFoundError, frappe.PermissionError):
                pass
        frappe.db.delete(DOCTYPE, {"name": row.name})
    if expired:
        frappe.db.commit()


def has_permission(doc, ptype=None, user=None):
    return bool(doc) and doc.user == (user or frappe.session.user)


def query_conditions(user=None):
    user = user or frappe.session.user
    return f"`tab{DOCTYPE}`.`user` = {frappe.db.escape(user)}"


def protect_response(response):
    if getattr(frappe.local, "pearnly_export_response", False):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"


__all__ = ["DEFAULT_SYNC_ROWS", "DOCTYPE", "EVENT", "deliver", "download", "enqueue", "failure_text", "own",
           "respond", "run", "status", "sweep", "sync_rows"]
