"""ERP 采购 / 销售单导入直写进 MR.ERP（评估 §13 第 6 步）：公司账本是 MR.ERP 的，提交时排上（:func:`queue`），提交后在后台
准备规范载荷、经共用层导入 5 步 + 列表回读（`pearnly_core.mrerp.push.execute`，与 Cowork 同一份），结论记回单据上的通用账本栏
（``ledger_state`` / ``ledger_docnum`` / ``ledger_reason`` / ``ledger_needed``，``ledger_target = mrerp``）。

- 只在 :data:`pearnly_erp_portal.mrerp.MRERP_LEDGER_ENABLED` 开着时生效（:func:`handles` 关着时直接回假，不查库）；Express 那条
  （`express.push`）在入口处先问 :func:`handles`，关着时一字不变。
- 哪些单：采购单（真进货 / 费用）与销售单（同 Express 的种类），已提交、在公司选 MR.ERP 当账本之后开始的。已付的采购按现购写
  （共用层第 9 列现金供应商、第 10 列真实卖家，同 Cowork）。出入库、收银日汇总、盘点 MR.ERP 没有对应的导入，不排。
- 要人选的导入菜单 / 主档默认值：读到过、此刻要人选的，导入前就停在单据上（``choice_needed:mrerp`` + ``ledger_needed``）；没读到过的
  照导，MR.ERP 那边按读到的核对，要选就停下（读到的选项存回连接）。选一次（:func:`answer`）记在公司上后再排。
- 同账号互斥（ADR-026 第 8 条）：导入拿共用层的账号短锁（`session_lock.account_lock`，导入 5 步里拿）；被 CC 桌面版代录占着
  （`operator_holds`）时不导、照旧排队（``mrerp.account_busy``），每分钟（:func:`poll`）再看，不算失败。
- 幂等：只从「排队中」领（行锁里改成「正在导入」并提交）；导入发出之后出错、受理了却查不到 = 结果不明（停下、不自动重推）；
  进程死掉的「正在导入」超过 15 分钟转结果不明。同一张单的 MR.ERP 单号固定（`contracts.history_id`），重推遇「单号已存在」回读认领。
- 推送不查重、不拦（用户 10-08）。
"""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import now_datetime


from pearnly_erp_portal import mrerp
from pearnly_erp_portal.express import contracts as express_contracts, pending, printed
from pearnly_erp_portal.mrerp import choices, connection as connections, contracts

SALES = "ERP Sales Document"
PURCHASE = "Purchase Document"
DIRECTIONS = {SALES: "sales", PURCHASE: "purchase"}
KINDS = {SALES: express_contracts.SALES_KINDS, PURCHASE: express_contracts.PURCHASE_KINDS}
JOB_TIMEOUT_SECONDS = 600
POLL_MAX = 100


def handles(company) -> bool:
    """这家公司的账本是 MR.ERP（功能开着、公司选了 MR.ERP 账套）。关着时不查库。"""
    if not mrerp.enabled() or not company:
        return False
    row = frappe.db.get_value("ERP Company", company, ["mrerp_enabled", "express_enabled"], as_dict=True)
    return bool(row and row.mrerp_enabled and not row.express_enabled)


def _eligible(doctype, doc) -> bool:
    if doctype not in DIRECTIONS or doc.get("status") != "submitted" or (doc.get("doc_kind") or "") not in KINDS[doctype]:
        return False
    since = frappe.db.get_value("ERP Company", doc.company, "mrerp_linked_at")
    created = frappe.db.get_value(doctype, doc.name, "creation")
    return bool(since and created and frappe.utils.get_datetime(created) >= frappe.utils.get_datetime(since))


def queue(doctype, doc):
    """（`express.push.queue` 交过来：提交事务里）该导入就标「排队中」、记账本 MR.ERP，排提交后的导入；不该什么都不做。"""
    if doc.ledger_state or not handles(doc.company) or not _eligible(doctype, doc):
        return
    _set(doctype, doc.name, {"ledger_state": express_contracts.QUEUED, "ledger_reason": "",
                             "ledger_target": contracts.LEDGER_MRERP})
    dispatch(doctype, doc.name)


