# -*- coding: utf-8 -*-
"""连对方 DMS 要等多久、等不到之后怎么退 —— **所有 DMS 操作共用这一份口径**。

## 为什么要有这个共用模块（2026-09-23 定的）

对方那台主机对新连接要愣 **6–16 秒**（生产实测：明文 HTTP 也慢，域名解析与建连接都是
几十毫秒，卡在"对方服务器迟迟不应答"）。这件事**不是一个功能的问题**：用户在 LINE 里
每一步要碰 DMS 的动作（查重、建档、改档、订车、查单）都会撞上它，后台的每小时/每分钟
任务也会。所以"等多久、等不到怎么办"只写一次，所有入口共用：

    · 用户按的那条路（有人等着看结果）→ `budget_ms(attempt)`：30 → 60 → 90 秒，配合
      LINE 那颗「重试」按钮，第三次仍失败才说「连不上第三方 DMS 系统，请联系管理员」；
    · 后台那条路（没人等着）→ `budget_ms(background=True)`：一次给足 90 秒，不搞阶梯。

## 失败退避（熔断）

连续失败到 `FAILURE_THRESHOLD` 次就**别再敲门**：冷却期直接快速失败，而不是让每个人
再白等 30–90 秒。冷却期逐级拉长（1 → 5 → 15 → 30 分钟封顶），成功后立刻归零。

**只对"连不上"这类失败计数**：账号错、被顶下线说明**对方好好地在**，那是凭据问题，
把它算进"对方连不上"会让所有人被误挡（这两类在调用方是分开的错误码）。

## 只读缓存 / 预热

`used_recently()` 记着"最近有没有人真的用过这条连接"，后台预热据此决定要不要保持热：
有人用的时候按 `BACKGROUND_MAX_AGE_SECONDS`（45 秒）追，没人用的时候降到
`IDLE_MAX_AGE_SECONDS`（10 分钟）——**没有理由在半夜每分钟敲人家一次**。

模块顶层不 import frappe（纯函数要能在任何环境里跑、也要能被离线断言覆盖）；
缓存访问一律懒引。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── 预算（毫秒） ─────────────────────────────────────────────────────────────

#: 用户按的这条路：第 1 / 2 / 3 次尝试，各自"打开登录页"的上限。
#: 第一次就 30 秒而不是老默认的 15 秒：全新连接的握手实测到过 24 秒。
ATTEMPT_TIMEOUT_MS = (30_000, 60_000, 90_000)

#: 用户最多试几次（= 上面那几档的数量）。第三次仍失败就只说"联系管理员"，不再给按键。
MAX_ATTEMPTS = len(ATTEMPT_TIMEOUT_MS)

#: 后台那条路：没人等着，一次给足（比阶梯的第三档还宽一点，减少无谓重跑）。
BACKGROUND_TIMEOUT_MS = 90_000

#: 可以重试的错误码：**连不上对方系统**这一类（用户那颗「重试」按钮就按这个给）。
# `ERR_DMS_BUSY`：等账号等到超时（人多排队）。锁没拿到就不会发出任何请求，重试是安全的。
RETRYABLE = frozenset({"ERR_DMS_UNREACHABLE", "ERR_DMS_TECHNICAL", "ERR_DMS_BUSY"})

# ── 熔断 ─────────────────────────────────────────────────────────────────────

#: 连续失败这么多次才熔断。取 3 = 与用户那条路的阶梯同宽：
#: 他连着点满三次都没成，说明真的不是抖一下。
FAILURE_THRESHOLD = 3

#: 熔断后的冷却时长，按连续失败次数取（最后一档封顶）。逐级拉长，别一分钟又回来敲。
COOLDOWN_STEPS = (60, 300, 900, 1800)

#: 这条连接多久算"刚用过"（后台预热据此决定追多紧）。
USED_TTL_SECONDS = 1800

#: 没人用的时候，主档最多允许旧到这个程度（10 分钟）才去刷一次。
IDLE_MAX_AGE_SECONDS = 600

_STATE_TTL_SECONDS = 7200
_KEY = "pearnly:dms:pacing:v1:{endpoint}"


# ── 纯函数（离线可测） ───────────────────────────────────────────────────────


def _state_store():
    """不淘汰的状态存储（``pearnly_core.state``；用到时才导入，本模块保持不依赖框架导入）。"""
    from pearnly_core.execution import state as pearnly_state

    return pearnly_state.store()


def clamp(attempt: Any) -> int:
    """尝试次数落在 1..3。脏值（None / 空串 / 非数字）按第 1 次算。"""
    try:
        value = int(attempt)
    except (TypeError, ValueError):
        value = 1
    return max(1, min(len(ATTEMPT_TIMEOUT_MS), value))


def budget_ms(attempt: Any = 1, *, background: bool = False) -> int:
    """这一次"打开登录页"给多长预算。后台不走阶梯。"""
    if background:
        return BACKGROUND_TIMEOUT_MS
    return ATTEMPT_TIMEOUT_MS[clamp(attempt) - 1]


def is_retryable(code: Any) -> bool:
    """连不上/技术失败才给重试；对方明确拒绝不给（再点几次还是同样的拒绝）。"""
    return str(code or "") in RETRYABLE


def cooldown_seconds(failures: Any) -> int:
    """连续失败 N 次之后该冷却多久。0 次 = 不冷却。"""
    try:
        count = int(failures)
    except (TypeError, ValueError):
        count = 0
    if count < FAILURE_THRESHOLD:
        return 0
    index = min(count - FAILURE_THRESHOLD, len(COOLDOWN_STEPS) - 1)
    return COOLDOWN_STEPS[index]


def should_warm(used_recently: bool, *, background_max_age: float, now: float = 0.0,
                age_seconds: float = 0.0) -> bool:
    """后台要不要去刷这条连接的主档。

    有人刚用过 → 按 45 秒的口径追（点到就是热的）；
    没人用 → 降频到 10 分钟一次（省掉半夜每分钟白敲对方一次）。
    """
    limit = float(background_max_age) if used_recently else float(IDLE_MAX_AGE_SECONDS)
    return float(age_seconds) > limit


# ── 状态（Redis，懒引 frappe） ───────────────────────────────────────────────


def _key(endpoint: Any) -> str:
    name = endpoint if isinstance(endpoint, str) else ""
    if not name and isinstance(endpoint, dict):
        name = str(endpoint.get("name") or endpoint.get("id") or "")
    if not name:
        name = str(getattr(endpoint, "name", "") or "")
    return _KEY.format(endpoint=name.strip())


def _read(endpoint: Any) -> Dict[str, Any]:
    try:
        import frappe

        blob = _state_store().get_value(_key(endpoint), use_local_cache=False)
    except Exception:
        logger.warning("[dms pacing] 读状态失败，按「没失败过」处理", exc_info=True)
        return {}
    return blob if isinstance(blob, dict) else {}


def _write(endpoint: Any, state: Dict[str, Any]) -> None:
    try:
        import frappe

        _state_store().set_value(_key(endpoint), state, expires_in_sec=_STATE_TTL_SECONDS)
    except Exception:
        logger.warning("[dms pacing] 写状态失败（不影响本次结果）", exc_info=True)


def mark_used(endpoint: Any) -> None:
    """记一笔"这条连接刚被真的用过" —— 后台预热据此决定追多紧。"""
    state = _read(endpoint)
    state["used_at"] = time.time()
    _write(endpoint, state)


def note_success(endpoint: Any, *, mark_used: bool = True) -> None:
    """连上了（哪怕业务上被拒）→ 计数归零、冷却解除。"""
    state = _read(endpoint)
    used_at = time.time() if mark_used else state.get("used_at", 0)
    if not state.get("failures") and not state.get("until"):
        state["used_at"] = used_at
        _write(endpoint, state)
        return
    _write(endpoint, {**state, "failures": 0, "until": 0, "used_at": used_at,
                      "last_code": "", "recovered_at": time.time()})


def note_failure(endpoint: Any, code: str = "") -> Dict[str, Any]:
    """记一次"连不上"。到阈值就开冷却，冷却时长按连续失败次数逐级拉长。"""
    state = _read(endpoint)
    failures = int(state.get("failures") or 0) + 1
    cooldown = cooldown_seconds(failures)
    state.update({"failures": failures, "last_code": str(code or ""),
                  "failed_at": time.time(), "until": (time.time() + cooldown) if cooldown else 0})
    _write(endpoint, state)
    if cooldown:
        logger.warning("[dms pacing] %s 连续失败 %s 次，冷却 %s 秒", _key(endpoint), failures, cooldown)
    return state


def cooldown_left(endpoint: Any) -> int:
    """还要冷却多少秒（0 = 可以试）。"""
    try:
        until = float(_read(endpoint).get("until") or 0)
    except (TypeError, ValueError):
        until = 0.0
    return max(0, int(until - time.time()))


def used_recently(endpoint: Any, within: int = USED_TTL_SECONDS) -> bool:
    try:
        used_at = float(_read(endpoint).get("used_at") or 0)
    except (TypeError, ValueError):
        used_at = 0.0
    return used_at > 0 and (time.time() - used_at) <= float(within)


def summary(endpoint: Any) -> Dict[str, Any]:
    """给日志/诊断用的一眼状态。"""
    state = _read(endpoint)
    return {"failures": int(state.get("failures") or 0),
            "cooldown_left": cooldown_left(endpoint),
            "used_recently": used_recently(endpoint),
            "last_code": state.get("last_code") or ""}


__all__ = [
    "ATTEMPT_TIMEOUT_MS", "MAX_ATTEMPTS", "BACKGROUND_TIMEOUT_MS", "RETRYABLE", "FAILURE_THRESHOLD",
    "COOLDOWN_STEPS", "USED_TTL_SECONDS", "IDLE_MAX_AGE_SECONDS",
    "budget_ms", "clamp", "cooldown_left", "cooldown_seconds", "is_retryable",
    "mark_used", "note_failure", "note_success", "should_warm", "summary", "used_recently",
]
