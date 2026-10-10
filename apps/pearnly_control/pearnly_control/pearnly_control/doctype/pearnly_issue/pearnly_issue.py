# -*- coding: utf-8 -*-
"""发号单：一次开户动作。

一张单子表达"给某家公司开哪几个产品、各自的域名是什么"。
提交时校验域名与账号，创建租户与登录账号，产出**开通清单**。

**功能不再单独勾选**（用户 2026-09-18 定）：选了哪个产品，那个产品的功能就全开。

真正的建站执行（建 Site、建库、装 App、绑证书）不在这里做——那是后续阶段。
本单只保证：**要开什么被完整、无歧义地记下来了**。

硬规则：
- 域名冲突就拒绝，不做"先建了再说"。
- 账号已存在时拒绝，不按邮箱合并到已有账号（那是另一个流程）。

账号三件套（用户 2026-09-18 定）：**邮箱 + 用户名 + 初始密码**。
这三样构成"首次绑定关系"——用户拿到初始密码**当天就能登录**（不强制改密），
之后可以在系统内改密码，也可以在登录页用邮箱走自助改密。

⚠️ 初始密码**会落库**（Frappe `Password` 字段 → `__Auth` 加密表）。
老设计里密码是当场随机生成、只在返回值里出现一次；现在由开户人指定，
要活到"执行开通"那一刻，所以必须存。存的是密文，接口**不回显**。
"""

import re

import frappe
from frappe import _
from frappe.model.document import Document

from pearnly_control import features as features_mod
from pearnly_control.domains import base_domain
from pearnly_control.onboarding import assistant
from pearnly_control.onboarding.line_ids import clean_credential, incomplete, normalize_liff_id

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# 域名前缀：一层标签。只允许小写字母、数字、短横线，首尾不能是短横线。
# 用户 2026-09-17 定的规则：开通时只填前缀，系统补成「前缀.pearnly.com」。
PREFIX_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

# 用户名：登录标识之一。不跟邮箱抢语义，所以只收常见的账号字符。
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,30}$")

# 不让租户占用这些前缀：要么是基础设施在用，要么会被误认成官方入口。
# 通配符证书 *.pearnly.com 会把这些名字一起盖住，所以必须在开通这一步挡住。
RESERVED_PREFIXES = {
    "www", "api", "app", "admin", "mail", "smtp", "imap", "pop", "webmail", "mx",
    "ns", "ns1", "ns2", "dns", "cdn", "static", "assets", "img", "files",
    "status", "support", "help", "docs", "blog", "shop", "store", "pay", "billing",
    "keycloak", "auth", "sso", "id", "login", "account", "accounts", "console",
    "control", "desk", "dev", "test", "staging", "stage", "demo", "sandbox",
    "pearnly", "cowork", "erp", "dms", "internal", "monitor", "grafana", "work", "ai", "testai",
}



class PearnlyIssue(Document):
    def validate(self):
        # Existing issued history remains readable; new or reopened requests are cloud-only.
        if self.is_new() or self.status != "issued" or self.has_value_changed("target"):
            from pearnly_control.onboarding.targets import require_cloud_target

            self.target = require_cloud_target(self.target)
        self._validate_account()
        assistant.validate(self)
        self._validate_apps()
        self._summarize_features()

    # ── 基础信息 ──────────────────────────────────────────────
    def _validate_account(self):
        self.account_email = (self.account_email or "").strip().lower()
        if not EMAIL_RE.match(self.account_email):
            frappe.throw(_("Invalid email"))
        self.account_username = (self.account_username or "").strip()
        if not USERNAME_RE.match(self.account_username):
            frappe.throw(_("Username may only contain letters, digits, dot, underscore and hyphen, length 3–30"))
        # 初始密码**不查复杂度**（用户明确要求别卡太严：纯字母、纯数字都行），只要不是空的。
        # 卡得越严，开户现场越容易卡在"想不出密码"上，而这是他自己租户的账号。
        self.account_password = (self.account_password or "").strip()
        if not self.account_password:
            frappe.throw(_("Initial password is required (the user signs in with it directly)"))
        self.tenant_name = (self.tenant_name or "").strip()
        if not self.tenant_name:
            frappe.throw(_("Company name is required"))

    # ── 产品与域名 ────────────────────────────────────────────
    def _validate_apps(self):
        if not self.apps:
            frappe.throw(_("Pick at least one product"))
        seen_apps, seen_domains = set(), {}
        for row in self.apps:
            if row.app in seen_apps:
                frappe.throw(_("Product {0} is selected twice").format(row.app))
            seen_apps.add(row.app)

            prefix = (row.domain_prefix or "").strip().lower()
            if not PREFIX_RE.match(prefix):
                frappe.throw(
                    _("Invalid domain prefix: {0}. Use lowercase letters, digits and hyphens only, no leading or trailing hyphen, length 1–63").format(row.domain_prefix or _("(empty)"))
                )
            if prefix.isdigit():
                frappe.throw(_("Domain prefix cannot be all digits: {0}").format(prefix))
            if prefix in RESERVED_PREFIXES:
                frappe.throw(_("Domain prefix {0} is reserved; pick another").format(prefix))

            # 域名由前缀统一生成，不让手填，避免同一件事有两种写法
            domain = f"{prefix}.{base_domain(self.target)}"
            if domain in seen_domains:
                frappe.throw(_("Duplicate domain on the same issue: {0}").format(domain))
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
                    _("Domain already taken: {0} (issue {1})").format(domain, other.parent)
                )
            row.domain_prefix = prefix
            row.domain = domain
            # LINE 后台给的是 LIFF URL，贴进来就归一成 ID；别的写法当场拒绝
            try:
                row.line_liff_id = normalize_liff_id(row.line_liff_id)
            except ValueError:
                frappe.throw(_("Invalid LIFF ID: {0}. Enter only the ID, e.g. 2010411313-abcd1234").format(row.line_liff_id))
            self._validate_line_credentials(row)

    def _validate_line_credentials(self, row):
        """LINE 凭据存之前去空白；没发号的单子只填了一半就当场拒绝。

        ISS-2026-0294（2026-09-29）：访问令牌没进保存请求，单子照样存下、照样发号，
        开通时渠道被静默跳过。在保存这一步拦住，开户人当场就知道要补哪一格。
        2026-10-05 起只要 Channel ID + Channel secret（令牌系统现取、LIFF 系统建），旧单带的长期令牌照旧认。
        已发号的历史单只去空白：LINE 渠道此后在「LINE 渠道」里维护，不让旧单挡住别的修改。
        """
        row.line_channel_id = (row.line_channel_id or "").strip()
        row.line_channel_secret = clean_credential(row.line_channel_secret)
        row.line_channel_access_token = clean_credential(row.line_channel_access_token)
        if self.status != "issued" and incomplete(row.line_channel_id, row.line_channel_secret,
                                                  row.line_channel_access_token):
            frappe.throw(_("Row {0}: a LINE channel needs both the Channel ID and the Channel secret").format(row.app))

    # ── 已开通功能 ────────────────────────────────────────────
    def _summarize_features(self):
        """选了哪个产品，那个产品的功能就**全开**（用户 2026-09-18 定）。

        以前这里是"勾选校验"：三张功能模版表 + 归属校验 + 摘要。现在没有勾选了，
        剩下的事只有一件 —— 把结果写清楚给操作人看。
        """
        selected = {row.app for row in self.apps}
        self.feature_summary = "; ".join(
            _("{0} — {1} features").format(
                features_mod.PRODUCT_LABELS[p], len(features_mod.codes_of(p))
            )
            for p in features_mod.PRODUCTS
            if p in selected
        )