def dispatch(doctype, name):
    # 任务入口在 `express.push.mrerp_run`：它把建单据的函数（`express.push.document`）交给 :func:`run`，本域不反向导入 express.push
    frappe.enqueue("pearnly_erp_portal.express.push.mrerp_run", queue="short", job_id=f"erp-mrerp-push::{doctype}::{name}",
                   deduplicate=True, enqueue_after_commit=True, timeout=JOB_TIMEOUT_SECONDS, doctype=doctype, name=name)


def _set(doctype, name, values):
    """写单据上的通用账本栏（不改修改时间）；改到状态或原因时单据进出「待处理」就推一次（同 `express.push._set`）。"""
    before = pending.ledger_of(doctype, name) if {"ledger_state", "ledger_reason"} & set(values) else None
    frappe.db.set_value(doctype, name, values, update_modified=False)
    if before is not None:
        pending.notify(doctype, name, before)


def prepare(doctype, name, document) -> dict:
    """准备一张：``{"payload", "company", "direction"}``，或 ``{"hold": 原因, "needed": [...]}`` / ``{"failed": 原因}``。不写单据。
    ``document``：建单据的函数（`express.push.document`，同 Express 交给写账规则的那个形状）。"""
    company = frappe.db.get_value(doctype, name, "company")
    row = choices.company_row(company)
    if not row or not row.mrerp_enabled or not row.mrerp_connection or not row.mrerp_account_set:
        return {"failed": contracts.NOT_CONFIGURED}
    held = printed.hold(doctype, name)  # 识别来的单票面合计没有 / 对不上：同 Express 直写停下
    if held:
        return {"hold": held}
    body = document(doctype, name)
    menu_view, master_view = choices.views(row)
    needed = contracts.needed(menu_view, master_view, body["direction"])
    if needed:
        return {"hold": contracts.CHOICE, "needed": needed}
    party_field = "supplier" if body["direction"] == "purchase" else "customer"
    found = frappe.db.get_value(doctype, name, ["doc_kind", "payment_method", party_field], as_dict=True) or {}
    flat = contracts.history(body, company_name=row.company_name, doc_kind=found.get("doc_kind") or "",
                             payment_method=found.get("payment_method") or "", party_id=found.get(party_field) or "")
    menus, defaults = choices.chosen(row)
    return {"payload": contracts.payload(flat, own_tax_id=body["company"]["tax_id"], menus=menus, defaults=defaults),
            "company": row, "direction": body["direction"]}


def send(doctype, name, manual=False):
    """（`express.push.send` 遇到账本是 MR.ERP 的单交过来，或「再写一次」）停下 / 失败的回到排队、排一次导入。回结论。

    ``manual``：人在明确的推送入口点的（ERP 的 CC 推送工具 `express.cc_tools`）：这个 MR.ERP 账号被踢过的标记先清掉、这次照常登录
    （:func:`release_kick`，11-09 风险 7）。其余（提交新单、每分钟巡检、选一次后再排、改票面合计放行）都不清，有标记时导入入口快速失败。"""
    row = frappe.db.get_value(doctype, name, ["name", "status", "company", "ledger_state", "ledger_reason"], as_dict=True)
    if not row or row.status != "submitted" or not handles(row.company):
        return {"status": "not_ready"}
    if manual:
        release_kick(row.company)
    if row.ledger_state == express_contracts.QUEUED and row.ledger_reason == contracts.IMPORTING:
        return {"status": "already_queued"}
    if row.ledger_state in (express_contracts.WRITTEN, express_contracts.MISMATCH):
        return {"status": "already_queued"}
    if str(row.ledger_reason or "").startswith(contracts.UNKNOWN):
        return {"status": "held", "reason": contracts.UNKNOWN}  # 结果不明：不自动重推
    _set(doctype, name, {"ledger_state": express_contracts.QUEUED, "ledger_reason": "", "ledger_needed": None,
                         "ledger_target": contracts.LEDGER_MRERP})
    frappe.db.commit()
    dispatch(doctype, name)
    return {"status": "sent"}


