"""团队管理「添加用户」邮箱留空时的占位邮箱（用户 2026-10-27，同控制面开户单的「前缀@pearnly.com」）。

`<登录名>.<本站域名前缀>@pearnly.com`，全小写。这不是真邮箱：pearnly.com 的收信在 Cloudflare 邮件转发，
catch-all 转到开户人邮箱，只用来兜底收「忘记密码」这类信。站点上不记租户第一行产品是哪个，取本站自己的
域名前缀（站点名第一段，开户时已全局查重）；登录名在本站唯一，所以生成的地址也唯一。地址里带点，
撞不上 `hello@pearnly.com` 这类官方地址。ERP / Cowork 的团队管理共用。
"""
import re

import frappe

DOMAIN = "pearnly.com"
# 手填的邮箱：与控制面开户单同一条（`pearnly_issue.EMAIL_RE`）
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid(email: str) -> bool:
    return bool(EMAIL_RE.match(email or ""))


def for_user(username: str) -> str:
    prefix = str(frappe.local.site or "").split(".")[0].strip().lower()
    return f"{str(username or '').strip().lower()}.{prefix}@{DOMAIN}"
