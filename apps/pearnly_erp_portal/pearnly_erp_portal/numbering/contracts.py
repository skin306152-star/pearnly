"""单据编号的纯规则：前缀、形状与取号口径，不碰框架与数据库。

两处来源，各管一段：

· **前缀**照旧线上生产版本（`services/erp/document_numbers.py`，固定源
  `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`）：采购 `PE`、销售 `SI`、入库 `IR`、
  出库 `IS`，四个字母固定，不是随手起的。
· **生成逻辑**照用户给的 MR.ERP 源码（`mrerp-source`）：每种单据一个计数器
  （`tbl_autono` 的 `runautono`，按 `comidyear` + `idmenu` 一行），号码 =
  `userdefinedno` + `-` + `str_pad(runautono, 6, "0", STR_PAD_LEFT)`（六位流水），
  表单把这个号填进**只读**输入框（`allform.php`），提交时计数器推到"已用号 + 1"
  （`allsave.php`；提交的号比计数器小就直接判错）。MR.ERP 的年份放在
  `userdefinedno` 里（真实数据是"6401"这种年+月），我们直接把**佛历年份**写进号码，
  唯一性不依赖人工填的前缀。

所以号码形如 `IR2569-000001`：类型码 + 佛历年份 + 六位流水，按"类型 + 年"从 000001 起。
号码由系统发、不接受手输；收发存报表"关联单据"那一列显示的就是它。
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
SERIAL_WIDTH = 6
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


def buddhist_year_text(on) -> str:
    """号码里的年份：`2569`（佛历，四位）。"""
    return f"{buddhist_year(_day(on).year):04d}"


def stem(prefix: str, on) -> str:
    """号码的固定开头：`IR2569-`。"""
    return f"{prefix}{buddhist_year_text(on)}-"


def year_bucket(on) -> str:
    """序号表里的桶：佛历年份（例 `2569`）。

    "同类型同一年共用一个计数器"由序号表的名字保证 —— 名字是
    `{方向}-{前缀}-{桶}`（原生 `ERP Doc Sequence` 的 autoname），前缀本来就在名字里，
    所以桶只放年份，不重复放前缀。
    """
    return buddhist_year_text(on)


def format_number(prefix: str, on, serial) -> str:
    return f"{stem(prefix, on)}{int(serial):0{SERIAL_WIDTH}d}"


def split(value):
    """把号码拆回 `(前缀, 佛历年, 流水)`；形状不对回 None（不猜、不修）。"""
    parts = str(value or "").strip().split("-")
    # `IR2569-000001` 就是两段：前缀+年 连在一起，中间没有分隔符（照 MR.ERP 的
    # `userdefinedno . "-" . 流水`）。三段的是旧四位日流水写法，不算本系统的号。
    if len(parts) != 2:
        return None
    head, serial = parts
    if len(head) != len("IR2569") or head[:2] not in PREFIXES:
        return None
    if not head[2:].isdigit() or int(head[2:]) < BUDDHIST_MIN_YEAR:
        return None
    if len(serial) != SERIAL_WIDTH or not serial.isdigit():
        return None
    return head[:2], head[2:], int(serial)


def matches(value, prefix: str = None) -> bool:
    """号码是不是本系统的形状（可选：是不是某个前缀）。"""
    parsed = split(value)
    return bool(parsed) and (prefix is None or parsed[0] == prefix)