def release_kick(company) -> bool:
    """清掉这家公司 MR.ERP 账号的「被踢」标记（`pearnly_core.mrerp.kick_mark`，与 Cowork 同一个标记）：只给人点的推送入口用。"""
    from pearnly_core.mrerp import kick_mark as account_kick
    from pearnly_core.mrerp.session import lock_key

    row = choices.company_row(company)
    lease = connections.lease(row.mrerp_connection) if row and row.mrerp_connection else {}
    if not lease.get("username"):
        return False
    return account_kick.clear(lock_key(lease["system_url"], lease["username"]))


def _claim(doctype, name):
    """行锁里从「排队中」领成「正在导入」并提交；领不到回 None。"""
    row = frappe.db.get_value(doctype, name, ["name", "owner", "company", "status", "ledger_state", "ledger_reason"],
                              as_dict=True, for_update=True)
    if not row or row.status != "submitted" or row.ledger_state != express_contracts.QUEUED \
            or row.ledger_reason == contracts.IMPORTING or not handles(row.company):
        frappe.db.rollback()
        return None
    return row


def run(doctype, name, document):
    """（后台任务，经 `express.push.mrerp_run`）领一张、准备、导入（不持行锁）、记结论。重复排队无副作用。"""
    if not mrerp.enabled():
        return
    # 连网的共用层（requests / openpyxl / Redis）只在导入任务里载入：本模块在只有标准库的离线检查里也要能导入（Express 入口导入它）
    from pearnly_core.mrerp.push.execute import execute
    from pearnly_core.mrerp.session import lock_key
    from pearnly_core.session_lock import operator_holds

    row = _claim(doctype, name)
    if row is None:
        return
    prepared = prepare(doctype, name, document)
    if "failed" in prepared or "hold" in prepared:
        _set(doctype, name, {"ledger_state": express_contracts.FAILED if "failed" in prepared else express_contracts.HELD,
                             "ledger_reason": prepared.get("failed") or prepared.get("hold"),
                             "ledger_needed": json.dumps(prepared.get("needed") or [], ensure_ascii=False)
                             if "hold" in prepared else None})
        frappe.db.commit()
        return
    company = prepared["company"]
    lease = connections.lease(company.mrerp_connection)
    if not lease.get("username") or not lease.get("password"):
        _set(doctype, name, {"ledger_state": express_contracts.HELD, "ledger_reason": contracts.NO_LOGIN})
        frappe.db.commit()
        return
    if operator_holds(lock_key(lease["system_url"], lease["username"])):
        _set(doctype, name, {"ledger_reason": contracts.ACCOUNT_BUSY})  # 等账号：照旧排队，每分钟再看
        frappe.db.commit()
        return
    _set(doctype, name, {"ledger_reason": contracts.IMPORTING, "ledger_sent_at": now_datetime()})
    frappe.db.commit()  # 领取落定、行锁释放，之后才对外请求

    today = datetime.now(ZoneInfo("Asia/Bangkok")).date()
    outcome = execute(prepared["payload"], {"account_set": company.mrerp_account_set},
                      {"username": lease["username"], "password": lease["password"]}, lease["system_url"], today)
    try:
        choices.observe(company.mrerp_connection, company.mrerp_account_set, outcome.get("menus"),
                        outcome.get("masters") or None)
        frappe.db.commit()
    except Exception:  # noqa: BLE001 — 只是给选择用的缓存，存不了不影响导入结论
        frappe.db.rollback()
    values = contracts.settle(outcome)
    if values["ledger_reason"] == contracts.CHOICE:
        fresh = choices.company_row(company.name)
        menu_view, master_view = choices.views(fresh)
        values["ledger_needed"] = json.dumps(contracts.needed(menu_view, master_view, prepared["direction"]),
                                             ensure_ascii=False)
    else:
        values["ledger_needed"] = None
    if frappe.db.get_value(doctype, name, "status") == "void":
        # 导入那几十秒里单据在 ERP 作废了（11-13）：导入成的不记成干净的「已写入」，转人工、单号如实留着（同 Express 已领走的）；
        # 没导入成的清账本栏；结果不明的照旧交人核
        if values["ledger_state"] == express_contracts.WRITTEN:
            values.update(ledger_state=express_contracts.HELD, ledger_reason=express_contracts.VOIDED_AFTER_WRITE,
                          ledger_needed=None)
        elif not values["ledger_reason"].startswith(contracts.UNKNOWN):
            values = {"ledger_state": None, "ledger_reason": "", "ledger_needed": None}
    _set(doctype, name, values)
    frappe.db.commit()
    if values.get("ledger_state") in (express_contracts.WRITTEN, express_contracts.MISMATCH):
        _after_write(company.name)


