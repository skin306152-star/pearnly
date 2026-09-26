"""LIFF ID 的格式：开户单与 LINE 渠道共用的纯校验，不依赖 Frappe。

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
