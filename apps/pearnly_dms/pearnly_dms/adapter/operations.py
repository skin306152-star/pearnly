"""Common scoped DMS session execution for live writes and background collectors.

Cookies are never cached. Reuses existing native account locks, authority gate,
timeouts and error contracts. Foreground previews must not call this module.
"""
from __future__ import annotations
import logging
import time
from typing import Any, Dict, Optional
from pearnly_dms.contracts.endpoint import endpoint_name
from pearnly_dms.adapter.push import build_adapter
from pearnly_dms.line.errors import friendly

logger = logging.getLogger(__name__)


def err(code: str, raw: str = "", response_body: Optional[dict] = None) -> Dict[str, Any]:
    """失败结果。带 `response_body` 是有意的：**光有 error_code 分不清**
    "DMS 拒了"还是"我们复核没查到"，这两件事的处理方式完全不同。"""
    return {
        "ok": False,
        "error_code": code,
        "error_friendly": friendly(code),
        "raw_error": (raw or "")[:300],
        "response_body": {**(response_body or {}), "raw_error": (raw or "")[:300]},
    }


def live_sync_active() -> bool:
    try:
        from pearnly_dms.adapter import live_sync

        return live_sync.enabled()
    except Exception:
        return False


def background_read_account():
    """登记了读档账号就返回它；否则 None（后台读取照旧用连接自己的账号）。"""
    from pearnly_dms.adapter import admin_pool

    return admin_pool.read_account()


def run_logged_in(endpoint: Any, fn, *, authoritative_read: bool = False, attempt: int = 1,
                  background: bool = False, read_only_batch: bool = False,
                  catch_up: bool = False, line_write: bool = False):
    """建适配器 → 登录 → `fn(client, adapter)`。**从不抛异常**。

    会话随 adapter 退出即注销，不缓存 cookie 复用。

    `attempt` / `background`：这一次给多长的"打开登录页"预算（见
    `pearnly_dms.config.pacing`，**所有 DMS 操作共用那一份口径**）：用户那条路按第几次
    尝试走 30/60/90 秒的阶梯；后台那条路不走阶梯、一次给足 90 秒。

    **连不上时返回 `ERR_DMS_UNREACHABLE`**（而不是 `ERR_UNEXPECTED`），上层才给得出
    "重试"按钮；连续失败到阈值会进入冷却，冷却期内**快速失败**，不让每个人再白等
    30–90 秒（同一个 `pacing` 模块，用户与后台同一套）。

    `authoritative_read=True`：**整块 DMS 读取**借管理员会话（配了独立管理员凭据组时），
    销售的挑选/匹配因此看到的是**权威映射**，而不是自己的不完整视图。整块只解析一次
    管理员 transport；未配管理员照样走销售；配了但管理员登录失败 →
    `ERR_DMS_ADMIN_AUTH` **明确失败关闭**，不静默降级。**写操作（上传/提交）不走这个入口。**

    同一档还有一条省时规矩：**权威只读 + 配了独立管理员凭据时不登销售会话**。这一整块
    读取只跑在管理员会话上（销售会话登了也用不到），白付的那一次完整登录在生产实测是
    6–16 秒（新连接卡在 TLS 握手；同一连接复用只要 0.2 秒）。只在**配了独立管理员**时跳：
    没配或与销售同号 → 照旧先登销售会话，单凭据路径行为逐字节不变。跳过之后这一块
    **必须**落在管理员会话上（`require_admin`）：拿不到就按 `ERR_DMS_ADMIN_AUTH` 明确失败，
    绝不回落成"没登录的主会话读出一片空"。

    `catch_up=True`：**操作员自己发起的那一次**（不是后台任务）在动作做完之后，
    顺手把 DMS 使用记录里的增量补回缓存（`snapshots/change_sync`）——用同一个会话，
    不额外登录、不额外抢账号；同步失败只记日志，绝不改这次动作的结果。

    `line_write=True`：**只由**两个 LINE 写入执行体传（订车 `booking.service.book_in_session`、
    建档 / 改档 `intake.service.push_idcard_fields`），其余调用方行为不变（2026-10-13）：

    · **登录页都没打开**（`DmsLoginPageUnreachableError`：账号密码还没提交、业务函数还没开始）
      时，同一个任务用下一档预算自动再试一次 —— 对方只是一时慢，操作员就看不到失败。只此一次，
      而且要：当前档还没到第三档、第一次已用时间不超过 `pacing.AUTO_RETRY_WITHIN_SECONDS`、
      再试前冷却没打开。"点了登录、对方没回应"不再试（对方可能已留下会话，马上再登可能被判
      重复登录）；业务函数一旦开始，绝不自动再试。再试仍失败时结果带 `login_attempt`（实际用到
      第几档），建档的重试阶梯据此接着走，总次数仍不超过三次。
    · 后台任务时限（600 秒）到点打断这一次时，报可重试的 `ERR_DMS_TECHNICAL`，不说成
      "服务器内部错误"，也不计入冷却。已经提交过的，订车与客户写入各自的登记会把它改成
      "结果未知"并拦住重放，不会叫人重做。
    """
    once = dict(authoritative_read=authoritative_read, background=background,
                read_only_batch=read_only_batch, catch_up=catch_up, line_write=line_write)
    if not line_write:
        return _run_once(endpoint, fn, attempt=attempt, probe={}, **once)
    from pearnly_dms.config import pacing

    started, probe = time.monotonic(), {}
    out = _run_once(endpoint, fn, attempt=attempt, probe=probe, **once)
    if (not isinstance(out, dict) or out.get("ok") or out.get("error_code") != "ERR_DMS_UNREACHABLE"
            or not probe.get("login_page_unopened") or probe.get("started")
            or pacing.clamp(attempt) >= pacing.MAX_ATTEMPTS
            or time.monotonic() - started > pacing.AUTO_RETRY_WITHIN_SECONDS
            or pacing.cooldown_left(endpoint_name(endpoint))):
        return out
    following = pacing.clamp(attempt) + 1
    logger.warning("[dms] DMS login page did not open; retrying once in the same job (budget %s ms)",
                   pacing.budget_ms(following))
    out = _run_once(endpoint, fn, attempt=following, probe={}, **once)
    return out if not isinstance(out, dict) or out.get("ok") else {**out, "login_attempt": following}


