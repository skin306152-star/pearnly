"""LINE 登录的共享约定（多产品共用）：身份提供者别名、ID token 里的登录来源声明。

纯函数：不依赖框架、不做 IO。站点、计费站点的建号服务与服务器上的开通步骤
都按这里算别名，三处必须一致 —— 别名一旦对不上，关联写进了 A、登录走的是 B。

**一个 LINE Login 频道一个入口**（用户 2026-09-26：密钥全平台只配一次）：同一 Provider 下
所有租户的 LIFF 都挂在这个频道，LINE 用户 ID 一致，所以入口共用、回调地址只登记一次。
"只能进绑定了它的站点"由各站点回调里"必须是本站该账号生效的绑定"保证，不靠别名区分。
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
_CHANNEL = re.compile(r"\d{6,20}")


def login_channel_of(liff_id) -> str:
    """LIFF ID 形如 `<LINE Login 频道号>-<应用码>`；取不出来就空串。"""
    channel = str(liff_id or "").strip().split("-", 1)[0]
    return channel if _CHANNEL.fullmatch(channel) else ""


def idp_alias(login_channel: str) -> str:
    """LINE Login 频道 → 身份提供者别名 `line-<频道号>`。"""
    if not _CHANNEL.fullmatch(str(login_channel or "")):
        raise ValueError("login_channel")
    return f"{IDP_PREFIX}{login_channel}"


def valid_line_user_id(value) -> bool:
    """LINE 用户 ID 形如 `U` + 32 位十六进制（同一 Provider 下的 Messaging API 与 LINE Login 一致）。"""
    return isinstance(value, str) and bool(_LINE_USER.fullmatch(value))
