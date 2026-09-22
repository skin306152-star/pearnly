"""单据编号的纯规则：前缀与形状，不碰框架与数据库。

口径来自旧线上生产版本（`services/erp/document_numbers.py`，固定源
`ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`）：

- 四种业务单据的前缀**固定**：采购 `PE`、销售 `SI`、入库 `IR`、出库 `IS`；
- 号码形如 `IR-25690922-0001` —— 前缀 + **佛历**日期（公历 + 543）+ 当日流水；
- 流水按"前缀 + 当天"从 0001 重新数（同一前缀同一天共用一个计数器）。

号码由系统发、不接受手输：收发存报表"关联单据"那一列显示的就是它，形状一旦不对，
报表跟着错，所以这几个前缀写在契约里、由测试钉住。
"""

from datetime import date

PURCHASE = "PE"
SALES = "SI"
STOCK_IN = "IR"
STOCK_OUT = "IS"

# 业务方向 → 前缀。方向名与旧线上 `direction` 一致（purchase / sales / in / out）。
BY_DIRECTION = {"purchase": PURCHASE, "sales": SALES, "in": STOCK_IN, "out": STOCK_OUT}
PREFIXES = (PURCHASE, SALES, STOCK_IN, STOCK_OUT)

BUDDHIST_OFFSET = 543
SERIAL_WIDTH = 4
# 佛历年份的下限：旧线上判"这串日期是佛历还是公历"用的就是 2400（`business_dates.standard`
# 里 year >= 2400 就减 543）。号码里的年份必须过这一关，`IR-20260922-0001` 那种公历写法
# 因此不算本系统发的号。
BUDDHIST_MIN_YEAR = 2400


def prefix_for(direction: str) -> str:
    """业务方向 → 固定前缀。认不出的方向直接拒绝，不猜一个默认值。"""
    try:
        return BY_DIRECTION[str(direction)]
    except KeyError:
        raise ValueError(f"unknown document direction: {direction!r}") from None


def buddhist_year(year: int) -> int:
    return int(year) + BUDDHIST_OFFSET


def _day(on) -> date:
    return on if isinstance(on, date) else date.fromisoformat(str(on)[:10])


def buddhist_day(on) -> str:
    """`25690922`：佛历年 + 月 + 日，定宽，号码里直接拼。"""
    value = _day(on)
    return f"{buddhist_year(value.year):04d}{value.month:02d}{value.day:02d}"


def stem(prefix: str, on) -> str:
    return f"{prefix}-{buddhist_day(on)}-"


def day_bucket(on) -> str:
    """序号表里的桶：佛历日（例 `25690922`）。

    "同前缀同一天共用一个计数器"由序号表的名字保证 —— 名字是
    `{方向}-{前缀}-{桶}`（原生 `ERP Doc Sequence` 的 autoname），前缀本来就在名字里，
    所以桶只放日期，不重复放前缀。
    """
    return buddhist_day(on)


def format_number(prefix: str, on, serial) -> str:
    return f"{stem(prefix, on)}{int(serial):0{SERIAL_WIDTH}d}"


def split(value):
    """把号码拆回 `(前缀, 佛历日, 流水)`；形状不对回 None（不猜、不修）。"""
    parts = str(value or "").strip().split("-")
    if len(parts) != 3:
        return None
    prefix, day, serial = parts
    if prefix not in PREFIXES:
        return None
    if len(day) != 8 or not day.isdigit():
        return None
    if int(day[:4]) < BUDDHIST_MIN_YEAR:
        return None
    if len(serial) != SERIAL_WIDTH or not serial.isdigit():
        return None
    return prefix, day, int(serial)


def matches(value, prefix: str = None) -> bool:
    """号码是不是本系统的形状（可选：是不是某个前缀）。"""
    parsed = split(value)
    return bool(parsed) and (prefix is None or parsed[0] == prefix)
