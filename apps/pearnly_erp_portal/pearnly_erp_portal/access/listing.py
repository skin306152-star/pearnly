"""列表排序（列头点一下排序）的公共判据。

产品列表都是**服务端分页**的：一页只取 N 行，所以排序必须在服务端做。前端只把
`sort_by` / `sort_order` 交上来，这里按**每个列表自己的白名单**换算成 SQL 的 `order_by`
或者内存排序用的键。

两条硬规矩：

· **只认白名单**。`order_by` 是要拼进 SQL 的，绝不能直接吃用户输入；认不出来的字段
  一律回落到该列表的默认顺序 —— 用户点了一个后端不认的列，看到的应该是"原来的顺序"，
  而不是一个报错，更不是别人拿这个参数去排任意列。
· **方向只认 asc / desc**，别的一律当 asc。
"""

from decimal import Decimal


def order_by(sort_by, sort_order, allowed: dict, default: str) -> str:
    """SQL 列表用：白名单命中就给 `"<列> <方向>"`，否则给默认顺序。"""
    field = allowed.get(str(sort_by or "").strip())
    if not field:
        return default
    direction = "desc" if str(sort_order or "").strip().lower() == "desc" else "asc"
    return f"{field} {direction}"


def sort_rows(rows: list, sort_by, sort_order, allowed: dict) -> list:
    """内存列表用（库存一览那种先算好整批、再切片分页的）。

    空值（None / 空串）不管升序降序都排在最后：列表里"没设过"的行堆在最前面最烦人。
    """
    field = allowed.get(str(sort_by or "").strip())
    if not field:
        return rows
    reverse = str(sort_order or "").strip().lower() == "desc"
    filled = [row for row in rows if row.get(field) not in (None, "")]
    empty = [row for row in rows if row.get(field) in (None, "")]
    filled.sort(key=lambda row: _comparable(row.get(field)), reverse=reverse)
    return filled + empty


def _comparable(value):
    """把一行里那个值换成"一定比得动"的形状。

    为什么不能直接比：同一列里可能混着两种类型 —— 销售列表按"单据日期"排就是
    （单据那边是 `datetime.date`，收银小票那边是字符串），直接 `sort` 会抛
    `TypeError: '<' not supported between instances of 'str' and 'datetime.date'`。
    数字（含 Decimal）按数值比，其余（日期也算）按文本比 —— 日期的文本是 ISO 形状，
    字符串序与时间序一致，所以这样排出来的顺序是对的。
    """
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return (1, value, "")
    return (0, 0, str(value))
