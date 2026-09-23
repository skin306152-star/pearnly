# -*- coding: utf-8 -*-
"""连不上对方 DMS 时的重试预算。

用户 2026-09-23 口径（原话）：

    超时第一次失败点加按钮重试，重试同步加时长，第三次还失败同步加重试，再加时长，
    最多三次，还失败就连不上第三方 dms 系统，请联系管理员

落成三条规则：

1. **不自动重试，把决定权交回给用户**：一次尝试最坏要等 90 秒，自动连试三次就是
   几分钟静默 —— 操作员盯着一个没反应的窗口，比"点一下再试"更糟。
2. **每次重试把超时拉长**（`ATTEMPT_TIMEOUT_MS`）。为什么第一次就比老的 15 秒宽：
   2026-09-23 生产日志里那台 DMS 的**全新 TLS 握手实测要 24 秒**（会话复用才 0.2 秒），
   15 秒预算对"每次都新建连接的 Playwright"必然超时。
3. **最多三次**，第三次仍失败 → 只回「连不上第三方 DMS 系统，请联系管理员」，
   不再给按钮（见 `line/cards.py` 的 `retry_message` 与 `intake/flow.py` 的 `_dms_failed`）。

只对**连不上/超时**这一类重试：对方明确拒绝（账号错、字段非法）再点几次还是同样的拒绝，
给它重试按钮只会让人白点。
"""

from __future__ import annotations

MAX_ATTEMPTS = 3

# 第 1 / 2 / 3 次尝试，各自"打开登录页"的上限（毫秒）。
ATTEMPT_TIMEOUT_MS = (30_000, 60_000, 90_000)

# 可以重试的错误码：**连不上对方系统**这一类。
#   · `ERR_DMS_UNREACHABLE` —— 新加的：登录页打不开/超时（原来会被兜成 ERR_UNEXPECTED）
#   · `ERR_DMS_TECHNICAL`   —— 网络/Playwright 类失败（`DmsTechnicalError`），
#                             它自己的文案就是"DMS 连接超时——请重试"
# 故意**不含** `ERR_UNEXPECTED`：那是"我们这边的意外"，重试解决不了，
# 照旧直说并让人联系管理员才是对的。
RETRYABLE = frozenset({"ERR_DMS_UNREACHABLE", "ERR_DMS_TECHNICAL"})


def clamp(attempt: int) -> int:
    """尝试次数落在 1..MAX_ATTEMPTS。"""
    try:
        value = int(attempt)
    except (TypeError, ValueError):
        value = 1
    return max(1, min(MAX_ATTEMPTS, value))


def timeout_ms(attempt: int) -> int:
    """这一次尝试给多长的"打开登录页"预算。"""
    return ATTEMPT_TIMEOUT_MS[clamp(attempt) - 1]


def is_retryable(code: str) -> bool:
    return str(code or "") in RETRYABLE
