"""Permission-scoped workbook for cashier shifts（交班记录导出）。

取数复用 `shifts.list_shifts`，所以「非管理员只能看自己的班」这条口径与页面完全一致：
勾选了别人的班次号也只会导出自己看得到的那几行。
"""
from frappe import _
from frappe.utils import get_fullname
from frappe.utils.xlsxutils import build_xlsx_response

from pearnly_erp_portal.cashier import shifts

# 与「交班记录」页同一组列、同一组词条（`trade.*` 的英文原文，翻译表按它取中/泰/日）。
COLUMNS = [("name", "Shift No"), ("terminal", "Terminal"), ("cashier", "Cashier"),
           ("status", "Status"), ("opened_at", "Opened At"), ("closed_at", "Closed At"),
           ("opening_float", "Opening Cash"), ("expected_cash", "Expected Cash"),
           ("counted_cash", "Counted Cash"), ("cash_diff", "Difference")]

STATUS_LABELS = {"open": "Open", "closed": "Closed"}


def cashier_label(user):
    """收银员按「全名（账号）」落表，与页面显示的写法一致。"""
    if not user:
        return ""
    fullname = get_fullname(user)
    return fullname if fullname == user else f"{fullname} ({user})"


def cell(row, field):
    value = row.get(field)
    if field == "status":
        return _(STATUS_LABELS.get(value, value or ""))
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


def workbook(names=None) -> None:
    data = [[_(label) for _field, label in COLUMNS]]
    for row in rows(names):
        data.append([cell(row, field) for field, _label in COLUMNS])
    build_xlsx_response(data, _("Shift Records"))