def _after_write(company):
    """写进了 MR.ERP（含写入核不上）：通知主档整读一次（公司接入第 3 批 `mrerp.masters.after_write`：去重、排进主档队列）。
    那边还没合入就跳过；它出错不改写账结论。"""
    try:
        from pearnly_erp_portal.mrerp import masters
    except ImportError:
        return
    hook = getattr(masters, "after_write", None)
    if hook is None:
        return
    try:
        hook(company)
    except Exception:  # noqa: BLE001 — 主档整读只是后续同步，不影响已落定的写账结论
        frappe.db.rollback()
        frappe.log_error(title="mrerp masters after_write")


def answer(doctype, name, answers) -> bool:
    """单据上选要人选的 MR.ERP 项（``[{kind, subject, value}]``，只认停在单据上的那几项、这一项能选的值）：记在公司上后再排。
    权限由调用方核（同 `express.api.answer`）。全部认得回真。"""
    row = frappe.db.get_value(doctype, name, ["company", "ledger_state", "ledger_needed"], as_dict=True)
    if not row or row.ledger_state != express_contracts.HELD or not handles(row.company):
        return False
    try:
        needed = {(item["kind"], item["subject"]): item for item in json.loads(row.ledger_needed or "[]")}
    except (ValueError, KeyError, TypeError):
        return False
    for given in answers or []:
        item = needed.get((given.get("kind"), given.get("subject"))) if isinstance(given, dict) else None
        value = str(given.get("value") or "").strip() if item else ""
        if not item or value not in {option["code"] for option in item.get("options") or []}:
            return False
        if choices.choose(row.company, item["kind"], item["subject"], value) is None:
            return False
    send(doctype, name)
    return True


def poll():
    """（每分钟，`express.push.poll` 末尾调）等账号的再排；「正在导入」超过 15 分钟的（进程死了）转结果不明。"""
    if not mrerp.enabled():
        return
    stale = now_datetime() - timedelta(minutes=contracts.IMPORTING_MINUTES)
    for doctype in DIRECTIONS:
        rows = frappe.get_all(doctype, filters={"ledger_target": contracts.LEDGER_MRERP,
                                                "ledger_state": express_contracts.QUEUED, "status": "submitted"},
                              fields=["name", "ledger_reason", "ledger_sent_at"], order_by="modified asc",
                              limit_page_length=POLL_MAX)
        for row in rows:
            if row.ledger_reason == contracts.IMPORTING:
                if row.ledger_sent_at and frappe.utils.get_datetime(row.ledger_sent_at) < stale:
                    _set(doctype, row.name, {"ledger_state": express_contracts.HELD,
                                             "ledger_reason": f"{contracts.UNKNOWN}: ERR_IMPORT_INTERRUPTED"})
            else:
                dispatch(doctype, row.name)
        frappe.db.commit()


__all__ = ["answer", "dispatch", "handles", "poll", "prepare", "queue", "release_kick", "run", "send"]
