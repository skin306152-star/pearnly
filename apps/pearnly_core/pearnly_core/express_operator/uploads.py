"""实操任务里上传的资料（多产品共用，Code 版 CC 评估 2026-11-04 B1 / B2，主会话 Q9 批）：上传一份就在任务上记一行
（子表 ``Pearnly Express Operator Task Upload``：原件、文件名、大小、识别任务号、上传时刻、请求键），刷新、换设备、手机上都看得到
同一张卡片；状态不存，照产品原来的识别记录现读（产品声明的 ``uploads`` 钩子），整理成同一个形状（``shapes.upload_view``）。

- 只给任务的创建人本人（同 ``tasks.read``），上传还要过产品的权限（``products.allowed``，同 ``intake``）；
- 同一请求键再交（页面重试同一份）只记一行；上传照常、识别照常、扣费照常（产品口径「上传识别都扣费」），这里不查重不拦；
- 对话输入框里放的资料（2026-11-06「选中就上传」；11-14 用户：上传是为了点发送不用等，识别要等点了发送才开始）：对话的上传口
  （``assistant/files.upload``）选中只记一行「待发送」（``hold``：不存原件层、不交识别、不扣费，也不记时刻，对话里不出上传卡片）；
  用户交出它的那一刻（输入框点发送、引导卡上提交，唯一入口 ``assistant.files.commit`` → ``commit``）才记上时刻（上传卡片排在
  这一刻后面），提交后由后台把它交给产品识别（``start``）。没交出就
  移除的那一行删掉（``drop``）。请求键就是本站给的文件编号，消息里带着它的位置，按它对上；
- 识别进度的实时提醒不在这里：识别排队、开始读、读到第几页、有结论，共用识别队列已推 ``pearnly_ocr_work``（只推提交人，
  ``reference`` 就是这里的 ``batch``），页面收到再读一次 ``read``。
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_core.execution.errors import Deferred
from pearnly_core.express_operator import products, shapes, tasks

UPLOAD = "Pearnly Express Operator Task Upload"
KEY_MAX = 128
ROW_FIELDS = ("request_key", "source", "filename", "size", "batch", "start_code")
START_LOCK_SECONDS = 180  # 同一份交识别按份排队：后台与识别工具同时来时只交一次


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


def record(name, request_key, filename, size, result, pending: bool = False) -> dict:
    """产品收下这一份后记一行（同一请求键已记过就更新那一行），回这一行此刻的样子（刚交识别：排队中）。``pending``：对话输入框里
    的一份，消息还没发出，先不记上传时刻（``sent`` 再记）。"""
    doc = tasks._own(name, for_update=True)
    key = str(request_key or "")
    values = {"request_key": key, "source": str(result.get("source") or "")[:140],
              "filename": str(filename or "")[:255], "size": max(0, int(size or 0)),
              "batch": str(result.get("name") or result.get("batch") or "")[:140]}
    row = next((item for item in doc.get("uploads") or [] if item.request_key == key), None)
    if row is None:
        row = doc.append("uploads", {**values, "uploaded_at": None if pending else now_datetime()})
    else:
        row.update(values)
    tasks._save(doc)
    return shapes.upload_view(_row(row), {"state": "queued", "page_count": result.get("page_count")}, doc.period or "")


def hold(name, filename, size, request_key) -> dict | None:
    """对话输入框里放进来的一份（对话的上传口调）：只在任务上记一行「待发送」，不交识别、不扣费（发出后 ``start`` 才交）。
    产品不收上传（没声明 ``intake``）回 None，文件照常只进对话。"""
    doc = tasks._own(name)
    if not products.enabled() or not products.can_intake() or not products.allowed(frappe.session.user, doc.company):
        return None
    check(name, doc.company, request_key)
    return record(name, request_key, filename, size, {}, pending=True)


def drop(name, request_key) -> bool:
    """输入框里移除了还没发出的那一份：删掉那一行「待发送」。已发出、已交识别的不动。回删了没有。"""
    doc = tasks._own(name, for_update=True)
    key = str(request_key or "")
    row = next((item for item in doc.get("uploads") or []
                if key and item.request_key == key and not item.uploaded_at and not item.batch), None)
    if row is None:
        return False
    doc.remove(row)
    tasks._save(doc)
    return True


def waiting(name, request_key) -> bool:
    """这一份已发出、还没交成识别（``start`` 该接着做）。"""
    doc = tasks._own(name)
    key = str(request_key or "")
    return any(key and row.request_key == key and row.uploaded_at and not row.batch for row in doc.get("uploads") or [])


def start(name, request_key, content) -> str:
    """发出后的一份交给产品识别（后台 ``assistant.files.start_sent``、识别工具 ``assistant.files.taken`` 都走这里），回识别任务号；
    没发出的不交（回空串）。按份加锁、锁里重读：已交过的直接回原来那个，后台与工具同时来也只交一次（产品按请求键本来也不重交）。
    交不成（原件存放处一时连不上、产品不收）记下原因（上传卡片显示失败），下次再来照样重交。"""
    key = str(request_key or "")
    with frappe.cache.lock(f"{frappe.local.site}:pearnly_operator_upload_start:{name}:{key}", timeout=START_LOCK_SECONDS,
                           blocking_timeout=START_LOCK_SECONDS):
        frappe.db.commit()  # 结束读快照（不回滚：识别工具里调时，这次请求已有的写入要留着），锁里读到别人刚交的那份
        doc = tasks._own(name)
        row = next((item for item in doc.get("uploads") or [] if key and item.request_key == key), None)
        if row is None or not row.uploaded_at:
            return ""
        if row.batch:
            return str(row.batch)
        try:
            if not products.allowed(frappe.session.user, doc.company):  # 发出前这家公司不能代录了：同产品拒收
                frappe.throw(_("Not Permitted"), frappe.PermissionError)
            result = products.intake(doc.company, row.filename, content, key)
            code = "" if result.get("accepted") else str(result.get("code") or result.get("reason") or "unsupported")
        except Deferred:
            frappe.db.rollback()
            result, code = {}, "upload.deferred"
        except (frappe.ValidationError, frappe.PermissionError):
            # 产品拒收（例如这家公司还没选好账套、没有这个权限）：以前在输入框里就红了，现在发出后才交，原因记进这一行（卡片显示失败）
            frappe.db.rollback()
            frappe.log_error(title="Operator upload start refused")
            frappe.clear_messages()
            result, code = {}, "start_refused"
        except Exception:
            # 别的意外（文件读不了等）：同样记下原因，卡片显示失败，不一直停在排队中
            frappe.db.rollback()
            frappe.log_error(title="Operator upload start failed")
            frappe.clear_messages()
            result, code = {}, "start_failed"
        doc = tasks._own(name, for_update=True)
        row = next(item for item in doc.get("uploads") or [] if item.request_key == key)
        row.update({"source": str(result.get("source") or row.source or "")[:140],
                    "batch": str(result.get("name") or result.get("batch") or "")[:140], "start_code": code[:80]})
        tasks._save(doc)
        frappe.db.commit()
        return str(row.batch or "")


def batch_of(name, request_key) -> str:
    """任务里这个请求键那一份交的识别任务号；没记过、没交成回空串（只认本人的任务，同 ``read``）。"""
    doc = tasks._own(name)
    key = str(request_key or "")
    return next((str(row.batch) for row in doc.get("uploads") or [] if key and row.request_key == key and row.batch), "")


def keys_in(name, text) -> list[str]:
    """消息原文里带着的、这个任务上记过的那几份的请求键（消息里带着附件的位置，按文件编号对上）。只读。"""
    doc = tasks._own(name)
    body = str(text or "")
    return [str(row.request_key) for row in doc.get("uploads") or [] if row.request_key and row.request_key in body]


def commit(name, request_keys) -> list[str]:
    """用户交出了这几份（唯一入口 ``assistant.files.commit`` 调；输入框点发送、引导卡上提交都走它）：还没进对话的记上此刻（上传
    卡片排在这一刻后面），回其中已交出、还没交成识别的请求键（调用方提交后交识别）。幂等：同几份再来不重记时刻，还没交上的照样回。"""
    keys = {str(key) for key in request_keys or () if key}
    doc = tasks._own(name, for_update=True)
    rows = [row for row in doc.get("uploads") or [] if row.request_key in keys]
    fresh = [row for row in rows if not row.uploaded_at]
    if fresh:
        moment = now_datetime()
        for row in fresh:
            row.uploaded_at = moment
        tasks._save(doc)
    return [str(row.request_key) for row in rows if not row.batch]


def read(name, batches=None) -> dict:
    """``task_uploads``：这个任务里上传过（已随消息发出）的每一份，按上传先后。``batches`` 给了只现读这几份（页面只盯在途的几份，省得每次把
    整个任务的识别记录都读一遍），其余照样列出但不带状态（``state`` 空）。"""
    doc = tasks._own(name)
    rows = [row for row in doc.get("uploads") or [] if row.uploaded_at]  # 还在输入框里（没发出）的不算任务里的上传
    wanted = {str(item) for item in batches} if batches else None
    asked = [row.batch for row in rows if row.batch and (wanted is None or row.batch in wanted)]
    found = products.uploads(doc.company, doc.period or "", asked) if asked else {}
    out = []
    for row in rows:
        if wanted is not None and row.batch not in wanted:
            out.append({**shapes.upload_view(_row(row), {"state": "queued"}, doc.period or ""), "state": ""})
            continue
        out.append(shapes.upload_view(_row(row), found.get(row.batch) if row.batch else shapes.unstarted(row), doc.period or ""))
    return {"task": doc.name, "period": doc.period or "", "uploads": out}


__all__ = ["UPLOAD", "check", "commit", "drop", "hold", "keys_in", "read", "record", "start", "waiting"]
