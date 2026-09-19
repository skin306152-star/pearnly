# -*- coding: utf-8 -*-
"""泰国身份证 13 位 mod-11 校验（纯函数）。

为什么要有它：模型读出 13 位**不代表**这 13 位是合法的 —— 校验位对不上，多半是
某一位读错了（热敏纸、糊图最容易错一位）。所以建档前自检一次。

**只用来降信心（needs_review），不阻断**：真号也可能因为罕见的 OCR 误判而不通过，
最终由人复核定夺。

照搬老产品 `services/erp/dms_id_validate.py`。
"""

from __future__ import annotations

# 泰文数字 ๐-๙ → ASCII（卡面或 OCR 偶尔会带泰数字）
_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


def normalize_thai_id(s: str) -> str:
    """去噪归一：泰文数字→ASCII、去空格/连字符。存库与查重前都用它。"""
    return str(s or "").translate(_THAI_DIGITS).replace(" ", "").replace("-", "")


def is_valid_thai_id(s: str) -> bool:
    """13 位泰国公民号 mod-11 校验：前 12 位按权重 13..2 加权求和，
    check = (11 - (sum % 11)) % 10，须等于第 13 位。

    容忍空格/连字符/泰文数字；去噪后不是"恰好 13 位纯数字"一律 False。
    """
    digits = normalize_thai_id(s)
    # 必须 isascii() + isdigit() 一起用才锁死 0-9（单用 isdigit 会放行其它 Unicode 数字）
    if len(digits) != 13 or not (digits.isascii() and digits.isdigit()):
        return False
    total = sum(int(digits[i]) * (13 - i) for i in range(12))
    check = (11 - (total % 11)) % 10
    return check == int(digits[12])
