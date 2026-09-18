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
    "DMS 管理员",
    "Cowork 管理员",
    "ERP 管理员",
)


def _require_login() -> str:
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("请先登录"), frappe.AuthenticationError)
    return user


def _require_admin() -> str:
    user = _require_login()
    if not set(ADMIN_ROLES) & set(frappe.get_roles(user)):
        frappe.throw(_("充值与订阅只有公司管理员能操作"), frappe.PermissionError)
    return user


def _client():
    from pearnly_core import billing_client

    if not billing_client.configured():
        frappe.throw(_("本站还没接上计费底座，请联系平台"))
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


@frappe.whitelist(methods=["POST"])
def cancel_subscription() -> dict:
    """取消订阅：本周期额度用到到期，不再续（不退款）。"""
    _require_admin()
    return _client().cancel_subscription()
