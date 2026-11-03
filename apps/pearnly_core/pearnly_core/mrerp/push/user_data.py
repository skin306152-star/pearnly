"""「用户数据错」的判定（重推还是同样的错、不该自动重试）：MR.ERP 推送执行与 Cowork 推送分类共用（多产品共用）。

函数体与两张表原样来自 Cowork `push/retry.py`（2026-11-03 下沉，ERP 接 MR.ERP 导入直写要用，评估 2026-11-02 §14.6）；
Cowork `push.retry` 从这里导入后照旧是它的对外名。纯函数，只用标准库。
"""
from typing import Optional

USER_DATA_ERROR_CODES = {
    "ERR_NO_CLIENT",
    "ERR_NO_CUSTOMER_MAPPING",
    "ERR_NO_INVOICE_NO",
    "ERR_NO_INVOICE_DATE",
    "ERR_NO_TOTAL_AMOUNT",
    "ERR_AMOUNTS_NOT_CLOSED",  # 写账前通用护栏（`tax_documents.ledger_guard`）：票面金额对不上
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
    # MR.ERP 主档默认值（2026-10-08）：要人选 / 选过的值没了 / 读不到主档，重推一样，要人处理。
    "ERR_MRERP_DEFAULT_CHOICE_REQUIRED",
    "ERR_MRERP_DEFAULT_CHANGED",
    "ERR_MRERP_MASTER_UNREADABLE",
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
