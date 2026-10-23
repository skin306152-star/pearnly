"""写账回执的状态口径（多产品共用，纯函数；ADR-025）：电脑回报一件写入之后，记录转成什么状态、算不算一次尝试。

由 `pearnly_cowork.push.lifecycle` 原样搬来（2026-10-23），Cowork 推送账本与共用机器通道同一套：成功、等账套（不计次数）、
要人处理（转人工）、普通失败重试到 :data:`MAX_ATTEMPTS` 次转人工。只接受旧线上的 legacy 分支。
"""
from dataclasses import dataclass
import re

from pearnly_core.express_rules.push import common

LEASE_SECONDS = 120
MAX_ATTEMPTS = 3
# 小助手回报的这些拒绝，重试多少次结论都一样（要人改票面、选对方档或核对账套）：直接转人工，不烧重试。
# 有意变化（旧线上按普通失败重试 3 次；2026-09-28 压测：同税号两个客户档的票在列表上一直是「录入中」）。
# 回报格式 `[错误码] 原始信息`（小助手 `queue_client._ack`）；老版本小助手没登记 outcome 的也按码认。
NEEDS_PERSON_CODES = frozenset({
    "CUSTOMER_BRANCH_AMBIGUOUS", "SUPPLIER_BRANCH_AMBIGUOUS", "CUSTOMER_DUP_SUSPECTED", "SUPPLIER_DUP_SUSPECTED",
    "FIELD_TOO_LONG", "PAYLOAD_INVALID", "ACCOUNT_DIR_NOT_ALLOWED", "ACCOUNT_COMPANY_MISMATCH",
    "DUPLICATE_CONTENT_DIFFERS", "PRIOR_DOC_STILL_IN_ERP",
    # 这台电脑的小助手里没有这份账套 / 文件夹在但不可写：重试结论一样。暂时连不上的，小助手 1.3.2 起回报「等账套」。
    "NO_ACCOUNT_SELECTION",
    # POS 日汇总票的付款方式转账凭证（载荷 3）：账套没有可用的普通日记账单据簿 / 选的收款科目在 Express 里不能记账 /
    # `pos` 块小助手不收。
    "JOURNAL_BOOK_NOT_FOUND", "ACCOUNT_NOT_POSTABLE", "POS_INVALID",
})
_ERROR_CODE = re.compile(r"^\[([A-Z0-9_]+)\]")


def error_code(error):
    """小助手回报原因里的错误码（`[CODE] …`）；没有就是空串。"""
    match = _ERROR_CODE.match(str(error or "").strip())
    return match.group(1) if match else ""


class LifecycleError(ValueError):
    pass


@dataclass(frozen=True)
class Transition:
    status: str
    attempt: int
    stage: str
    reason: str = ""
    retry: bool = False


def _branch(branch):
    if branch != "legacy":
        raise LifecycleError("push.branch_invalid")


def acknowledgement(*, branch, attempt, success, outcome=None, error=None):
    """State effects after a valid owned ACK. Transport/lease auth is separate.

    Legacy waiting_lock leaves the wire attempt unchanged; the same counter
    drives failure exhaustion.
    """
    _branch(branch)
    if type(attempt) is not int or attempt < 0:
        raise LifecycleError("push.attempt_invalid")
    stage = outcome if outcome in common.ACK_OUTCOMES else (
        common.STAGE_SUCCESS if success else common.STAGE_FAILED)
    if stage == common.STAGE_SUCCESS:
        return Transition("success", attempt, stage)
    if stage == common.STAGE_WAITING_LOCK:
        return Transition("pending", attempt, stage, retry=True)
    if stage in {common.STAGE_NEEDS_MAPPING, common.STAGE_NEEDS_REVIEW}:
        return Transition("manual", attempt, stage, str(error or "")[:500] or stage)
    if stage == common.STAGE_FAILED and error_code(error) in NEEDS_PERSON_CODES:
        return Transition("manual", attempt, common.STAGE_NEEDS_REVIEW, str(error)[:500])
    # Legacy applies `int(log.attempt or 1)` only on the failure branch;
    # success/waiting/manual leave the stored attempt unchanged, including zero.
    attempt = attempt or 1
    status = "manual" if attempt >= MAX_ATTEMPTS else "pending"
    reason = (str(error or "")[:500] or "agent_failed")
    return Transition(status, attempt + 1, stage, reason, status == "pending")
