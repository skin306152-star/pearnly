# -*- coding: utf-8 -*-
"""产品站点上的「订阅与计费」页用的接口（三个产品同一套）。

页面是同源的（在产品站点上），钱在控制面 —— 所以这里是**中转**：
站点自己的登录用户 → 本站这个白名单方法 → 控制面（服务令牌）。

为什么不直接让浏览器去调控制面：控制面只在本机跑、地址也不一样，
而且服务令牌绝不能下发到浏览器里。钱的动作一律走服务端。

权限：查看要登录；**充值与订阅只有管理员**能做（普通员工治不了公司的钱，
这一点与老产品一致：那两个动作是 owner_only）。
"""

from __future__ import annotations

import frappe
from frappe import _

ADMIN_ROLES = (
    "System Manager",
    "DMS Admin",
    "Cowork Admin",
    "ERP Admin",
)


def _require_login() -> str:
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Please sign in"), frappe.AuthenticationError)
    return user


def _require_admin() -> str:
    user = _require_login()
    if not set(ADMIN_ROLES) & set(frappe.get_roles(user)):
        frappe.throw(
            _("Only a company admin can top up or change the subscription"),
            frappe.PermissionError,
        )
    return user


def _client():
    from pearnly_core import billing_client

    if not billing_client.configured():
        frappe.throw(_("Billing is not wired up on this site, please contact the platform"))
    return billing_client


@frappe.whitelist()
def home() -> dict:
    """首页要的全部：余额、流水、当前套餐、套餐目录、待审核的充值。"""
    _require_login()
    data = _client().home()
    # 管理员才知道公司账目；普通员工只看得到"有没有余额"这一层
    data["can_manage"] = bool(set(ADMIN_ROLES) & set(frappe.get_roles(frappe.session.user)))
    return data


@frappe.whitelist(methods=["POST"])
def topup(amount_thb: float, note: str = "", payer_name: str = "") -> dict:
    """提交充值申请。**只落申请，余额要等平台审核通过才变。**"""
    _require_admin()
    return _client().topup_request(float(amount_thb or 0), note=note, payer_name=payer_name)


@frappe.whitelist(methods=["POST"])
def subscribe(plan_code: str) -> dict:
    """订阅 / 换套餐：当场从余额扣月费。余额不够会返回 insufficient_balance。"""
    _require_admin()
    return _client().subscribe(plan_code)


# ── 记录明细（扣费 / 充值 / 识别）──────────────────────────────────────────


def _period_range(period: str, anchor: str) -> tuple[str, str]:
    """把 day / month / year / all 换成 [start, end) 两个日期。

    日期锚按 **Asia/Bangkok（UTC+7）** 算 —— 与计费的"月度"口径保持一致，
    否则"本月"在账单上和在筛选里会是两个范围（老产品踩过这个）。
    """
    from datetime import datetime, timedelta, timezone

    if period not in ("day", "month", "year"):
        return "", ""
    try:
        d = datetime.strptime(anchor, "%Y-%m-%d").date()
    except Exception:
        d = (datetime.now(timezone.utc) + timedelta(hours=7)).date()
    if period == "day":
        return d.isoformat(), (d + timedelta(days=1)).isoformat()
    if period == "month":
        start = d.replace(day=1)
        end = (start.replace(year=start.year + 1, month=1) if start.month == 12
               else start.replace(month=start.month + 1))
        return start.isoformat(), end.isoformat()
    start = d.replace(month=1, day=1)
    return start.isoformat(), start.replace(year=start.year + 1).isoformat()


def _ocr_rows(start: str, end: str, limit: int) -> list[dict]:
    """识别记录来自**本站**（每个租户一个站点，识别就发生在这里）。"""
    filters = {}
    if start or end:
        filters["creation"] = ("between", [start or "1970-01-01", end or "2999-12-31"])
    return frappe.get_all(
        "Pearnly OCR Record", filters=filters,
        fields=["name", "creation", "product", "status", "filename", "page_count",
                "charged_thb", "detail", "error"],
        order_by="creation desc", limit_page_length=int(limit),
    )


@frappe.whitelist()
def records(tab: str = "usage", period: str = "all", date: str = "", limit: int = 100) -> dict:
    """记录明细：`usage`（扣费）/ `topup`（充值）/ `ocr`（识别）。"""
    user = _require_login()
    is_admin = bool(set(ADMIN_ROLES) & set(frappe.get_roles(user)))
    if tab == "topup" and not is_admin:
        # 充值是公司账目，普通员工看不到（老产品同款口径）
        frappe.throw(_("Only a company admin can view top-up records"), frappe.PermissionError)

    start, end = _period_range(period, date)
    if tab == "ocr":
        rows = _ocr_rows(start, end, int(limit))
    else:
        rows = _client().records(tab, start=start, end=end, limit=int(limit)).get("rows") or []
    return {"tab": tab, "period": period, "start": start, "end": end, "rows": rows}


@frappe.whitelist()
def export_records(date_from: str = "", date_to: str = "") -> None:
    """导出全量明细 → 一个 xlsx，三个 sheet（扣费 / 充值 / 识别）。

    列名跟着界面语言走：导出来是给人看的，不是给程序读的。
    """
    user = _require_login()
    is_admin = bool(set(ADMIN_ROLES) & set(frappe.get_roles(user)))
    start, end = (date_from or ""), (date_to or "")

    usage = _client().records("usage", start=start, end=end, limit=5000).get("rows") or []
    topup = (_client().records("topup", start=start, end=end, limit=5000).get("rows") or []
             if is_admin else [])
    ocr = _ocr_rows(start, end, 5000)

    import io

    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)

    def add_sheet(title: str, headers: list[str], rows: list[list]) -> None:
        ws = wb.create_sheet(title[:31])
        ws.append(headers)
        for r in rows:
            ws.append(r)
        for i, _h in enumerate(headers, start=1):
            ws.column_dimensions[get_column_letter(i)].width = 20

    add_sheet(
        _("Charges"),
        [
            _("Time"),
            _("Product"),
            _("Pages"),
            _("Amount"),
            _("Balance"),
            _("Description"),
            _("Reference"),
        ],
        [[str(r.get("creation") or "")[:16], r.get("product") or "", r.get("pages") or 0,
          float(r.get("amount_thb") or 0), float(r.get("balance_after") or 0),
          r.get("description") or "", r.get("reference") or ""] for r in usage],
    )
    if is_admin:
        add_sheet(
            _("Top-ups"),
            [
                _("Time"),
                _("Reference"),
                _("Status"),
                _("Requested"),
                _("Received"),
                _("Payer"),
                _("Notes"),
            ],
            [[str(r.get("creation") or "")[:16], r.get("name") or "", r.get("status") or "",
              float(r.get("requested_thb") or 0), float(r.get("approved_thb") or 0),
              r.get("payer_name") or "", r.get("review_note") or r.get("note") or ""]
             for r in topup],
        )
    add_sheet(
        _("Recognition Records"),
        [
            _("Time"),
            _("Product"),
            _("Result"),
            _("File"),
            _("Pages"),
            _("Charges"),
            _("Summary"),
            _("Failure reason"),
        ],
        [[str(r.get("creation") or "")[:16], r.get("product") or "", r.get("status") or "",
          r.get("filename") or "", r.get("page_count") or 0,
          float(r.get("charged_thb") or 0), r.get("detail") or "", r.get("error") or ""]
         for r in ocr],
    )

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    frappe.local.response.filename = "pearnly-billing.xlsx"
    frappe.local.response.filecontent = buf.read()
    frappe.local.response.type = "download"
