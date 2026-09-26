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


def printed(raw, today=None, *, after=None):
    """票面原样日期 → ISO；认不出返回 None。支持 D/M/YYYY、YYYY-M-D 与 D/M/YY。

    `after`：到期日这类「单据日期当天及以后」的日期，两位年份取落在该日 ~ 两年后的那个
    （09-26 票面「ครบกำหนด 19/05/69」原样进了到期日，网页日期框报格式错误）。"""
    match = _NUMERIC.match(str(raw or ""))
    if not match:
        return None
    first, second, third = match.groups()
    anchor = today or date.today()
    plausible = (lambda value: after <= value <= after + timedelta(days=366 * 2)) if after else (
        lambda value: _plausible(value, anchor))
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
        found = [value for value in found if plausible(value)]
        if len(found) != 1:
            return None
    return found[0].isoformat() if found else None


def iso(value):
    """已经是有效的 YYYY-MM-DD 就原样返回，否则 None。"""
    try:
        return date.fromisoformat(str(value or "")).isoformat() if len(str(value or "")) == 10 else None
    except ValueError:
        return None


def due(raw, doc_date):
    """到期日：模型给的若不是 ISO（常见是照抄票面「19/05/69」），按票面日期规则换算；换不出就不填。"""
    start = iso(doc_date)
    return iso(raw) or printed(raw, after=date.fromisoformat(start) if start else None)
