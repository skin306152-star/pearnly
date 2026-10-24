"""ERP 任务管理看板的纯规则：看板权限、顺序、字段清洗、提醒 / 重复、清单、自定义字段、链接（不依赖框架与 IO）。

照 Cowork 任务管理（`pearnly_cowork.work.contracts`）复制到 ERP 自有 App（用户 2026-10-23，ERP 站点不装 Cowork、
ERP 不导入 Cowork），规则与口径相同；两份权限矩阵由 `tests/test_erp_work.py` 逐个组合比对，改一边要同改另一边。
多块看板（用户 2026-09-29，照 Trello）：每块看板有自己的成员与可见范围，权限矩阵见 `board_rights`。
列表由用户自己增删、改名、排序、复制（2026-09-27 第四批）。
新看板默认三列：待办 / 进行中 / 已完成，「已完成」带"完成列表"标记——拖进去就算完成，没有审核。
"""
import calendar
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

DEFAULT_LISTS = (("todo", False), ("doing", False), ("done", True))
# 标签 / 列表 / 封面的颜色用 Desk 原生色（指示色与色阶），不另造色板。
LABEL_COLORS = ("green", "yellow", "orange", "red", "purple", "blue", "cyan", "pink", "gray")
# 新看板自带的六个无名颜色标签（照 Trello；2026-09-27 实测：没有标签时「修改标签」是个空弹窗）。
DEFAULT_LABEL_COLORS = ("green", "yellow", "orange", "red", "purple", "blue")
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


# ── 看板权限（用户 2026-09-29） ─────────────────────────────────────────────
# 可见性（用户 2026-09-29 定 B）：团队可见 = 有「任务管理」权限的人都能看，但只有看板成员能改（没加进来的人按观察者算）；
# 默认看板（原来那一块）例外，有任务管理权限的人照常都能改。私有 = 只有看板成员能看。
VISIBILITIES = ("team", "private")
# 看板角色：管理员、成员（增改卡片 / 列表）、观察者（只读，评论看看板设置）。
ROLES = ("admin", "member", "observer")
# 谁能评论：成员（含管理员；默认看板上所有人）/ 成员和观察者（团队可见的板上没加进来的人按观察者算）/ 关闭。
COMMENTING = ("members", "observers", "disabled")
# 谁能加减成员：仅管理员 / 所有成员。
MEMBER_MANAGEMENT = ("admins", "members")
# 权利：看、增改卡片与列表、评论、加减成员、管理（设置 / 标签 / 字段 / 自动化 / 归档 / 彻底删除）。
RIGHTS = ("view", "edit", "comment", "members", "manage")
BOARD_TITLE_MAX = 100
BOARDS_MAX = 200
BOARD_MEMBERS_MAX = 500


def board_rights(*, role=None, visibility="private", team_user=False, product_admin=False,
                 commenting="members", member_management="admins", is_default=False) -> frozenset:
    """一个人在一块看板上的权利。`role` 是他在这块板上的角色（不是成员为 None），
    `team_user` = 有「任务管理」权限（进得了页面），`product_admin` = ERP 管理员（看得到、管得了所有看板）。"""
    if product_admin or role == "admin":
        return frozenset(RIGHTS)
    outsider = role is None and team_user and visibility == "team"
    if role == "member" or (outsider and is_default):
        rights = {"view", "edit"}
        if commenting in ("members", "observers"):
            rights.add("comment")
        if role == "member" and member_management == "members":
            rights.add("members")
        return frozenset(rights)
    if role == "observer" or outsider:
        return frozenset({"view", "comment"} if commenting == "observers" else {"view"})
    return frozenset()


