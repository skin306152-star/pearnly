# -*- coding: utf-8 -*-
"""从一条 LINE 文字里认出客户电话 · **零 IO 纯函数**。

收电话那一步原来按"含数字即视为号码"整条收下。真实销售会像聊天一样连发：
2026-10-05 ris 上，电话 `0611655634` 后紧跟一条「ที่อยู่ปัจจุบัน 142/5 ม.3 … 21160 (ห้อง142/50) คะ」，
第二条把电话整个覆盖成了地址文字。DMS 不校验电话栏（测试 DMS 实测整段文字原样存进去），
所以这里必须自己分清"这是电话"还是"带数字的别的话"。

规矩：

· 只取**一段**连续的数字（允许空格、连字符、点、括号分隔），9–12 位数字才算电话；
  带 `+` 的国际号码放宽到 15 位。门牌 `142/5`、邮编 `21160`、房号都到不了 9 位。
· 13 位身份证号不是电话。
· 一条消息里认出不止一个电话 → 不猜，交回调用方当普通文字处理。
· 返回的是那一段原文（去首尾空白），不改写格式：DMS 里电话怎么写由业务员决定。
"""

from __future__ import annotations

import re
from typing import Optional

# 以数字（或 +）开头、以数字结尾，中间只夹分隔符的一段。
_RUN = re.compile(r"\+?\d[\d \-.()]*\d")


def extract_phone(text: str) -> Optional[str]:
    found = []
    for match in _RUN.finditer(text or ""):
        run = match.group(0).strip()
        digits = sum(ch.isdigit() for ch in run)
        if 9 <= digits <= (15 if run.startswith("+") else 12):
            found.append(run)
    return found[0] if len(found) == 1 else None
