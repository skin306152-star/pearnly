"""放行后的收款交进共用机器通道的写账队列，回执记回收款记录（RE 号 / 失败原因 / 电脑重读到的各张余额）。

- 交：载荷方向 ``receipt``、载荷版本 20（`contracts.payload`）；请求号 ``erp:receipt:<记录号>``（同一笔只交一次）；写进放行时冻结的
  那本账套；作业归谁按 `express.push.office_actor`（收款员不在办公室电脑上登录 CC：归这家公司在够得到这本账套的电脑上登录过
  CC 的员工）。电脑不在线就排着，上线后补写。
- 回执（共用通道在同一事务里经 `express.desktop.job_changed` 交来）：排队 / 领走 → 待写入 / 写入中；成功 → 已写入 + RE 号，
  回执里每张发票写后的余额改进快照，再按客户读一次；转人工 → 失败 + 原因（``[ALREADY_COLLECTED]`` 时电脑重读到的各张余额记在
  记录上、改进快照）；撤掉 → 撤回的保持已撤回，其余记失败。
"""
import json

import frappe
from frappe.utils import now_datetime

from pearnly_core.cc_desktop import jobs
from pearnly_core.cc_desktop.wire import ack_rules

from pearnly_erp_portal.collection import contracts, snapshot

DOCTYPE = "ERP Collection"
COMPANY = "ERP Company"
REQUEST = "erp:receipt:"
CANCELLED = "collection.withdrawn"


def send(name):
    """（放行的事务里）交一件写账。账套此刻没有电脑报了（位置取不到）：记失败「没有账套」，不交。"""
    from pearnly_erp_portal.collection import service
    from pearnly_erp_portal.express import contracts as express_contracts, push as express_push

    doc = frappe.get_doc(DOCTYPE, name)
    book = service._book_row(doc)
    if not book.get("path"):
        _set(name, {"status": contracts.FAILED, "reason": express_contracts.NO_BOOK})
        return None
    company = frappe.db.get_value(COMPANY, doc.company, ["tax_id", "branch"], as_dict=True) or {}
    record = {"ref_no": doc.name, "doc_date": doc.doc_date, "customer": doc.customer, "salesman": doc.salesman,
              "department": doc.department, "cash": doc.cash,
              "invoices": [{"docnum": row.docnum, "amount": row.amount} for row in doc.invoices],
              "methods": [{"code": row.code, "prefix": row.prefix, "amount": row.amount} for row in doc.payments]}
    payload = contracts.payload(record, book, company, user=doc.collector)
    job = jobs.enqueue(source_doctype=DOCTYPE, source_name=doc.name, request_key=REQUEST + doc.name,
                       actor=express_push.office_actor(doc.collector, doc.company, doc.book_key), book_key=doc.book_key,
                       payload=payload, payload_version=contracts.PAYLOAD_VERSION, label=doc.name)
    _set(name, {"job": job["job"]})
    return job


def _set(name, values):
    frappe.db.set_value(DOCTYPE, name, values, update_modified=False)


def cancellable(job) -> bool:
    """这件写账还能撤：还没被电脑领走（排队、转人工）或已经了结（撤掉的、失败的）。领走的、写成的不行。"""
    found = jobs.find_by_name(job)
    return found is None or found["status"] in ("queued", "manual", "cancelled")


def cancel(job):
    found = jobs.find_by_name(job)
    if found is not None and found["status"] in ("queued", "manual"):
        jobs.cancel(job, CANCELLED)


def _meta(view):
    """回执的附带信息（每张发票的余额）：共用通道交进来的视图里有就用；没有就读这件作业存下的回执。"""
    found = view.get("receipt")
    if isinstance(found, dict):
        return found
    raw = frappe.db.get_value(jobs.JOB, view.get("job"), "meta_json") if view.get("job") else None
    try:
        meta = json.loads(raw or "{}")
    except ValueError:
        return {}
    return meta if isinstance(meta, dict) else {}


def state_of(view) -> str:
    status = view.get("status")
    if status == "success":
        return contracts.WRITTEN
    if status == "leased":
        return contracts.WRITING
    if status == "queued":
        return contracts.QUEUED
    return contracts.FAILED


def job_changed(view):
    """共用写账队列里这笔收款有了新状态（同一事务里叫）：记回收款记录。"""
    name = view.get("source_name")
    if view.get("source_doctype") != DOCTYPE or not name or not frappe.db.exists(DOCTYPE, name):
        return
    doc = frappe.db.get_value(DOCTYPE, name, ["name", "status", "book_key", "company", "customer"], as_dict=True)
    if doc.status == contracts.WITHDRAWN:
        _set(name, {"job": view.get("job")})
        return  # 撤回在先，撤掉作业的回执不改它
    state = state_of(view)
    values = {"status": state, "job": view.get("job")}
    if state == contracts.WRITTEN:
        found = contracts.balances(_meta(view))
        values.update(docnum=str(view.get("docnum") or "")[:40], reason="", written_at=now_datetime(),
                      balances=json.dumps({key: contracts.text(value) for key, value in found.items()}) if found else None)
        _set(name, values)
        snapshot.apply_balances(doc.book_key, found, values["written_at"])
        _refresh(doc)
        return
    if state == contracts.FAILED:
        reason = str(view.get("reason") or "")[:ack_rules.OPERATOR_REASON_MAX] or view.get("status") or ""
        values["reason"] = reason
        found = contracts.balances(_meta(view)) if ack_rules.error_code(reason) in (
            "ALREADY_COLLECTED", "INVOICE_NOT_FOUND") else {}
        if found:
            values["balances"] = json.dumps({key: contracts.text(value) for key, value in found.items()})
            snapshot.apply_balances(doc.book_key, found)
        _set(name, values)
        if found:
            _refresh(doc)
        return
    values["reason"] = str(view.get("reason") or "")[:500]  # 排队里的原因（等账套、CC 桌面版要更新……）照实记
    _set(name, values)


def _refresh(doc):
    """写入 / 被拒后按这位客户再读一次未收清发票（电脑在线才发；失败不影响回执）。"""
    from pearnly_erp_portal.collection import service

    try:
        book = service._book_row(frappe.get_doc(DOCTYPE, doc.name))
        if book.get("path"):
            snapshot.request(doc.company, book, doc.customer)
    except Exception:  # noqa: BLE001  刷新只是顺手，出错只记日志
        frappe.log_error(title="ERP receivable snapshot refresh failed")
