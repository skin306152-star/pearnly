"""Pure legacy result classification; no scheduling, DB or transport.

Source ec2e5c957e6b services/erp/push_retry.py. Endpoint counters classify
pending as non-failure; that compatibility predicate is NOT proof of success.
"""

from typing import Optional

_ERP_RETRY_DELAYS_SEC = [60, 300, 1800]

ERP_MAX_RETRIES = 3

USER_DATA_ERROR_CODES = {
    "ERR_NO_CLIENT",
    "ERR_NO_CUSTOMER_MAPPING",
    "ERR_NO_INVOICE_NO",
    "ERR_NO_INVOICE_DATE",
    "ERR_NO_TOTAL_AMOUNT",
    "ERR_NEGATIVE_AMOUNT",
    "ERR_TAX_ID_INVALID",
    "ERR_DATE_FUTURE",
    "ERR_INVOICE_NO_TOO_LONG",
    "ERR_BILL_NO_TOO_LONG",
    "ERR_CUSTOMER_CODE_TOO_LONG",
    "ERR_NO_SEED_CUSTOMER",
    "ERR_NO_SEED_PRODUCT",
    # MR.ERP duplicate · 重推还是同一个 bill_no · 没意义
    "ERR_DUPLICATE_INVOICE",
    # Fail-safe 名字复核不匹配(Zihao 2026-05-26 拍板 · P1)· 用户得先改映射 ·
    # 重推同样不匹配 · 不入 retry。注:ERR_*_VERIFY_UNAVAILABLE(网络/超时无法确认)
    # 故意不在此 set · 属技术错 · 走 retry 队列(但永远不显示 success)。
    "ERR_CUSTOMER_NAME_MISMATCH",
    "ERR_PRODUCT_NAME_MISMATCH",
}

USER_DATA_ERROR_THAI_PATTERNS = (
    "เลขที่ดังกล่าวมีอยู่ในระบบแล้ว",  # duplicate
    "เลขที่เอกสารซ้ำ",  # duplicate
    "ไม่พบข้อมูลรหัสลูกค้า",  # customer mapping missing
    "ลูกค้าไม่ระบุ",  # client missing
    "เลขที่ต้องไม่เกิน",  # length limit
    # MR.ERP 税务库查重「已存在于进项 / 销项税数据库」（`importpc.php:179`）：重推还是同一个号（2026-09-28）。
    "มีอยู่ในฐานข้อมูลภาษีซื้อแล้ว",
    "มีอยู่ในฐานข้อมูลภาษีขายแล้ว",
)

_ALREADY_PUSHED_THAI_PATTERNS = (
    "เลขที่ดังกล่าวมีอยู่ในระบบแล้ว",  # 该号已存在系统
    "เลขที่เอกสารซ้ำ",  # 单据号重复
)

def is_already_pushed_error(error_msg: Optional[str]) -> bool:
    """这条「失败」其实是「发票号已存在/之前已推过」吗?是 → 应记为 skipped_dup 中性态。"""
    if not error_msg:
        return False
    s = str(error_msg)
    if "ERR_DUPLICATE_INVOICE" in s:
        return True
    for pat in _ALREADY_PUSHED_THAI_PATTERNS:
        if pat in s:
            return True
    return False

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

def is_user_data_error(error_msg: Optional[str]) -> bool:
    """判断这条错误是不是"用户数据错"(不应该 retry).
    匹配 ERR_* code 前缀 OR 已知泰文 raw 模式.
    """
    if not error_msg:
        return False
    s = str(error_msg)
    # 任一 ERR_* code 命中
    for code in USER_DATA_ERROR_CODES:
        if code in s:
            return True
    # 任一已知泰文模式命中
    for pat in USER_DATA_ERROR_THAI_PATTERNS:
        if pat in s:
            return True
    return False

def get_erp_retry_delay_sec(retry_count: int) -> Optional[int]:
    """根据已重试次数返回下次重试延迟(秒)· 超过最大次数返回 None"""
    if retry_count < 0 or retry_count >= ERP_MAX_RETRIES:
        return None
    if retry_count >= len(_ERP_RETRY_DELAYS_SEC):
        return _ERP_RETRY_DELAYS_SEC[-1]
    return _ERP_RETRY_DELAYS_SEC[retry_count]
