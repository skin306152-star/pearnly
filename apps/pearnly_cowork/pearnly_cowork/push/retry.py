"""Pure legacy result classification; no scheduling, DB or transport.

Source ec2e5c957e6b services/erp/push_retry.py. Endpoint counters classify
pending as non-failure; that compatibility predicate is NOT proof of success.
"""

from typing import Optional

# 「单号已存在」的判定在共用层（MR.ERP 规则，2026-11-02 搬去）；本模块对外名照旧。
from pearnly_core.mrerp.push.duplicates import is_already_pushed_error  # noqa: F401
# 「用户数据错」的判定与两张表也在共用层（2026-11-03 搬去，ERP 接 MR.ERP 要用）；本模块对外名照旧。
from pearnly_core.mrerp.push.user_data import (  # noqa: F401
    USER_DATA_ERROR_CODES, USER_DATA_ERROR_THAI_PATTERNS, is_user_data_error)

_ERP_RETRY_DELAYS_SEC = [60, 300, 1800]

ERP_MAX_RETRIES = 3

def classify_push_status(success: bool, error_msg: Optional[str]) -> str:
    """推送结果 → 单一权威 status(铁律 #12)。
    success → 'success';「发票号已存在」→ 'skipped_dup'(中性·已推送过);其余 → 'failed'。

    Express 出站拉取:它的"推送"= 写待领取队列,不是即时成败。enqueue_express 在
    error_msg 里带哨兵让这一行落成队列态(单一状态源 · 不另立队列表):
      EXPRESS_QUEUED   → 'pending'(进队列等本地 Agent lease)
      EXPRESS_MANUAL:* → 'manual' (低置信/判脏/账套拒 · 留人工)
    其它 adapter 永不产生这两个哨兵,行为零变化。
    """
    if not success and error_msg:
        if "EXPRESS_QUEUED" in error_msg:
            return "pending"
        if "EXPRESS_MANUAL" in error_msg:
            return "manual"
    if success:
        return "success"
    if is_already_pushed_error(error_msg):
        return "skipped_dup"
    return "failed"

def counts_as_endpoint_success(final_status: Optional[str]) -> bool:
    """端点 success_count/failure_count 口径(单一源 · 铁律 #3/#12)。

    success / skipped_dup(已推送过)记成功;failed / manual 记失败 —— manual = Express
    缺科目/低置信/账套拒,留人工,绝不冒充成功。pending(排队中)按非失败处理,ack 落定时再计。
    """
    return (final_status or "") not in ("failed", "manual")

def get_erp_retry_delay_sec(retry_count: int) -> Optional[int]:
    """根据已重试次数返回下次重试延迟(秒)· 超过最大次数返回 None"""
    if retry_count < 0 or retry_count >= ERP_MAX_RETRIES:
        return None
    if retry_count >= len(_ERP_RETRY_DELAYS_SEC):
        return _ERP_RETRY_DELAYS_SEC[-1]
    return _ERP_RETRY_DELAYS_SEC[retry_count]
