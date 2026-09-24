"""Common scoped DMS session execution for live writes and background collectors.

Cookies are never cached. Reuses existing native account locks, authority gate,
timeouts and error contracts. Foreground previews must not call this module.
"""
from __future__ import annotations
import logging
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


def run_logged_in(endpoint: Any, fn, *, authoritative_read: bool = False, attempt: int = 1,
                  background: bool = False, read_only_batch: bool = False):
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
    """
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
    if read_only_batch:
        adapter.page_timeout_ms = min(adapter.page_timeout_ms, 15000)
    try:
        from pearnly_dms.adapter.session import (
            DmsAdminAuthError,
            DmsAuthError,
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
                    except (DmsAuthError, DmsTechnicalError):
                        if adapter.concurrent_login_detected:
                            return err("ERR_DMS_CONCURRENT_LOGIN", adapter.last_dialog)
                        raise
                try:
                    client = adapter.client()
                    if authoritative_read:
                        from pearnly_dms.adapter.admin_read import authoritative_read_session

                        # 跳过了销售登录 → 这一块**必须**是管理员会话（拿不到就失败关闭，
                        # 不许在没登录的主会话上读出一片空还当成"查无此客"）。
                        with authoritative_read_session(client, require_admin=skip_sales_login):
                            out = fn(client, adapter)
                    else:
                        out = fn(client, adapter)
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
                # 但**已经拿到单号**的结果算数 —— 单确实建出来了。
                if adapter.concurrent_login_detected and not (
                    isinstance(out, dict) and out.get("booking_id")
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
        from pearnly_dms.adapter.read_priority import ReadDeferred
        if isinstance(exc, ReadDeferred):
            return {**err("ERR_DMS_SNAPSHOT_BUSY"), "busy": True}
        logger.exception("DMS 操作意外失败")
        return err("ERR_UNEXPECTED", f"{type(exc).__name__}: {exc}")
