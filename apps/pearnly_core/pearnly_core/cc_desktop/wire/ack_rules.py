"""写账回执的状态口径（多产品共用，纯函数；ADR-025）：电脑回报一件写入之后，记录转成什么状态、算不算一次尝试。

由 `pearnly_cowork.push.lifecycle` 原样搬来（2026-10-23），Cowork 推送账本与共用机器通道同一套：成功、等账套（不计次数）、
要人处理（转人工）、普通失败重试到 :data:`MAX_ATTEMPTS` 次转人工。只接受旧线上的 legacy 分支。
"""
from dataclasses import dataclass
import re

from pearnly_core.express_rules.push import common

LEASE_SECONDS = 120
MAX_ATTEMPTS = 3
# 等账套里「Express 开着这本账套」这一种（CC 桌面版回 `[ACCOUNT_BUSY_LOCKED] …`）：原因留在记录上（各产品据此显示现成那条
# 「Express 正在使用此账套，关闭后将自动推送」），这本账套歇 :data:`EXPRESS_OPEN_REST_SECONDS` 再派给电脑（期间别的账套照常领）。
# 以前原因清空、放回即可再领：电脑一直连着领同一张，别的账套的单领不到，界面上也看不出在等什么（11-12）。
EXPRESS_OPEN = "ACCOUNT_BUSY_LOCKED"
EXPRESS_OPEN_REST_SECONDS = 30
# 给人看的「在等 Express 关闭」算到上次回报之后多久：三个间隔。过了还没有新的回报（电脑关机、CC 掉线、Express 已关还没轮到）
# 就不再说在等 Express，回到普通的排队中——这个标志只在读的时候算，不另存，所以不会留成陈旧状态。
EXPRESS_OPEN_SHOWN_SECONDS = 90
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
    # 新建库存品挂的存货科目组不合格 / 没配（CC 桌面版 1.11.0 起单独回报）：要人选一次组
    "STOCK_ACC_GROUP_UNFIT",
    # 物料科目组的存货 / 购货科目不在科目表（原生 Express 1155 整张拒存，CC 桌面版 1.12.18 起照原生停单）：要人在 Express 改科目组
    "ITEM_GROUP_ACCOUNT_MISSING",
    # ERP 的单写进 Express 已关账的会计期（1.12.0，只有带 respect_period_lock 的载荷会回）
    "PERIOD_LOCKED",
    # 账套不允许负库存（ISINFO.NEGALLOW = N）、写销货时库存不够（出入库第一期，CC 桌面版 stock_guard）
    "STOCK_INSUFFICIENT",
    # 正式包里没有索引重建程序 / 跑不起来（1.12.3 起拒写，不再只改 DBF 让 Express 读到对不上的索引）：要人重装
    "REINDEX_UNAVAILABLE",
    # 库存单（载荷 6）认不出 Express 的库存品：要人改单或在 Express 建品
    "STOCK_ITEM_NOT_FOUND",
    # 作废 ERP 写进去的库存单（载荷 8）：原单在 Express 里被改过 / 找不到，要人核对
    "VOID_DOCUMENT_CHANGED", "VOID_DOCUMENT_NOT_FOUND",
    # 收银退货单 SR（载荷 10，CC 桌面版 dbf_refund）：原单找不到 / 金额被改过 / 原单没有这一行 / 退得比原单多，要人核对
    "REFUND_ORIGINAL_NOT_FOUND", "REFUND_ORIGINAL_CHANGED", "REFUND_LINE_NOT_FOUND", "REFUND_EXCEEDS_ORIGINAL",
    # 跨年退货单：新年度账套没有现金客户档、也没能从原年度账套复制过来（主会话 10-26），要人处理
    "REFUND_CUSTOMER_NOT_COPIED",
    # 赊购单服务品的进项税口径这本账套的历史定不了、载荷里也没有人选的（CC 桌面版写进货单，10-29）：要人选一次
    "SERVICE_VAT_BASIS_CHOICE",
    # 收款单 RE（载荷 20，CC 桌面版 dbf_receipt，码照契约小写）：发票余额不够 / 已被收清、发票找不到（不在 / 别的客户 / 作废）、
    # 收款方式没有可用科目、收了现金但账套没设现金科目，整单没写，要人处理
    "already_collected", "invoice_not_found", "receipt_type_invalid", "cash_account_missing",
})
# 码多为大写；收款单的四个码照契约小写（11-11），一并认
_ERROR_CODE = re.compile(r"^\[([A-Za-z0-9_]+)\]")
REASON_MAX = 500
# 代录回执（`[OPERATOR_<原因>] <说明>`，CC 桌面版 1.12.15 起说明以 AI 接手的结论原文开头、最多 2000 字）：原因整段留下，
# 站点卡片要显示完整结论（11-05 真机：截到 500 / 140 / 80 后只剩「无法识别的 Express 画面」）。其它回报仍截 500。
OPERATOR_REASON_MAX = 2100


def reason_text(error):
    """回报原因存多长：代录回执整段（:data:`OPERATOR_REASON_MAX`），其它截 :data:`REASON_MAX`。"""
    text = str(error or "")
    return text[:OPERATOR_REASON_MAX if error_code(text).startswith("OPERATOR_") else REASON_MAX]


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


def express_open(status, reason) -> bool:
    """这条排队中的记录是不是在等 Express 关闭（``status`` 用各自账本的叫法：Cowork ``pending``、共用通道 ``queued``）。"""
    return status in ("pending", "queued") and reason == EXPRESS_OPEN


def waiting_for_express(status, reason, since, moment) -> bool:
    """给人看：这条排队中的记录此刻是不是在等 Express 关闭（``since`` = 电脑上次回报「Express 开着」的时刻）。"""
    if not express_open(status, reason) or since is None or moment is None:
        return False
    return 0 <= (moment - since).total_seconds() < EXPRESS_OPEN_SHOWN_SECONDS


def resting(reason, since, moment) -> bool:
    """等 Express 关闭的记录这会儿该不该先不派：上次回报（``since``）到现在不满 :data:`EXPRESS_OPEN_REST_SECONDS`。"""
    if reason != EXPRESS_OPEN or since is None or moment is None:
        return False
    return 0 <= (moment - since).total_seconds() < EXPRESS_OPEN_REST_SECONDS


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
        return Transition("pending", attempt, stage, EXPRESS_OPEN if error_code(error) == EXPRESS_OPEN else "", retry=True)
    if stage in {common.STAGE_NEEDS_MAPPING, common.STAGE_NEEDS_REVIEW}:
        return Transition("manual", attempt, stage, reason_text(error) or stage)
    if stage == common.STAGE_FAILED and error_code(error) in NEEDS_PERSON_CODES:
        return Transition("manual", attempt, common.STAGE_NEEDS_REVIEW, reason_text(error))
    # Legacy applies `int(log.attempt or 1)` only on the failure branch;
    # success/waiting/manual leave the stored attempt unchanged, including zero.
    attempt = attempt or 1
    status = "manual" if attempt >= MAX_ATTEMPTS else "pending"
    reason = (reason_text(error) or "agent_failed")
    return Transition(status, attempt + 1, stage, reason, status == "pending")
