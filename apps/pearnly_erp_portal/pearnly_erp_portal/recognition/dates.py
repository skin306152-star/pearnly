"""票面日期由代码换算（照旧线上 `services/erp/business_dates.py:11-26`：模型照抄票面原样的日期，
年份 ≥ 2400 视为佛历减 543），不让模型自己换算佛历 —— 换错一位就差十年。

旧线上只认四位年份；两位年份（「19/05/69」）这里多认一种：公历 20yy 与佛历 25yy 各算一个，
只有一个落在「5 年前 ~ 明天」内才用它，两个都说得通或都说不通就不改（交给模型给的日期）。
"""
import re
from datetime import date, timedelta

_NUMERIC = re.compile(r"^\s*(\d{1,4})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{1,4})\s*$")


def _plausible(value, today):
    return today - timedelta(days=365 * 5) <= value <= today + timedelta(days=1)


def printed(raw, today=None):
    """票面原样日期 → ISO；认不出返回 None。支持 D/M/YYYY、YYYY-M-D 与 D/M/YY。"""
    match = _NUMERIC.match(str(raw or ""))
    if not match:
        return None
    first, second, third = match.groups()
    anchor = today or date.today()
    if len(first) == 4:
        year, month, day = int(first), int(second), int(third)
        candidates = [year - 543 if year >= 2400 else year]
    else:
        day, month = int(first), int(second)
        if len(third) == 4:
            year = int(third)
            candidates = [year - 543 if year >= 2400 else year]
        elif len(third) == 2:
            short = int(third)
            candidates = [2000 + short, 2500 + short - 543]
        else:
            return None
    found = []
    for year in candidates:
        try:
            value = date(year, month, day)
        except ValueError:
            continue
        found.append(value)
    if len(candidates) > 1:
        found = [value for value in found if _plausible(value, anchor)]
        if len(found) != 1:
            return None
    return found[0].isoformat() if found else None
