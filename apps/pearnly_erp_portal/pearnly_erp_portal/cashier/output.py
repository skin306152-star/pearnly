"""Permission-scoped workbook for cashier shifts（交班记录导出）。

取数复用 `shifts.list_shifts`，所以「非管理员只能看自己的班」这条口径与页面完全一致：
勾选了别人的班次号也只会导出自己看得到的那几行。

表头**按界面那套键取词**（`trade.*`），不写英文原文 —— 术语表是唯一来源，
导出件与页面上是同一句话（用户 2026-09-23："导出的报表术语表口径要统一"）。
"""
from frappe import _
from frappe.utils.xlsxutils import make_xlsx
from pearnly_core import browser_text

from pearnly_erp_portal.cashier import shifts

# 与「交班记录」页同一组列、同一组键。
COLUMNS = [("name", "trade.shiftNo"), ("terminal", "trade.terminal"),
           ("cashier", "trade.cashier"), ("status", "trade.status"),
           ("opened_at", "trade.openedAt"), ("closed_at", "trade.closedAt"),
           ("opening_float", "trade.float"), ("expected_cash", "trade.expectedCash"),
           ("counted_cash", "trade.countedCash"), ("cash_diff", "trade.diff")]

def cashier_label(user):
    """收银员落表写**用户名**（用户 2026-09-24："全局可以涉及到姓名的地方，全部改为用户名"）。

    没有用户名的老账号回落账号本身（邮箱），不让这一格空着。
    """
    from pearnly_erp_portal.team import service as team

    return team.username_of(user)


def cell(row, field):
    value = row.get(field)
    if field == "status":
        # 与页面上那两个状态同一套词（`trade.state.open/closed` → 开班中 / 已交班）。
        return browser_text.label(f"trade.state.{value}") if value else ""
    if field == "cashier":
        return cashier_label(value)
    if field in ("opened_at", "closed_at"):
        return str(value or "")
    if field in ("opening_float", "expected_cash", "counted_cash", "cash_diff"):
        return float(value or 0)
    # 绝不让人填的文本变成 Excel 公式。
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def rows(names=None) -> list[dict]:
    return shifts.list_shifts(names=names, limit=0)


def export_size(names=None) -> int:
    """导出的班次数（与导出同一份取数、同一道权限：非管理员只看自己的班）。"""
    return len(rows(names))


def workbook(names=None) -> dict:
    data = [[browser_text.label(key) for _field, key in COLUMNS]]
    for row in rows(names):
        data.append([cell(row, field) for field, _label in COLUMNS])
    title = _("Shift Records")
    return {"filename": f"{title}.xlsx", "content": make_xlsx(data, title).getvalue()}
