"""MR.ERP 账套目录采集的网络那一半（只读、不碰数据库；多产品共用）：登录、列账套、试选账套验凭据，补每个年度库的期间与税号。

函数体与常量原样来自 Cowork `connections/mrerp_collector.py`（2026-11-03 下沉，评估 2026-11-02 §14.6）：Cowork 的采集任务（租约、
快照、刷新记录留在 Cowork）与 ERP（`pearnly_erp_portal.mrerp.catalog`）调同一份。与推送共用同一把账号锁；凭据由调用方交进来。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from pearnly_core.session_lock import SessionLockUnavailable, account_lock
from pearnly_core.mrerp import catalog as mrerp_catalog
from pearnly_core.mrerp.session import MrErpSession, lock_key
from pearnly_core.mrerp.session import SessionError as CollectError

logger = logging.getLogger(__name__)

RETRY_DELAY_SECONDS = 2.0
LOCK_WAIT_SECONDS = 120  # 等推送释放同账号的会话；短于采集租约
# 每个年度库的期间与税号（`_read_details`）最多读这么久：租约 180 秒，登录与列账套之外留够余量；读不完的年度（旧的在后）
# 这次没有起止日期，下次刷新再读。
DETAILS_SECONDS = 90


def _reading_order(rows, current):
    """先读已经切进去的那个年度（省一次切库），其余按 comidyear 从大到小（MR.ERP 新建的年度号更大）：读不完时先保住新的。"""
    rest = sorted((row for row in rows if (row["comidyear"], row["seldb"]) != current),
                  key=lambda row: int(row["comidyear"]) if str(row.get("comidyear") or "").isdigit() else -1,
                  reverse=True)
    return [row for row in rows if (row["comidyear"], row["seldb"]) == current][:1] + rest


def _read_details(session, rows, *, current=None):
    """每个年度库的本年起止（`mrerp_catalog.PERIOD_PAGE`）写进行的 ``period_start`` / ``period_end``；每家公司在第一个读到的
    年度库里读一次总店税号（`MasterReader.company_tax`，同 T2 推送时的读法），有效的写进这家公司各年度行的 ``tax_id``。
    只读，只在刷新账套时做（不在推送路上）；每个年度库多切一次库（两次请求）、读一页（税号每家公司多三次请求）。哪个年度
    读不到就留空、接着读下一个，不让整次刷新失败；超过 ``DETAILS_SECONDS`` 就停（旧的年度在后）。耗时记日志。"""
    from pearnly_core.mrerp.master_reader import MasterReader

    started = time.monotonic()
    taxes, read, budget_hit = {}, 0, False
    for row in _reading_order(rows, current):
        if time.monotonic() - started > DETAILS_SECONDS:
            budget_hit = True
            break
        key = (row["comidyear"], row["seldb"])
        try:
            if key != current:
                session.switch(*key)
                current = key
            page = session.request("GET", mrerp_catalog.PERIOD_PAGE,
                                   headers={"Referer": f"{session.base}/login/mainmenu.php"})
            row["period_start"], row["period_end"] = mrerp_catalog.parse_periods(page.text or "")
            company = row.get("company") or ""
            if company and company not in taxes:
                taxes[company] = mrerp_catalog.company_tax(MasterReader(session.request, session.base).company_tax())
            read += 1
        except CollectError as exc:
            logger.info("MR.ERP year %s details not read: %s", key, exc.code)
            if exc.code == "ERR_AUTH":
                break  # 会话没了：后面的年度也读不到，已读到的照样发布
        except Exception:  # noqa: BLE001 — 只是补充资料，读不到不影响目录本身
            logger.exception("MR.ERP year %s details raised", key)
    for row in rows:
        if taxes.get(row.get("company") or ""):
            row["tax_id"] = taxes[row["company"]]
    logger.info("[mrerp-catalog] details years=%d/%d companies=%d seconds=%.1f%s", read, len(rows), len(taxes),
                time.monotonic() - started, " budget_hit" if budget_hit else "")


def collect(lease):
    """登录并列账套；网络类失败隔 2 秒重试一次。永不抛：失败写成错误码。"""
    result = {}
    for attempt in range(2):
        result = _collect_once(lease)
        if not result.get("error_code") or result["error_code"] not in mrerp_catalog.TRANSIENT_ERRORS:
            return result
        if attempt == 0:
            time.sleep(RETRY_DELAY_SECONDS)
    return result


def _collect_once(lease):
    if not lease.get("username") or not lease.get("password"):
        return {"error_code": "ERR_NO_CREDS"}
    # 与推送共用同一 MR.ERP 账号：老 PHP 单账号单会话，并发登录会把正在导入的推送踢掉。
    try:
        with account_lock(lock_key(lease["system_url"], lease["username"]), timeout_sec=LOCK_WAIT_SECONDS):
            return _collect_locked(lease)
    except SessionLockUnavailable:
        return {"error_code": "ERR_TECHNICAL"}


def _collect_locked(lease):
    observed_at = datetime.now(timezone.utc)
    try:
        session = MrErpSession(lease["system_url"], lease["username"], lease["password"])
    except CollectError as exc:
        return {"error_code": exc.code}
    try:
        rows = session.account_sets()
        if rows:
            # 选公司页任何会话都能列出账套，只有真进一个账套才验得出账号密码（旧版同法）。
            session.select(rows[0]["comidyear"], rows[0]["seldb"])
            _read_details(session, rows, current=(rows[0]["comidyear"], rows[0]["seldb"]))
        return {"rows": rows, "observed_at": observed_at}
    except CollectError as exc:
        logger.info("MR.ERP catalog collection failed: %s", exc)
        return {"error_code": exc.code}
    except Exception:  # noqa: BLE001 — 契约：采集失败只落错误码，不冒到任务队列
        logger.exception("MR.ERP catalog collection raised")
        return {"error_code": "ERR_UNEXPECTED"}
    finally:
        session.close()
