"""MR.ERP 主档整读进 ERP（公司接入第 3 批，评估 2026-11-14 §3.3）：与 Express 同一个落库（`express.masters.apply`）、同一条
后台队列（``erp_masters``），不另写一份。

- 读：服务端用这条连接的账号登录 MR.ERP，整读这家公司账套的客户 / 供应商 / 物料 / 单位 / 仓库 / 按仓结存
  （`pearnly_core.mrerp.master_export`，导出页与按仓结存报表），不占电脑。会话走共用入口（同账号锁、被踢标记、接上缓存会话）；
  后台读不算人点的：账号被踢过、还没人点过推送时这次不登录，记失败原因，下一轮再试。
- 落：交 `express.masters.apply(..., ledger="mrerp")`：按「公司 + 账本编码」认档，只写有变化的行，没了的停用；档上记来源
  ``ledger``（只读）。公司上的同步状态与 Express 同一组字段（``express_sync_state`` 等，公司页同一处显示）。
- 触发（MR.ERP 没有文件标记可看）：选 / 建公司后一次（`cc_service.create`、`catalog.choose`）、写单后一次（:func:`after_write`，
  写账一侧写成后调）、每小时整读比对一次（:func:`sync_all`）。长期没人用的公司（:func:`idle`）每小时那次不读；它一写单就会
  因写单后那次再读上。
"""
from datetime import timedelta
import logging

import frappe
from frappe.utils import get_datetime, now_datetime

from pearnly_erp_portal import mrerp
from pearnly_erp_portal.express import masters as ledger_masters
from pearnly_erp_portal.mrerp import connection as connections

logger = logging.getLogger(__name__)

COMPANY = "ERP Company"
JOB_TIMEOUT = 1800  # 整读（大账套导出页与结存报表各一次）+ 落库
# 「长期没人用」：这么多天里这家公司没有新单据（采购 / 销售 / 出入库 / 盘点）、也没有接上或换过账套，每小时那次不整读
IDLE_DAYS = 14
ACTIVITY = ("Purchase Document", "ERP Sales Document")
# 写单后那次：上次整读开始不到这么久的不再排（连写几张只读一次；读完落库前写的单由下一轮补上）
AFTER_WRITE_GAP = timedelta(minutes=5)
NO_BOOK = "account_set_missing"  # 目录里没有这家公司的账套（公司页按 Express 那句「没有账套」显示）


def companies(filters=None) -> list:
    """接着 MR.ERP 的公司（启用、选了账套）。"""
    return frappe.get_all(COMPANY, filters={"is_active": 1, "mrerp_enabled": 1, "mrerp_connection": ["is", "set"],  # bounded: 一个租户的公司数
                                            "mrerp_account_set": ["is", "set"], **(filters or {})},
                          fields=["name", "mrerp_connection", "mrerp_account_set", "mrerp_linked_at", "express_synced_at",
                                  "express_sync_state"],
                          limit_page_length=0)


def _job_id(company):
    return f"erp-mrerp-masters:{frappe.local.site}:{company}"


def queue(company, notify=None, manual=False):
    """排一次整读（同一家公司排着的只留一件）。开关关着、公司没接 MR.ERP 的不排。``manual``：人点出来的（从连接添加、公司页
    选账套，`catalog.choose`）——读时先清这个 MR.ERP 账号的被踢标记、照常登录；定时与写单后的不算。"""
    if not mrerp.enabled() or not company:
        return False
    if not frappe.db.get_value(COMPANY, company, "mrerp_enabled"):
        return False
    if notify:
        frappe.cache.hset(ledger_masters.NOTIFY_KEY + company, notify, 1)
    if frappe.db.get_value(COMPANY, company, "express_sync_state") not in (ledger_masters.READING, ledger_masters.SYNCING):
        ledger_masters._record(company, {"state": ledger_masters.QUEUED})
    frappe.enqueue("pearnly_erp_portal.mrerp.masters.run", queue=ledger_masters.lane(), timeout=JOB_TIMEOUT,
                   enqueue_after_commit=True, job_id=_job_id(company), deduplicate=True, company=company,
                   manual=bool(manual))
    return True


def after_write(company):
    """写账一侧把一张单写进 MR.ERP 之后调：排一次整读（推送里自建的客户 / 供应商 / 物料落回 ERP、结存跟上）。
    刚读过（:data:`AFTER_WRITE_GAP` 内）不排；出错只记日志，不影响写账结果。"""
    try:
        row = frappe.db.get_value(COMPANY, company, ["mrerp_enabled", "express_synced_at", "express_sync_state"],
                                  as_dict=True) if company else None
        if not row or not row.mrerp_enabled:
            return False
        if row.express_sync_state in (ledger_masters.QUEUED, ledger_masters.READING):
            return False
        if row.express_synced_at and get_datetime(row.express_synced_at) > now_datetime() - AFTER_WRITE_GAP:
            return False
        return queue(company)
    except Exception:  # noqa: BLE001 — 顺手排的读取，出错不挡写账
        frappe.log_error(title="ERP MR.ERP master read after write not queued")
        return False


