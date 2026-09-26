"""LINE 登录的共享约定（多产品共用）：身份提供者别名、ID token 里的登录来源声明。

纯函数：不依赖框架、不做 IO。站点、计费站点的建号服务与服务器上的开通步骤
都按这里算别名，三处必须一致 —— 别名一旦对不上，关联写进了 A、登录走的是 B。
"""
import re

# Keycloak 身份提供者别名前缀；realm 里只允许这一类外部身份提供者（隐藏、拒绝首次登录）。
IDP_PREFIX = "line-"
# 由 realm 默认客户端作用域 `pearnly-login-source` 写进 ID token（会话备注映射器）。
CLAIM_IDP = "pearnly_idp"
CLAIM_LINE_USER = "pearnly_line_user_id"
# 可以接 LINE 登录的产品。
PRODUCTS = ("erp", "dms", "cowork")

_LINE_USER = re.compile(r"U[0-9a-f]{32}")


def idp_alias(product: str, tenant: str) -> str:
    """每个站点（产品 × 租户）一个身份提供者：`line-<产品>-<租户>`。"""
    if product not in PRODUCTS:
        raise ValueError("product")
    slug = re.sub(r"[^a-z0-9]+", "-", str(tenant or "").lower()).strip("-")
    if not slug or len(slug) > 60:
        raise ValueError("tenant")
    return f"{IDP_PREFIX}{product}-{slug}"


def valid_line_user_id(value) -> bool:
    """LINE 用户 ID 形如 `U` + 32 位十六进制（同一 Provider 下的 Messaging API 与 LINE Login 一致）。"""
    return isinstance(value, str) and bool(_LINE_USER.fullmatch(value))