def member_changes(current: dict, wanted: dict, *, actor: str, manage: bool, can_members: bool) -> dict:
    """成员名单一次保存：`current` / `wanted` 是 {用户: 角色}。返回 {"add", "remove", "change"}。

    - 看板管理员（`manage`）什么都能改；
    - 有「加减成员」权利的普通成员只能加 / 减成员和观察者，不能改角色、不能动管理员；
    - 任何人都可以把自己移出（离开看板）；
    - 原来有管理员的看板不能被改成一个管理员都没有。
    """
    if not isinstance(wanted, dict) or len(wanted) > BOARD_MEMBERS_MAX:
        raise Invalid("erp.work.error.request")
    for user, role in wanted.items():
        if not isinstance(user, str) or not user or role not in ROLES:
            raise Invalid("erp.work.error.request")
    add = {user: role for user, role in wanted.items() if user not in current}
    remove = [user for user in current if user not in wanted]
    change = {user: role for user, role in wanted.items() if user in current and current[user] != role}
    if not manage:
        leaving_only = remove == [actor] and not add and not change
        if not leaving_only:
            if not can_members or change:
                raise Invalid("erp.work.error.board_rights")
            if any(role == "admin" for role in add.values()):
                raise Invalid("erp.work.error.board_rights")
            if any(current[user] == "admin" for user in remove if user != actor):
                raise Invalid("erp.work.error.board_rights")
    had_admin = any(role == "admin" for role in current.values())
    if had_admin and not any(role == "admin" for role in wanted.values()):
        raise Invalid("erp.work.error.last_admin")
    return {"add": add, "remove": remove, "change": change}


def text(value, limit: int, *, required: bool = False, key: str = "erp.work.error.title") -> str:
    cleaned = " ".join(str(value or "").split())
    if required and not cleaned:
        raise Invalid(key)
    if len(cleaned) > limit:
        raise Invalid("erp.work.error.too_long")
    return cleaned


def comment(value) -> str:
    # 评论保留换行，只去掉首尾空白。
    cleaned = str(value or "").replace("\r\n", "\n").strip()
    if not cleaned:
        raise Invalid("erp.work.error.comment")
    if len(cleaned) > COMMENT_MAX:
        raise Invalid("erp.work.error.too_long")
    return cleaned


def color(value) -> str:
    if value not in LABEL_COLORS:
        raise Invalid("erp.work.error.color")
    return value


def choice(value, allowed, key="erp.work.error.request"):
    if value not in allowed:
        raise Invalid(key)
    return value


def checklists(groups) -> list:
    """多个清单：[{title, items: [{title, done}]}] → 清洗后的同形列表；空标题的项丢掉，没名字的清单叫空串。"""
    if groups in (None, ""):
        return []
    if not isinstance(groups, list) or len(groups) > CHECKLISTS_MAX:
        raise Invalid("erp.work.error.checklist")
    result, total = [], 0
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("items") or [], list):
            raise Invalid("erp.work.error.checklist")
        items = []
        for item in group.get("items") or []:
            if not isinstance(item, dict):
                raise Invalid("erp.work.error.checklist")
            title = text(item.get("title"), CHECKLIST_ITEM_MAX)
            if title:
                items.append({"title": title, "done": 1 if item.get("done") in (1, True, "1") else 0})
        total += len(items)
        result.append({"title": text(group.get("title"), LIST_TITLE_MAX), "items": items})
    if total > CHECKLIST_MAX:
        raise Invalid("erp.work.error.too_many")
    return result


def distinct(values, limit: int) -> list:
    """去重、保序；给负责人 / 标签这类多选用。"""
    if values in (None, ""):
        return []
    if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
        raise Invalid("erp.work.error.request")
    result = list(dict.fromkeys(values))
    if len(result) > limit:
        raise Invalid("erp.work.error.too_many")
    return result


def link(url) -> str:
    """附件里的链接：只收 http / https。"""
    value = str(url or "").strip()
    parsed = urlparse(value)
    if len(value) > URL_MAX or parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise Invalid("erp.work.error.link")
    return value


def field_options(fieldtype, options) -> list:
    if fieldtype != "select":
        return []
    values = [text(option, FIELD_VALUE_MAX) for option in (options or [])]
    values = list(dict.fromkeys(value for value in values if value))
    if not values or len(values) > FIELD_OPTIONS_MAX:
        raise Invalid("erp.work.error.field_options")
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
            raise Invalid("erp.work.error.field_value")
        if not number.is_finite():
            raise Invalid("erp.work.error.field_value")
        return format(number.normalize(), "f")
    if fieldtype == "date":
        try:
            return date.fromisoformat(str(value)[:10]).isoformat()
        except ValueError:
            raise Invalid("erp.work.error.field_value")
    if fieldtype == "checkbox":
        return "1" if value in (1, True, "1", "true") else ""
    if fieldtype == "select":
        if value not in options:
            raise Invalid("erp.work.error.field_value")
        return value
    raise Invalid("erp.work.error.field_value")


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
    raise Invalid("erp.work.error.request")


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
