# -*- coding: utf-8 -*-
r"""账套会计期间(ISPRD)判定:余额表月桶的列号是**财年期号**,不是日历月。

ARBAL/APBAL 每组桶恒 24 列(`{P}1..12` = CY 槽,`{P}1NY..12NY` = NY 槽),列里的数字是
ISPRD 的期号。真账逐格对账(镜像账套 · 2026-07-27):69ASIAR 的 APBAL 里 206 个非零
HP/RR 格,按日历月只能解释 17 个、按 ISPRD 期号 206 个全中。

**CY / NY 不是"今年/明年"而是两段财年区间**:CY = `BEG1..END12` 覆盖的财年,NY =
`BEG1NY..END12NY` 覆盖的下一财年。所以两件事同时错过:
- 11 月起账的账套(69ASIAMI / 69ASIAR / 68ASIAMI / 68ASIAR)期号整体偏两个月;
- 1 月起账的账套里 CY 也常常不是当前公历年(69SINCER/6902ASC 的 CY 是 2025、NY 是
  2026),按日历月取桶就把 2026 年的单全塞进 2025 年那组列。

判不出(ISPRD 缺表 / 空表 / 起止日读不出 / 单据日期落在 24 期窗口外)一律不给列名,由调用方
不写桶并上报 —— 绝不拿 CY 当默认。写错桶不报错:试算表照样平,只有客户/供应商对账单错,
要等几个月后会计对账才发现。

⚠️ 别跟着改的两处:凭证号里的 YYMMDD 是**佛历年 + 日历月**(HP690315-001 ↔ DOCDAT
2026-03-15,而该账套是财年第 5 期),发号逻辑与财年无关;VATPRD/TAXPRD 恒为自然月首日
(泰国 VAT 按自然月申报),同样与财年无关。

本文件与 `bridge/writepath/fiscal_period.py` 是**同源复制而非共享**:桥与小助手是两条独立
发版线,互不 import(闸 `test_bridge_never_imports_companion` 锁着反向)。桥那份还带关账锁
闸(LOCK 列 → 拒单),小助手这条写路没有拒单转人工的出口,故只搬期号判定这一半。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Tuple

import dbf

from . import dbf_schema as S

TABLE = "ISPRD"
PERIODS_PER_SLOT = 12
SLOT_CY = ""  # 列名后缀:CY 槽无后缀
SLOT_NY = "NY"
_SLOTS = (SLOT_CY, SLOT_NY)


@dataclass(frozen=True)
class Period:
    """一个会计期:财年第 index 期(1..12)、落在 CY 还是 NY 槽、起止日(公历)。"""

    index: int
    slot: str
    beg: dt.date
    end: dt.date

    @property
    def suffix(self) -> str:
        """余额表列名后缀:CY 第 5 期 → '5',NY 第 5 期 → '5NY'。"""
        return f"{self.index}{self.slot}"


@dataclass(frozen=True)
class FiscalCalendar:
    """一本账套的 24 期日历(按时间升序)。"""

    periods: Tuple[Period, ...]

    @property
    def start_month(self) -> int:
        """财年起始月(1..12)。取自 BEG1.month,不需要任何反推。"""
        return self.periods[0].beg.month

    @property
    def window(self) -> Tuple[dt.date, dt.date]:
        return self.periods[0].beg, self.periods[-1].end

    def locate(self, day: dt.date) -> Optional[Period]:
        for p in self.periods:
            if p.beg <= day <= p.end:
                return p
        return None


def load(account_dir) -> Optional[FiscalCalendar]:
    """读 ISPRD → 日历。表不存在 / 空表 / 一期都读不出来 → None(调用方必须按判不出处理)。

    不做缓存:ISPRD 是 1 行 72 列的小表,读的代价远低于一次备份拷贝;而缓存过的期间表会在
    会计刚改完财年时继续按旧期号落列。
    """
    path = Path(account_dir) / f"{TABLE}.DBF"
    if not path.is_file():
        return None
    t = dbf.Table(str(path), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    t.open(dbf.READ_ONLY)
    try:
        row = next(iter(S.live_rows(t)), None)  # 真表恒 1 行
        periods = tuple(_read_periods(row)) if row is not None else ()
    finally:
        t.close()
    if not periods:
        return None
    return FiscalCalendar(tuple(sorted(periods, key=lambda p: p.beg)))


def _read_periods(row) -> Iterator[Period]:
    """逐字段容错读 BEG/END × 12 期 × 2 槽。起止日缺一即跳过该期。

    容错的理由与主档读取同款:生产账套里真有坏字节,整行读会把一张好单判失败。跳过的期不会
    被 locate() 命中,单据落在那里就是判不出 → 不写桶,不会静默落错桶。
    """
    for slot in _SLOTS:
        for m in range(1, PERIODS_PER_SLOT + 1):
            beg = _date(row, f"BEG{m}{slot}")
            end = _date(row, f"END{m}{slot}")
            if beg is None or end is None:
                continue
            yield Period(index=m, slot=slot, beg=beg, end=end)


def _date(row, field: str) -> Optional[dt.date]:
    """日期列容错读:列不存在 / 字节坏 / 空值 → None。"""
    try:
        v = row[field]
    except Exception:
        return None
    return v if isinstance(v, dt.date) else None


def month_field(prefix: str, period: Period) -> str:
    """(桶组前缀, 期) → 余额表列名。IV + CY 第 5 期 → 'IV5';HP + NY 第 5 期 → 'HP5NY'。"""
    return f"{prefix}{period.suffix}"


def resolve_month_field(account_dir, day: dt.date, prefix: str) -> Tuple[Optional[str], str]:
    """单据日期 → 余额表桶列名。返回 (列名, 判不出的原因)。

    判不出时列名为 None 且原因非空 —— 调用方必须**不写桶并把原因上报**,不许拿 CY 当默认,
    也不许静默跳过让会计以为写了。
    """
    cal = load(account_dir)
    name = Path(account_dir).name
    if cal is None:
        return None, (
            f"账套 {name} 的会计期间表 {TABLE} 读不到或没有期间行,财年判不出来,"
            f"{prefix} 月桶本单未写,请人工在 Express 里补记该笔余额"
        )
    period = cal.locate(day)
    if period is None:
        beg, end = cal.window
        return None, (
            f"单据日期 {day} 不在账套 {name} 的会计期间窗内({beg} ~ {end}),"
            f"{prefix} 月桶本单未写,请确认这张单是不是该进另一本账套"
        )
    return month_field(prefix, period), ""