def _is_job_timeout(exc: BaseException) -> bool:
    """后台任务时限到点（RQ 的 `JobTimeoutException`）。按类名认，不在这里 import rq。"""
    return any(cls.__name__ == "JobTimeoutException" for cls in type(exc).__mro__)


def _run_once(endpoint: Any, fn, *, authoritative_read: bool, attempt: int, background: bool,
              read_only_batch: bool, catch_up: bool, line_write: bool, probe: Dict[str, bool]):
    """`run_logged_in` 的一次完整尝试（建适配器 → 登录 → `fn`），从不抛异常。

    `probe` 只给 `run_logged_in` 判断能不能自动再试：`login_page_unopened` = 主会话登录时
    登录页都没打开；`started` = 业务函数已经开始。"""
    name = endpoint_name(endpoint)
    from pearnly_dms.config import pacing

    # 熔断：最近连着几次都连不上就先别敲门 —— 直接回同一个错误码（上层照样给得出"重试"），
    # 但不让每个人再白等一次 30–90 秒。冷却期随时间自解，成功一次立刻归零。
    wait = pacing.cooldown_left(name)
    if wait:
        return err("ERR_DMS_UNREACHABLE",
                   f"circuit open: {wait}s left ({pacing.summary(name).get('failures')} consecutive failures)")
    adapter, build_err = build_adapter(name)
    if build_err:
        return err(build_err["error_code"], build_err["raw"])
    # 这一次的"打开登录页"预算（默认那档就比老的 15 秒宽）。
    adapter.page_timeout_ms = pacing.budget_ms(attempt, background=background)
    adapter.read_only_batch = bool(read_only_batch)
    # 权威只读 + 独立管理员 = 这一块只用管理员会话：适配器据此**只锁管理员账号**，
    # 不去占销售账号（以前两个都锁，后台读取会挡住销售本人的操作）。
    adapter.skip_sales_session = bool(authoritative_read) and bool(
        getattr(adapter, "has_distinct_admin_creds", False))
    if read_only_batch:
        adapter.page_timeout_ms = min(adapter.page_timeout_ms, 15000)
    # 后台读取（每分钟同步、整组重读、首次读全档、凌晨回读）登记了读档账号就只用它，
    # 不去占连接自己的管理员账号 —— 网页上可能有人正用着它（用户 2026-09-27）。
    if authoritative_read and background and read_only_batch:
        reader = background_read_account()
        if reader:
            adapter.use_account(reader.username, reader.password)
    try:
        from pearnly_dms.adapter.session import (
            DmsAdminAuthError,
            DmsAuthError,
            DmsLoginPageUnreachableError,
            DmsTechnicalError,
            DmsUnreachableError,
        )
        from pearnly_dms.contracts.client_base import DmsClientError

        try:
            with adapter:
                # **权威只读 + 独立管理员 = 不登销售会话**：这一整块读取会被
                # `authoritative_read_session` 换成管理员 transport，销售会话登了也用不到，
                # 白付一次完整登录（生产实测每次 6–16 秒，几乎全花在新连接的 TLS 握手上）。
                # 账号锁不受影响：`with adapter` 本来就同时锁销售与管理员两个登录槽。
                skip_sales_login = authoritative_read and bool(
                    getattr(adapter, "has_distinct_admin_creds", False)
                )
                if not skip_sales_login:
                    try:
                        adapter.login()
                    except (DmsAuthError, DmsTechnicalError) as exc:
                        if adapter.concurrent_login_detected:
                            return err("ERR_DMS_CONCURRENT_LOGIN", adapter.last_dialog)
                        # 登录页都没打开：账号密码还没提交、业务函数也还没开始（见 `run_logged_in`）。
                        probe["login_page_unopened"] = isinstance(exc, DmsLoginPageUnreachableError)
                        raise
                try:
                    client = adapter.client()

                    def run_and_catch_up():
                        probe["started"] = True  # 业务函数开始之后绝不自动再试
                        out = fn(client, adapter)
                        # 白天同步开着时（`adapter/live_sync`，每分钟看一次使用记录），
                        # 不在操作员等待的这条路上再顺手翻日志 —— 那最多要 6 秒、还占着账号。
                        if catch_up and not live_sync_active():
                            from pearnly_dms.adapter import change_sync

                            change_sync.catch_up(endpoint, client=client)
                        return out

                    if authoritative_read:
                        from pearnly_dms.adapter.admin_read import authoritative_read_session

                        # 跳过了销售登录 → 这一块**必须**是管理员会话（拿不到就失败关闭，
                        # 不许在没登录的主会话上读出一片空还当成"查无此客"）。
                        with authoritative_read_session(client, require_admin=skip_sales_login):
                            out = run_and_catch_up()
                    else:
                        out = run_and_catch_up()
                except DmsClientError as exc:
                    # 提交类失败带着"已经发出去了"的证据（`response_body.submitted`）：
                    # 这时候报"被别人顶下线"会误导 —— 单可能已经建出来了。
                    if adapter.concurrent_login_detected and not getattr(
                        exc, "response_body", {}
                    ).get("submitted"):
                        return err("ERR_DMS_CONCURRENT_LOGIN", adapter.last_dialog)
                    return err(exc.error_code or "ERR_DMS_TECHNICAL", str(exc),
                               getattr(exc, "response_body", None))
                # 收尾再查一次：中途被别人顶下线，结果不能算数。
                # 但**已经拿到单号**的结果算数 —— 单确实建出来了；客户写入已经逐字段读回核对
                # 成功（带客户号的成功结果）也算数 —— 写进去了，改判成"被顶下线"只会让它变成
                # 结果不明、把这位客户挡住。
                if adapter.concurrent_login_detected and not (
                    isinstance(out, dict)
                    and (out.get("booking_id") or (out.get("success") and out.get("customer_id")))
                ):
                    return err("ERR_DMS_CONCURRENT_LOGIN", adapter.last_dialog)
                # 走到了这里 = 真的连上并用完了（业务上被拒也算连上）：计数归零，
                # 顺手记"这条连接刚被用过" —— 后台预热据此决定追多紧。
                pacing.note_success(name, mark_used=not background)
                return out
        except DmsAdminAuthError as exc:
            # admin 凭据组登录失败（≠ 用户会话）—— **子类必须先于基类捕获**
            return err("ERR_DMS_ADMIN_AUTH", f"{type(exc).__name__}: {exc}")
        except DmsAuthError as exc:
            return err("ERR_DMS_AUTH", f"{type(exc).__name__}: {exc}")
        # 「连不上」是技术失败的**子类**，必须排在它前面 —— 上层要靠这个码给重试按钮。
        except DmsUnreachableError as exc:
            pacing.note_failure(name, "ERR_DMS_UNREACHABLE")
            return err("ERR_DMS_UNREACHABLE", f"{type(exc).__name__}: {exc}")
        except DmsTechnicalError as exc:
            pacing.note_failure(name, "ERR_DMS_TECHNICAL")
            return err("ERR_DMS_TECHNICAL", f"{type(exc).__name__}: {exc}")
        except DmsClientError as exc:
            return err(exc.error_code or "ERR_DMS_TECHNICAL", str(exc),
                       getattr(exc, "response_body", None))
    except Exception as exc:  # noqa: BLE001
        from pearnly_core.session_lock import SessionLockUnavailable
        from pearnly_dms.adapter.read_priority import ReadDeferred
        if isinstance(exc, ReadDeferred):
            return {**err("ERR_DMS_SNAPSHOT_BUSY"), "busy": True}
        if isinstance(exc, SessionLockUnavailable):
            # 等账号等到超时：是"人太多在排队"，不是我们服务器坏了。
            return {**err("ERR_DMS_BUSY", str(exc)), "busy": True}
        if line_write and _is_job_timeout(exc):
            # 后台任务时限到点：对方太慢，不是我们服务器坏了 —— 给可重试的码，不计入冷却。
            # 已经提交过的由订车 / 客户写入各自的登记改判"结果未知"（见 `run_logged_in`）。
            logger.warning("DMS write job reached the background time limit", exc_info=True)
            return err("ERR_DMS_TECHNICAL", f"{type(exc).__name__}: {exc}")
        logger.exception("DMS 操作意外失败")
        return err("ERR_UNEXPECTED", f"{type(exc).__name__}: {exc}")