def active_companies(names, now=None) -> set:
    """这几家里 :data:`IDLE_DAYS` 天内有新采购 / 销售单的：每类单据一条分组查询（不按公司逐家查）。"""
    if not names:
        return set()
    edge = (now or now_datetime()) - timedelta(days=IDLE_DAYS)
    found = set()
    for doctype in ACTIVITY:
        found.update(frappe.get_all(doctype, filters={"company": ["in", list(names)], "creation": [">", edge]},  # bounded: 按公司分组，至多这几家；# loop-query: 固定两类单据各一条
                                    pluck="company", group_by="company", limit_page_length=0))
    return found


def idle(row, now=None, active=None) -> bool:
    """长期没人用：读过一次（没读过的总要读一次），接上 / 换账套已超过 :data:`IDLE_DAYS` 天，这段时间也没有新的采购 / 销售单。"""
    now = now or now_datetime()
    edge = now - timedelta(days=IDLE_DAYS)
    if not row.get("express_synced_at"):
        return False
    if row.get("mrerp_linked_at") and get_datetime(row["mrerp_linked_at"]) > edge:
        return False
    if active is not None:
        return row["name"] not in active
    return row["name"] not in active_companies([row["name"]], now)


def sync_all():
    """（每小时）接着 MR.ERP、不是长期没人用的公司各排一次整读；整读比对后只写有变化的行。"""
    if not mrerp.enabled():
        return
    rows = [row for row in companies()  # 排着 / 在读 / 在落库的不再排
            if row.express_sync_state not in (ledger_masters.QUEUED, ledger_masters.READING, ledger_masters.SYNCING)]
    active = active_companies([row.name for row in rows])
    for row in rows:
        if not idle(row, active=active):  # loop-query: 传了活跃名单，不再查库
            _enqueue(row.name)  # loop-query: 只排队（每家一件后台任务），状态已在上面一次取回


def _enqueue(company):
    """（每小时那次）排一件整读任务并记「排队中」：调用方已确认开关、公司接 MR.ERP、不在读；不算人点的。"""
    ledger_masters._record(company, {"state": ledger_masters.QUEUED})
    frappe.enqueue("pearnly_erp_portal.mrerp.masters.run", queue=ledger_masters.lane(), timeout=JOB_TIMEOUT,
                   enqueue_after_commit=True, job_id=_job_id(company), deduplicate=True, company=company,
                   manual=False)


def run(company, manual=False):
    """（后台任务）整读这家公司的 MR.ERP 账套并落库。永不抛到队列：结果记在公司上，手动发起的推给发起人。"""
    if not mrerp.enabled():
        return
    row = frappe.db.get_value(COMPANY, company, ["name", "company_name", "is_active", "mrerp_enabled", "mrerp_connection",
                                                 "mrerp_account_set", "mrerp_company"], as_dict=True)
    if not row or not row.is_active or not row.mrerp_enabled or not (row.mrerp_company or row.mrerp_account_set):
        return
    from pearnly_core.mrerp import master_export
    from pearnly_erp_portal.mrerp import catalog

    # 读今天该写的那一本（各年度账套归一家，`catalog.current`）；主档按「公司 + 账本编码」认，换年度不重建
    book = catalog.current(row, frappe.utils.nowdate())
    if book is None:
        ledger_masters._record(company, {"state": ledger_masters.FAILED, "note": NO_BOOK})
        frappe.db.commit()
        ledger_masters._notify(company, {"state": ledger_masters.FAILED})
        return

    ledger_masters._record(company, {"state": ledger_masters.READING})
    frappe.db.commit()
    result = {"state": ledger_masters.FAILED}
    try:
        lease = connections.lease(book["connection"])
        facts, error, stats = master_export.read(lease, book["key"], manual=bool(manual)) if lease \
            else (None, "ERR_NO_CREDS", {})
        if error:
            result = {"state": ledger_masters.FAILED, "note": error}
        else:
            ledger_masters._record(company, {"state": ledger_masters.SYNCING})
            frappe.db.commit()
            counts = ledger_masters.apply(company, str(book["label"])[:140], facts, ledger=ledger_masters.MRERP)
            result = {"state": ledger_masters.READY, "counts": counts}
            logger.info("MR.ERP masters %s: %s", company, {**stats, "landed": counts})
    except Exception:  # noqa: BLE001 — 契约：结果记在公司上
        frappe.db.rollback()
        frappe.log_error(title="ERP MR.ERP master read failed", message=frappe.get_traceback()[-3000:])
        result = {"state": ledger_masters.FAILED, "note": "masters_failed"}
    ledger_masters._record(company, result)
    frappe.db.commit()
    ledger_masters._notify(company, result)

