# -*- coding: utf-8 -*-
"""票面日期的合理性判定(两条识别链共用)。

从 validators.py 抽出:那里管的是"字段取自哪一列"(source_refs 溯源),日期合理性
是另一个维度的判据,且直读链不走 validate_invoice —— 混在一起会逼两条链各抄一份。
判据单一实现,Vision 路经 validate_invoice、直读经 read_page 各自引用。

纪年换算口径见 core/thai_date;本模块只管"这个公历日期合不合理"。
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import List, Optional

# 泰国税法凭证保存期 5 年 —— 比这更旧的票不该还在走识别入库,多半是年份读错一位
# (2026-07-20:佛历 2569 被读成 2559,归一出 2016,全链零告警推进 Express,
# 连税期都落到 2559-05)。代账补录旧账是常态,窗口再短会误杀。
MAX_BACKDATE_YEARS = 5
MAX_FUTURE_DAYS = 1  # 容时区偏差,不容"明年的票"

_ISO_DATE_RE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$")


def validate_invoice_date(inv, *, today: date) -> List[str]:
    """票面日期离今天太远 → 标注复核。

    软判据不阻断识别:回落 Vision 路救不了日期(那条路读日期更差,回落是净亏),
    该由人判是补录旧账还是读错了年份。调用方据此置 force_review。
    """
    if type(today) is not date:
        raise ValueError("today must be an explicit calendar date")
    m = _ISO_DATE_RE.match(str(getattr(inv, "date", "") or ""))
    if not m:
        return []  # 缺日期/非 ISO 由别的闸管,这里不重复报
    try:
        parsed = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return []
    anchor = today
    if parsed > anchor + timedelta(days=MAX_FUTURE_DAYS):
        return [f"invoice date {parsed.isoformat()} is in the future — check the year"]
    if parsed < anchor - timedelta(days=365 * MAX_BACKDATE_YEARS):
        return [
            f"invoice date {parsed.isoformat()} is over {MAX_BACKDATE_YEARS} years old "
            "— check the year (Buddhist-era digit misread reads 10 years early)"
        ]
    return []


# 佛历年份读错一位（2569 读成 2559）换成公历正好差 10 年；跨年前后的票差 9 或 11 年。
YEAR_SHIFT = range(9, 12)
YEAR_SHIFT_PREFIX = "date_year_shift:"


def year_shift(inv, *, anchor_year: int) -> Optional[str]:
    """票面年份与识别当年差约 10 年 → 疑佛历年份读错一位，返回给复核用的原因；否则 None。

    只是内部信号（2026-10-02 测试机 27 页扫描销项第 3 页：3/6/2569 读成 2559）：让同一模型带着原因看原图再读一次，
    取年份对得上的读数；不标红、不给用户加提示（用户 2026-10-02）。真是十年前的票极少，多一次复核的代价也小。
    """
    if type(anchor_year) is not int or getattr(inv, "is_not_invoice", False):
        return None
    m = _ISO_DATE_RE.match(str(getattr(inv, "date", "") or ""))
    if not m or abs(int(m.group(1)) - anchor_year) not in YEAR_SHIFT:
        return None
    return (f"{YEAR_SHIFT_PREFIX} date {inv.date} is about 10 years from {anchor_year} — the Buddhist-era year "
            "is probably misread by one digit; read the year printed on the document again")
