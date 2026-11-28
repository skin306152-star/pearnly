"""放行后的收款交进共用机器通道的写账队列，回执记回收款记录（RE 号 / 失败原因 / 电脑重读到的各张余额）。

- 交：载荷方向 ``receipt``、载荷版本 20（`contracts.payload`）；请求号 ``erp:receipt:<记录号>``（同一笔只交一次）；写进放行时冻结的
  那本账套；作业归收款员本人，谁的电脑写在领单那一刻判（用户 11-17 定稿，`cc_desktop.jobs`：收款员没有在线的电脑时，这家公司在线、
  够得到这本账套、登录人有权限的电脑替她写）。电脑都不在线就排着，谁先上线谁写。
- 回执（共用通道在同一事务里经 `express.desktop.job_changed` 交来）：排队 / 领走 → 待写入 / 写入中；成功 → 已写入 + RE 号，
  回执里每张发票写后的余额改进快照，再按客户读一次；转人工 → 失败 + 原因（``[ALREADY_COLLECTED]`` 时电脑重读到的各张余额记在
  记录上、改进快照；服务品现金制的发票 CC 也回 ``[INVOICE_NOT_FOUND]``（证据 ``problem="cash_basis_vat"``），照原因记失败；
  这两种都按客户再读一次）；
  撤掉 → 撤回的保持已撤回，其余记失败。
- 排队中 / 写入中不记原因：在等 Express 关闭由读的时候算（`service.express_waiting`），不另推送说原因。
- 写成 / 失败后告诉录入的人：LINE 录的经 `integrations.line_collection.notify`（提交之后排的短任务；放行后 LINE 上还在等就用那次
  的回复令牌回结果，`pearnly_core.line_ui.receipt`；不推送）。
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
# 写成 / 失败后按来源告诉录入的人（提交之后才排：任务读到的是已提交的结果）
TELL = {"line": "pearnly_erp_portal.integrations.line_collection.notify"}


def request_key(name, attempt=0) -> str:
    """写账请求号：第一次 ``erp:receipt:<记录号>``（线上已有的写账件不变）；写前被拒退回草稿再放行的带次数
    ``erp:receipt:<记录号>:<次数>``。只进共用写账队列的去重（`cc_desktop.jobs.enqueue`），不下发给 CC 桌面版（领单只给投递号、
    记录号、单号与载荷，`queue_contracts.job`），YOUREF 仍是记录号。"""
    attempt = int(attempt or 0)
    return REQUEST + name + (f":{attempt}" if attempt > 0 else "")


def send(name):
    """（放行的事务里）交一件写账。账套此刻没有电脑报了（位置取不到）：记失败「没有账套」，不交。"""
    from pearnly_erp_portal.collection import service
    from pearnly_erp_portal.express import contracts as express_contracts, push as express_push

    doc = frappe.get_doc(DOCTYPE, name)
    from pearnly_erp_portal.collection import mrerp as ledger

    if ledger.handles(doc.company):
        return ledger.send(name)  # 账本是 MR.ERP：服务端导入（不经 CC 桌面版）
    book = service._book_row(doc)
    if not book.get("path"):
        _set(name, {"status": contracts.FAILED, "reason": express_contracts.NO_BOOK})
        return None
    company = frappe.db.get_value(COMPANY, doc.company, ["tax_id", "branch"], as_dict=True) or {}
    record = {"ref_no": doc.name, "doc_date": doc.doc_date, "customer": doc.customer, "salesman": doc.salesman,
              "department": doc.department, "cash": doc.cash,
              "invoices": [{"docnum": row.docnum, "amount": row.amount, "rectyp": row.get("rectyp") or ""} for row in doc.invoices],
              "methods": [{"code": row.code, "prefix": row.prefix, "amount": row.amount} for row in doc.payments]}
    payload = contracts.payload(record, book, company, user=doc.collector)
    job = jobs.enqueue(source_doctype=DOCTYPE, source_name=doc.name, request_key=request_key(doc.name, doc.get("attempt")),
                       actor=doc.collector, book_key=doc.book_key,
                       payload=payload, payload_version=payload["payload_version"], label=doc.name)
    _set(name, {"job": job["job"]})
    return job


LIVE_EVENT = "pearnly_erp_collection_changed"


def _set(name, values):
    frappe.db.set_value(DOCTYPE, name, values, update_modified=False)
    _live(name)


def _live(name):
    """状态变了通知能看收款页的人刷新（11-15 真机：「待写入」写完不自己变，要手动刷新）：同盘点页多人刷新的做法，
    只推给有「收款·查看」的人、只带单号，页面按本人权限重新取；同一请求里只推一次。"""
    sent = getattr(frappe.flags, "pearnly_collection_live", None)
    if sent is None:
        sent = set()
        setattr(frappe.flags, "pearnly_collection_live", sent)
    if name in sent:
        return
    sent.add(name)
    from pearnly_erp_portal.access import guard

    for user in frappe.get_all("User", filters={"enabled": 1, "user_type": "System User"},  # bounded: 本租户的员工账号（十几到几十个）
                               pluck="name"):
        if user != "Guest" and guard.has("collection.view", user=user):
            frappe.publish_realtime(LIVE_EVENT, {"name": name}, user=user, after_commit=True)


def cancellable(job) -> bool:
    """这件写账还能撤：还没被电脑领走（排队、转人工）或已经了结（撤掉的、失败的）。领走的、写成的不行。"""
    found = jobs.find_by_name(job)
    return found is None or found["status"] in ("queued", "manual", "cancelled")


def cancel(job):
    found = jobs.find_by_name(job)
    if found is not None and found["status"] in ("queued", "manual"):
        jobs.cancel(job, CANCELLED)


def retry(job) -> bool:
    """写进 Express 结果不明的写账件再排一次（共用 `cc_desktop.jobs.retry`：钉回原电脑，CC 桌面版按参照号查重；回执照常经
    :func:`job_changed` 记回）。不是转人工、原因不是结果不明的不动，回假。"""
    found = jobs.find_by_name(job) if job else None
    if not found or found["status"] != "manual" or found.get("reason") != contracts.EXPRESS_UNKNOWN:
        return False
    jobs.retry(job)
    return True


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
    doc = frappe.db.get_value(DOCTYPE, name, ["name", "status", "book_key", "company", "customer", "source", "job",
                                              "draft_version", "attempt"], as_dict=True)
    if doc.status == contracts.WITHDRAWN:
        _set(name, {"job": view.get("job")})
        return  # 撤回在先，撤掉作业的回执不改它
    if doc.status == contracts.DRAFT or (doc.job and view.get("job") and view.get("job") != doc.job):
        return  # 退回草稿之后、或换了新写账件之后，上一件的回执不改它（草稿没有在跑的写账件）
    state = state_of(view)
    values = {"status": state, "job": view.get("job")}
    if state == contracts.WRITTEN:
        found = contracts.balances(_meta(view))
        values.update(docnum=str(view.get("docnum") or "")[:40], reason="", written_at=now_datetime(),
                      balances=json.dumps({key: contracts.text(value) for key, value in found.items()}) if found else None)
        _set(name, values)
        snapshot.apply_balances(doc.book_key, found, values["written_at"])
        _refresh(doc)
        _tell(doc)
        return
    if state == contracts.FAILED:
        reason = str(view.get("reason") or "")[:ack_rules.OPERATOR_REASON_MAX] or view.get("status") or ""
        values["reason"] = reason
        uncollectable = ack_rules.error_code(reason) in contracts.UNCOLLECTABLE  # 已被收 / 不存在（含服务品现金制）
        found = contracts.balances(_meta(view)) if uncollectable else {}
        if found:
            values["balances"] = json.dumps({key: contracts.text(value) for key, value in found.items()})
            snapshot.apply_balances(doc.book_key, found)
        if ack_rules.error_code(reason) in contracts.RETURNABLE:
            # 写前就拒、一行没写：退回草稿带原因，录入的人改了再放行（版本 +1，旧卡失效；再放行换新请求号）
            values.update(status=contracts.DRAFT, draft_version=int(doc.draft_version or 0) + 1,
                          attempt=int(doc.attempt or 0) + 1)
        _set(name, values)
        if uncollectable:
            _refresh(doc)
        _tell(doc)
        return
    # 排队中 / 写入中：原因码不抄到收款记录上（排队里的 ``ACCOUNT_BUSY_LOCKED`` 等抄过来，记录就像失败了）。在不在等 Express 关闭
    # 读的时候问共用通道（`service.express_waiting`，`cc_desktop.jobs.express_wait_many` / `book_express_wait`，11-12）
    values["reason"] = ""
    _set(name, values)


def _tell(doc):
    path = TELL.get(doc.source)
    if path:
        frappe.enqueue(path, queue="short", enqueue_after_commit=True, name=doc.name)


def _refresh(doc):
    """写入 / 被拒后按这位客户再读一次未收清发票（电脑在线才发；失败不影响回执）。"""
    from pearnly_erp_portal.collection import service

    try:
        book = service._book_row(frappe.get_doc(DOCTYPE, doc.name))
        if book.get("path"):
            snapshot.request(doc.company, book, doc.customer)
    except Exception:  # noqa: BLE001  刷新只是顺手，出错只记日志
        frappe.log_error(title="ERP receivable snapshot refresh failed")
