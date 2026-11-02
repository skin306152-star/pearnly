"""MR.ERP 报「单号已存在」的判定（纯函数）：导入报告里的这类失败其实是之前已推过。

由 Cowork `push.retry` 原样搬来（2026-11-02，那里改为从这里导入，推送状态分类照旧）。
"""
from typing import Optional

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
