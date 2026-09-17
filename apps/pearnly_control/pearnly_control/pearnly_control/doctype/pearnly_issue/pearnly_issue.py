# -*- coding: utf-8 -*-
"""发号单：一次开户动作。

一张单子表达"给某家公司开哪几个产品、各自的域名是什么、给什么 OCR 能力"。
提交时校验域名与账号，创建租户与登录账号，产出**开通清单**。

真正的建站执行（建 Site、建库、装 App、绑证书）不在这里做——那是后续阶段。
本单只保证：**要开什么被完整、无歧义地记下来了**。

硬规则：
- 有域名冲突就拒绝，不做"先建了再说"。
- 初始密码**不落库**，只在提交时返回一次。
- 账号已存在时拒绝，不按邮箱合并到已有账号（那是另一个流程）。
"""

import re

import frappe
from frappe import _
from frappe.model.document import Document

DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class PearnlyIssue(Document):
    def validate(self):
        self._validate_account()
        self._validate_apps()
        self._validate_ocr()

    def _validate_account(self):
        self.account_email = (self.account_email or "").strip().lower()
        if not EMAIL_RE.match(self.account_email):
            frappe.throw(_("登录账号必须是合法邮箱"))
        self.tenant_name = (self.tenant_name or "").strip()
        if not self.tenant_name:
            frappe.throw(_("租户名称必填"))

    def _validate_apps(self):
        if not self.apps:
            frappe.throw(_("至少要选一个产品"))
        seen_apps, seen_domains = set(), {}
        for row in self.apps:
            if row.app in seen_apps:
                frappe.throw(_("产品 {0} 选了两次").format(row.app))
            seen_apps.add(row.app)

            domain = (row.domain or "").strip().lower()
            if not DOMAIN_RE.match(domain):
                frappe.throw(_("域名格式不对：{0}").format(row.domain or "(空)"))
            if domain in seen_domains:
                frappe.throw(_("域名在同一张单里重复：{0}").format(domain))
            seen_domains[domain] = row.app

            # 全局唯一：不允许两个租户（或两次发号）撞同一个域名
            other = frappe.db.get_value(
                "Pearnly Issue App",
                {"domain": domain, "parent": ("!=", self.name)},
                ["parent"],
                as_dict=True,
            )
            if other:
                frappe.throw(
                    _("域名已被占用：{0}（发号单 {1}）").format(domain, other.parent)
                )
            row.domain = domain

    def _validate_ocr(self):
        # A 档（高精／财务扫描件）与 B 档（经济／日常发票）是两种能力，
        # 不要求同时选，也不允许"都不选却声称有 OCR"。
        if not self.ocr_economy and not self.ocr_enterprise:
            self.ocr_summary = "未开通"
        elif self.ocr_enterprise and self.ocr_economy:
            self.ocr_summary = "A 档 + B 档"
        elif self.ocr_enterprise:
            self.ocr_summary = "仅 A 档（高精·财务扫描件）"
        else:
            self.ocr_summary = "仅 B 档（经济·日常发票）"
