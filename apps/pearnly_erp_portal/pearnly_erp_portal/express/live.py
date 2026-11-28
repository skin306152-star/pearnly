"""账本变化即时同步：ERP 一侧（评估与主会话审定 docs/work/2026-11-22-ledger-instant-sync.md）。

- **CC 桌面版报变化**（共用通道心跳的 ``ledger_changes``，`pearnly_core.cc_desktop.machine`）→ :func:`ledger_changed`：按账套找
  现用它的公司，主档 / 结存类变了就当场发起读取（`masters.kick`，不等 10 分钟看标记那轮），应收变了且这本账套在用收款就整本
  刷新未收清发票（`collection.snapshot.request`）。只排读取，不在心跳里读、不等电脑。
- **要电脑盯哪几本** → :func:`watch_books`：已接 Express 的公司（启用）的现用账套里这台电脑够得到的；心跳回复带给它。
- **用时再核一次**（不阻塞）→ :func:`touch`：选择框搜主档、打开主档列表时（主档的权限查询 `masters.access.master_query`，按当前
  公司），同一家公司 :data:`TOUCH_GAP` 秒内只一次，排后台任务 :func:`check`：Express 公司请电脑看一眼标记（`masters.check_soon`），
  MR.ERP 公司在会话还热时探测一次列表（`mrerp.masters.probe_soon`）；变了就排读，读完落库后原生 ``list_update`` 让打开的列表刷新。
  写单前的核对仍按现有写前检查（不在这里放宽）。

10 分钟看标记、每小时整读照旧兜底（旧版 CC 桌面版、监视失效、文件开着原地改要等 Express 关表）。
"""
import frappe

from pearnly_core.ledger_sync import contracts as ledger

# 同步与账套挑选只在用到时载入：:func:`touch` 挂在主档的权限查询上（每次列表 / 选择框都调），只读一次缓存
COMPANY = "ERP Company"
TOUCH_KEY = "erp-ledger-touch:"
TOUCH_GAP = 120  # 秒：同一家公司用时再核的最小间隔


def _linked_rows():
    """接了 Express、启用的公司（不含接 MR.ERP 的）。"""
    from pearnly_erp_portal.express import masters

    return [row for row in masters.linked_companies() if not frappe.db.get_value(COMPANY, row.name, "mrerp_enabled")]  # loop-query: 每家公司一个值（只在算要盯的账套、5 分钟缓存一次）


def _current(row):
    from frappe.utils import nowdate

    from pearnly_erp_portal.express import target

    if target.masters_choice(row, nowdate()):
        return None  # 同期几份没选过：主档同步停着等人选，不盯
    return target.current(row, nowdate())


def watch_books(device) -> list:
    """这台电脑要盯着的账套键：已接公司的现用账套（同期几份没选过的不盯）。共用层只回这台电脑够得到的那几本。"""
    keys = []
    for row in _linked_rows():
        book = _current(row)  # loop-query: 每家公司一次（5 分钟缓存一次）
        if book:
            keys.append(book["key"])
    return sorted(set(keys))


def _companies_of(book_key) -> list:
    """现用这本账套的公司：按账套上的税号 + 分店先缩到几家，再核现用的是不是这本。"""
    from pearnly_erp_portal.express import contracts

    row = frappe.db.get_value("Pearnly CC Book", {"book_key": book_key}, ["tax_id", "branch"], as_dict=True)
    if not row or not contracts.tax13(row.tax_id):
        return []
    found = []
    for company in frappe.get_all(COMPANY, filters={"tax_id": row.tax_id, "is_active": 1},  # bounded: 同一税号的几家（分店）
                                  fields=["name", "tax_id", "branch", "mrerp_enabled"]):
        if company.mrerp_enabled:
            continue
        book = _current(company)  # loop-query: 同一税号的几家
        if book and book["key"] == book_key:
            found.append((company, book))
    return found


def ledger_changed(device, changes) -> None:
    """CC 桌面版报 ``{book_key: {类别}}``：主档 / 结存类 → `masters.kick`；应收 → 在用收款的账套整本刷新。"""
    from pearnly_erp_portal.express import masters, push

    # 有界：一拍心跳至多 `ledger_sync.contracts.MAX_BOOKS` 本（实际是这台电脑盯着的那几本），每本只到同税号的几家
    for book_key, kinds in (changes or {}).items():
        for company, book in _companies_of(book_key):  # loop-query: 每本账套一次，至多 MAX_BOOKS 本
            if not push.linked(company.name):  # loop-query: 同税号的几家
                continue
            if kinds & ledger.MASTER_KINDS:
                masters.kick(company.name)  # loop-query: 同税号的几家，每家一次排读
            if ledger.RECEIVABLE in kinds:
                _receivables(company.name, book)  # loop-query: 同税号的几家，在用收款的才发


def _receivables(company, book) -> None:
    from pearnly_erp_portal.collection import snapshot

    if snapshot.in_use(book["key"]):
        snapshot.request(company, book)


def touch(company) -> None:
    """用时再核一次（不阻塞）：同一家公司 :data:`TOUCH_GAP` 秒内只排一次后台 :func:`check`。只读一次缓存，不写库
    （可能在 GET 请求里，GET 不提交）。出错不影响调用方的查询。"""
    if not company:
        return
    try:
        if frappe.cache.get_value(TOUCH_KEY + company):
            return
        frappe.cache.set_value(TOUCH_KEY + company, 1, expires_in_sec=TOUCH_GAP)
        from rq.exceptions import InvalidJobOperation

        from pearnly_erp_portal.express import masters

        arguments = {"queue": masters.lane(), "timeout": 300, "job_id": f"erp-ledger-touch:{frappe.local.site}:{company}",
                     "company": company}
        try:
            frappe.enqueue("pearnly_erp_portal.express.live.check", deduplicate=True, **arguments)
        except InvalidJobOperation:
            # 去重时同名旧任务刚跑完、状态键刚过期（11-28 生产 Error Log）：锁定版本里 `frappe.enqueue(deduplicate=True)`
            # 先 `get_job` 取到旧任务（状态已结束），再 `job.delete()`；RQ 2.6.1 `Job.delete` → `_remove_from_registries`
            # → `is_finished` → `get_status()`（默认 refresh）重读状态键，这时任务哈希已过期、读回空，抛
            # `InvalidJobOperation("Failed to retrieve status …")`。旧任务已不在，不带去重再排一次；`check` 只是请电脑看一眼
            # 标记，重复一次无害。
            frappe.enqueue("pearnly_erp_portal.express.live.check", **arguments)
    except Exception:  # noqa: BLE001 — 只是顺手核一次
        frappe.log_error(title="ERP ledger check-at-use not queued")


def check(company) -> None:
    """（后台任务）用时再核：Express 公司请电脑看标记，MR.ERP 公司在会话还热时探测列表。"""
    if frappe.db.get_value(COMPANY, company, "mrerp_enabled"):
        from pearnly_erp_portal.mrerp import masters as mrerp_masters

        mrerp_masters.probe_soon(company)
    else:
        from pearnly_erp_portal.express import masters

        masters.check_soon(company)
    frappe.db.commit()


__all__ = ["check", "ledger_changed", "touch", "watch_books"]
