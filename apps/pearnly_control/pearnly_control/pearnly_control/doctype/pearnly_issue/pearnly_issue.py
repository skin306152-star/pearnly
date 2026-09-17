# -*- coding: utf-8 -*-
"""发号单：一次开户动作。

一张单子表达"给某家公司开哪几个产品、各自的域名是什么、开哪些功能、给什么 OCR 能力"。
提交时校验域名、账号与功能，创建租户与登录账号，产出**开通清单**。

真正的建站执行（建 Site、建库、装 App、绑证书）不在这里做——那是后续阶段。
本单只保证：**要开什么被完整、无歧义地记下来了**。

硬规则：
- 域名冲突就拒绝，不做"先建了再说"。
- 账号已存在时拒绝，不按邮箱合并到已有账号（那是另一个流程）。
- 凭据要么走邀请邮件（用户自己设密码），要么当场生成一次性初始密码；**都不落库**。
"""

import re

import frappe
from frappe import _
from frappe.model.document import Document

from pearnly_control import features as features_mod
from pearnly_control.domains import base_domain

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# 域名前缀：一层标签。只允许小写字母、数字、短横线，首尾不能是短横线。
# 用户 2026-09-17 定的规则：开通时只填前缀，系统补成「前缀.pearnly.com」。
PREFIX_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

# 不让租户占用这些前缀：要么是基础设施在用，要么会被误认成官方入口。
# 通配符证书 *.pearnly.com 会把这些名字一起盖住，所以必须在开通这一步挡住。
RESERVED_PREFIXES = {
    "www", "api", "app", "admin", "mail", "smtp", "imap", "pop", "webmail", "mx",
    "ns", "ns1", "ns2", "dns", "cdn", "static", "assets", "img", "files",
    "status", "support", "help", "docs", "blog", "shop", "store", "pay", "billing",
    "keycloak", "auth", "sso", "id", "login", "account", "accounts", "console",
    "control", "desk", "dev", "test", "staging", "stage", "demo", "sandbox",
    "pearnly", "cowork", "erp", "dms", "internal", "monitor", "grafana",
}


class PearnlyIssue(Document):
    def validate(self):
        self._validate_account()
        self._validate_apps()
        self._validate_features()
        self._validate_ocr()

    # ── 基础信息 ──────────────────────────────────────────────
    def _validate_account(self):
        self.account_email = (self.account_email or "").strip().lower()
        if not EMAIL_RE.match(self.account_email):
            frappe.throw(_("登录账号必须是合法邮箱"))
        self.tenant_name = (self.tenant_name or "").strip()
        if not self.tenant_name:
            frappe.throw(_("租户名称必填"))
        self.contact_phone = (self.contact_phone or "").strip()

    # ── 产品与域名 ────────────────────────────────────────────
    def _validate_apps(self):
        if not self.apps:
            frappe.throw(_("至少要选一个产品"))
        seen_apps, seen_domains = set(), {}
        for row in self.apps:
            if row.app in seen_apps:
                frappe.throw(_("产品 {0} 选了两次").format(row.app))
            seen_apps.add(row.app)

            prefix = (row.domain_prefix or "").strip().lower()
            if not PREFIX_RE.match(prefix):
                frappe.throw(
                    _("域名前缀不合法：{0}。只能用小写字母、数字和短横线，"
                      "首尾不能是短横线，长度 1–63").format(row.domain_prefix or "(空)")
                )
            if prefix.isdigit():
                frappe.throw(_("域名前缀不能全是数字：{0}").format(prefix))
            if prefix in RESERVED_PREFIXES:
                frappe.throw(_("域名前缀 {0} 是保留名，换一个").format(prefix))

            # 域名由前缀统一生成，不让手填，避免同一件事有两种写法
            domain = f"{prefix}.{base_domain()}"
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
            row.domain_prefix = prefix
            row.domain = domain

    # ── 功能模版 ──────────────────────────────────────────────
    def _validate_features(self):
        """勾的功能必须：属于对应产品、且那个产品确实开通了。

        「归到哪个产品」是靠表本身区分的（cowork_features / erp_features /
        dms_features），所以这里要挡住"把 ERP 的功能勾进 Cowork 那一栏"。
        """
        selected = {row.app for row in self.apps}
        tables = {
            "cowork": self.cowork_features,
            "erp": self.erp_features,
            "dms": self.dms_features,
        }
        picked = {}
        for product, rows in tables.items():
            if rows and product not in selected:
                frappe.throw(
                    _("填了 {0} 的功能，但上面的产品里没有选 {0}").format(
                        features_mod.PRODUCT_LABELS.get(product, product))
                )
            seen = set()
            for row in rows or []:
                if not row.feature:
                    frappe.throw(_("功能行里有一项没选功能"))
                if row.feature in seen:
                    frappe.throw(_("功能重复勾选：{0}").format(row.feature))
                seen.add(row.feature)

                actual = frappe.db.get_value("Pearnly Feature", row.feature, "product")
                if not actual:
                    frappe.throw(_("功能不存在：{0}").format(row.feature))
                if actual != product:
                    frappe.throw(
                        _("功能 {0} 属于 {1}，不能勾在 {2} 那一栏").format(
                            row.feature, actual, product)
                    )
            picked[product] = sorted(seen)
        self.feature_summary = "；".join(
            f"{features_mod.PRODUCT_LABELS[p]} {len(picked[p])} 项"
            for p in features_mod.PRODUCTS if picked.get(p)
        )

    # ── OCR ───────────────────────────────────────────────────
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
