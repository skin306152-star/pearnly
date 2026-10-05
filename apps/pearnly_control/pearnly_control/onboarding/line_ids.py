"""LIFF ID 与渠道凭据：开户单与 LINE 渠道共用的纯校验，不依赖 Frappe。

站点只认 `<LINE Login 频道号>-<应用码>` 这种原样的 ID（`pearnly_core.line.verify_id_token`
按短横线拆出频道号去验身份）。LINE 后台同时显示 LIFF URL，开户人很容易整条贴进来；
存成 URL 的话站点验签会一律失败且不报错，所以在保存时就归一或拒绝。
"""

import re

LIFF_ID_RE = re.compile(r"^\d+-[A-Za-z0-9]+$")
# LINE 后台给的两种链接：https://liff.line.me/<ID>[/路径][?参数] 与旧式 line://app/<ID>
LIFF_URL_RE = re.compile(r"^(?:https?://liff\.line\.me/|line://app/)([^/?#]+)")


def normalize_liff_id(value: str | None) -> str:
    """去空白；LIFF URL 取出其中的 ID；空值原样为空。格式不对抛 ValueError。"""
    text = (value or "").strip()
    if not text:
        return ""
    match = LIFF_URL_RE.match(text)
    candidate = match.group(1) if match else text
    if not LIFF_ID_RE.match(candidate):
        raise ValueError(text)
    return candidate


def clean_credential(value: str | None) -> str:
    """渠道密钥 / 访问令牌去首尾空白（从 LINE 后台复制常带空格）。

    已存过的密码字段在单据上是一串 `*`（Frappe 的占位，真值在 `__Auth`），原样返回。
    """
    text = value or ""
    if text and set(text) == {"*"}:
        return text
    return text.strip()


def incomplete(channel_id: str | None, secret: str | None, token: str | None = None) -> bool:
    """填了 LINE 渠道却用不起来：保存 / 规划时必须当场拒绝。

    2026-10-05 起开户只要 Channel ID + Channel secret，令牌由系统用这两个值现取。长期令牌仍可填
    （之前开的租户）：有密钥并且有频道号或长期令牌就算齐；一格都没填是"这次不配 LINE"。
    """
    if not (channel_id or secret or token):
        return False
    return not (secret and (channel_id or token))
