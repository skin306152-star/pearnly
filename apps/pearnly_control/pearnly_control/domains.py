# -*- coding: utf-8 -*-
"""租户域名的生成规则。

用户 2026-09-17 定的规则：开通时**只填前缀**，系统补成「前缀.pearnly.com」。
例如填 `aerp` → `aerp.pearnly.com`；填 `bcowork` → `bcowork.pearnly.com`。

为什么是一层（`xxx.pearnly.com`）而不是两层（`xxx.cowork.pearnly.com`）：
线上证书实测覆盖 `*.pearnly.com` 与 `pearnly.com`（Let's Encrypt，Cloudflare 通用证书），
而通配符按标准只匹配**一层**。所以一层写法零证书工作，两层要另外买证书。

基础域名可在站点配置里覆盖（`pearnly_base_domain`），换域名时不用改代码。
"""

import frappe

DEFAULT_BASE_DOMAIN = "pearnly.com"


def base_domain(target=None) -> str:
    from pearnly_control.infra import target_settings
    configured = target_settings(target).get("base_domain")
    if configured:
        return configured.strip().lower()
    return (frappe.conf.get("pearnly_base_domain") or DEFAULT_BASE_DOMAIN).strip().lower()


def domain_for(prefix: str) -> str:
    """前缀 → 完整域名。前缀应当已经过 `PREFIX_RE` 校验。"""
    return f"{(prefix or '').strip().lower()}.{base_domain()}"
