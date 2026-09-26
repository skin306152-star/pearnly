"""工作协调看板的纯规则：顺序、字段清洗、提醒 / 重复、清单、自定义字段、链接（不依赖框架与 IO）。

一个团队一块板（用户 2026-09-27）；列表由用户自己增删、改名、排序、复制（2026-09-27 第四批）。
新站点默认三列：待办 / 进行中 / 已完成，「已完成」带"完成列表"标记——拖进去就算完成，没有审核。
"""
import calendar
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

DEFAULT_LISTS = (("todo", False), ("doing", False), ("done", True))
# 标签 / 列表 / 封面的颜色用 Desk 原生色（指示色与色阶），不另造色板。
LABEL_COLORS = ("green", "yellow", "orange", "red", "purple", "blue", "cyan", "pink", "gray")
# 到期前多久提醒（分钟）；none = 不提醒。照 Trello 的选项。
REMINDERS = {"none": None, "at_due": 0, "5m": 5, "10m": 10, "15m": 15, "1h": 60, "2h": 120,
             "1d": 1440, "2d": 2880}
DEFAULT_REMINDER = "1d"
REPEATS = ("never", "daily", "weekly", "monthly", "yearly")
FIELD_TYPES = ("text", "number", "date", "checkbox", "select")
# 自动化（照 Trello 的"当卡片被添加到列表中时"）：分派给成员、加标签、到期日设为 N 天后、标记完成、加关注。
RULE_ACTIONS = ("assign", "label", "due_days", "complete", "watch")
RULES_MAX = 50
SEARCH_MIN = 2
# 只填了到期日、没填时间：按当天最后一分钟算到期。
END_OF_DAY = time(23, 59)

TITLE_MAX = 200
LABEL_TITLE_MAX = 60
LIST_TITLE_MAX = 60
CHECKLISTS_MAX = 20
CHECKLIST_MAX = 100
CHECKLIST_ITEM_MAX = 200
COMMENT_MAX = 4000
DESCRIPTION_MAX = 20000
LABELS_MAX = 30
LISTS_MAX = 50
FIELDS_MAX = 30
FIELD_VALUE_MAX = 500
FIELD_OPTIONS_MAX = 30
URL_MAX = 2000
ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024

# 新卡落在列尾：尾卡位置 + STEP。两卡之间取中值；间距小于 MIN_GAP 时整列重排。列表的顺序同一套。
STEP = 1024.0
MIN_GAP = 1e-6


class Invalid(ValueError):
    """前端能改掉的输入错误；`args[0]` 是术语键。"""


def text(value, limit: int, *, required: bool = False, key: str = "cowork.work.error.title") -> str:
    cleaned = " ".join(str(value or "").split())
    if required and not cleaned:
        raise Invalid(key)
    if len(cleaned) > limit:
        raise Invalid("cowork.work.error.too_long")
    return cleaned


def comment(value) -> str:
    # 评论保留换行，只去掉首尾空白。
    cleaned = str(value or "").replace("\r\n", "\n").strip()
    if not cleaned:
        raise Invalid("cowork.work.error.comment")
    if len(cleaned) > COMMENT_MAX:
        raise Invalid("cowork.work.error.too_long")
    return cleaned


def color(value) -> str:
    if value not in LABEL_COLORS:
        raise Invalid("cowork.work.error.color")
    return value


def choice(value, allowed, key="cowork.work.error.request"):
    if value not in allowed:
        raise Invalid(key)
    return value


def checklists(groups) -> list:
    """多个清单：[{title, items: [{title, done}]}] → 清洗后的同形列表；空标题的项丢掉，没名字的清单叫空串。"""
    if groups in (None, ""):
        return []
    if not isinstance(groups, list) or len(groups) > CHECKLISTS_MAX:
        raise Invalid("cowork.work.error.checklist")
    result, total = [], 0
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("items") or [], list):
            raise Invalid("cowork.work.error.checklist")
        items = []
        for item in group.get("items") or []:
            if not isinstance(item, dict):
                raise Invalid("cowork.work.error.checklist")
            title = text(item.get("title"), CHECKLIST_ITEM_MAX)
            if title:
                items.append({"title": title, "done": 1 if item.get("done") in (1, True, "1") else 0})
        total += len(items)
        result.append({"title": text(group.get("title"), LIST_TITLE_MAX), "items": items})
    if total > CHECKLIST_MAX:
        raise Invalid("cowork.work.error.too_many")
    return result


