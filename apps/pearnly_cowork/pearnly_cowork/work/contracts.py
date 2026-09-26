"""工作协调看板的纯规则：状态、顺序、字段清洗（不依赖框架与 IO）。

用户 2026-09-27 定：一个团队一块板，固定三列（待办 / 进行中 / 已完成），没有审核——
拖到「已完成」就算完成。
"""

STATUSES = ("todo", "doing", "done")
DONE = "done"
# 标签颜色用 Desk 原生指示色（`indicator-pill <color>`），不另造色板。
LABEL_COLORS = ("green", "yellow", "orange", "red", "purple", "blue", "cyan", "pink", "gray")

TITLE_MAX = 200
LABEL_TITLE_MAX = 60
CHECKLIST_MAX = 100
CHECKLIST_ITEM_MAX = 200
COMMENT_MAX = 4000
DESCRIPTION_MAX = 20000
LABELS_MAX = 30
ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024

# 新卡落在列尾：尾卡位置 + STEP。两卡之间取中值；间距小于 MIN_GAP 时整列重排。
STEP = 1024.0
MIN_GAP = 1e-6


class Invalid(ValueError):
    """前端能改掉的输入错误；`args[0]` 是术语键。"""


def status(value) -> str:
    if value not in STATUSES:
        raise Invalid("cowork.work.error.status")
    return value


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


def checklist(items) -> list:
    """[{title, done}] → 清洗后的同形列表；空标题的行丢掉。"""
    if items in (None, ""):
        return []
    if not isinstance(items, list):
        raise Invalid("cowork.work.error.checklist")
    rows = []
    for item in items:
        if not isinstance(item, dict):
            raise Invalid("cowork.work.error.checklist")
        title = text(item.get("title"), CHECKLIST_ITEM_MAX)
        if title:
            rows.append({"title": title, "done": 1 if item.get("done") in (1, True, "1") else 0})
    if len(rows) > CHECKLIST_MAX:
        raise Invalid("cowork.work.error.too_many")
    return rows


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


def position_between(before, after):
    """`before` / `after` 是落点上下两张卡的位置（没有就是 None）。

    返回新位置；两卡之间已经挤不下时返回 None，由调用方整列重排后再算。
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