def distinct(values, limit: int) -> list:
    """去重、保序；给负责人 / 标签这类多选用。"""
    if values in (None, ""):
        return []
    if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
        raise Invalid("cowork.work.error.request")
    result = list(dict.fromkeys(values))
    if len(result) > limit:
        raise Invalid("cowork.work.error.too_many")
    return result


def link(url) -> str:
    """附件里的链接：只收 http / https。"""
    value = str(url or "").strip()
    parsed = urlparse(value)
    if len(value) > URL_MAX or parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise Invalid("cowork.work.error.link")
    return value


def field_options(fieldtype, options) -> list:
    if fieldtype != "select":
        return []
    values = [text(option, FIELD_VALUE_MAX) for option in (options or [])]
    values = list(dict.fromkeys(value for value in values if value))
    if not values or len(values) > FIELD_OPTIONS_MAX:
        raise Invalid("cowork.work.error.field_options")
    return values


def field_value(fieldtype, options, value) -> str:
    """一格自定义字段的值 → 存库的字符串；空值返回空串（= 清掉）。"""
    if value in (None, ""):
        return ""
    if fieldtype == "text":
        return text(value, FIELD_VALUE_MAX)
    if fieldtype == "number":
        try:
            number = Decimal(str(value).strip())
        except InvalidOperation:
            raise Invalid("cowork.work.error.field_value")
        if not number.is_finite():
            raise Invalid("cowork.work.error.field_value")
        return format(number.normalize(), "f")
    if fieldtype == "date":
        try:
            return date.fromisoformat(str(value)[:10]).isoformat()
        except ValueError:
            raise Invalid("cowork.work.error.field_value")
    if fieldtype == "checkbox":
        return "1" if value in (1, True, "1", "true") else ""
    if fieldtype == "select":
        if value not in options:
            raise Invalid("cowork.work.error.field_value")
        return value
    raise Invalid("cowork.work.error.field_value")


def due_at(due_date, due_time=None) -> datetime:
    """到期时刻（站点时区的裸时间）。"""
    day = due_date if isinstance(due_date, date) else date.fromisoformat(str(due_date)[:10])
    if due_time in (None, ""):
        moment = END_OF_DAY
    elif isinstance(due_time, time):
        moment = due_time
    elif isinstance(due_time, timedelta):  # 数据库 Time 列取出来是 timedelta
        seconds = int(due_time.total_seconds()) % 86400
        moment = time(seconds // 3600, seconds % 3600 // 60)
    else:
        parts = str(due_time).split(":")
        moment = time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    return datetime.combine(day, moment)


def remind_at(due_date, due_time, reminder):
    minutes = REMINDERS.get(reminder)
    if minutes is None or not due_date:
        return None
    return due_at(due_date, due_time) - timedelta(minutes=minutes)


def next_due(due_date, repeat) -> date:
    """重复到期：下一次的到期日（按月 / 按年遇到没有那天的月份取月底）。"""
    day = due_date if isinstance(due_date, date) else date.fromisoformat(str(due_date)[:10])
    if repeat == "daily":
        return day + timedelta(days=1)
    if repeat == "weekly":
        return day + timedelta(days=7)
    if repeat in ("monthly", "yearly"):
        months = 1 if repeat == "monthly" else 12
        year, month = divmod(day.month - 1 + months, 12)
        year, month = day.year + year, month + 1
        return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))
    raise Invalid("cowork.work.error.request")


def position_between(before, after):
    """`before` / `after` 是落点上下两张卡（或两个列表）的位置（没有就是 None）。

    返回新位置；两者之间已经挤不下时返回 None，由调用方整列重排后再算。
    """
    if before is None and after is None:
        return STEP
    if before is None:
        return after - STEP
    if after is None:
        return before + STEP
    if after - before <= MIN_GAP:
        return None
    return (before + after) / 2


def renumber(count: int) -> list:
    """整列重排后的位置：STEP, 2·STEP, …"""
    return [STEP * (index + 1) for index in range(count)]
