# -*- coding: utf-8 -*-
"""LINE 菜单第 6 项「คู่มือ」：子菜单 6.0 / 6.1 / 6.2 → 在 LINE 里直接播放教程视频。

客户 Korn 2026-10 提出：把教程视频放进第 6 项（6.0 首次设置、6.1 建立客户资料、6.2 开订车单）。
条目、动作码、配置键都由产品在这里自己声明（ADR-009），控制面与共用层不保存镜像。

**视频地址不写死在代码里**。站点配置（`site_config.json`；整台 bench 共用就写
`common_site_config.json`，`frappe.conf` 两者合并读）里每个条目一个键：

    "pearnly_dms_manual_setup":    {"video": "https://<媒体域名>/dms/…/6.0.mp4", "preview": "https://<媒体域名>/dms/…/6.0.jpg"}
    "pearnly_dms_manual_customer": {"video": "https://<媒体域名>/dms/…/6.1.mp4", "preview": "https://<媒体域名>/dms/…/6.1.jpg"}
    "pearnly_dms_manual_booking":  {"video": "https://<媒体域名>/dms/…/6.2.mp4", "preview": "https://<媒体域名>/dms/…/6.2.jpg"}

两个值都填**完整 URL**，代码里不写死任何域名。视频托管在 Cloudflare R2 的公开媒体域名上、
由用户本人上传（用户 2026-10 定），不放在生产服务器上。
LINE 视频消息的要求：两个地址都必须是 https；视频 mp4（≤200MB），封面 JPEG（≤1MB）。
没配置或配置不合格 → 那一项回「教程视频暂未上线」，不报错。

**只用回覆（免费），不推播**：免费版每月只有 300 条推播额度。点按钮是同步 webhook，
回覆令牌一定还新鲜；手上没有令牌就不发。看手册**不动会话**：订车做到一半点开视频，
回来接着做。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from pearnly_dms.line import cards
from pearnly_dms.line import out

logger = logging.getLogger(__name__)

CONFIG_PREFIX = "pearnly_dms_manual_"
# (条目码, 编号)：条目码同时是 postback 载荷和配置键后缀，不翻译。
ITEMS = (("setup", "6.0"), ("customer", "6.1"), ("booking", "6.2"))
_CODES = frozenset(code for code, _num in ITEMS)
_MAX_URL = 2000  # LINE 对 originalContentUrl / previewImageUrl 的长度上限


def config_key(code: str) -> str:
    return CONFIG_PREFIX + code


# 文案按收件人语言现取；写成显式常量引用，`sample-verify-dms-cards` 的防漏键守卫才扫得到。
_TITLES = {
    "setup": lambda: cards.TXT_MANUAL_SETUP,
    "customer": lambda: cards.TXT_MANUAL_CUSTOMER,
    "booking": lambda: cards.TXT_MANUAL_BOOKING,
}


def title(code: str) -> str:
    return _TITLES[code]()


# 快捷按钮上的短标题（LINE 快捷按钮标签最多 20 字符；英文、泰文原标题加编号会超长，另立按钮词条）
_QUICK = {"setup": "dms.manual.quick.setup", "customer": "dms.manual.quick.customer",
          "booking": "dms.manual.quick.booking"}


def quick_label(code: str, num: str, lang: Optional[str] = None) -> str:
    from pearnly_dms import i18n

    return f"{num} {i18n.t(_QUICK[code], lang=lang or i18n.line_lang())}"


def manual_card() -> Dict[str, Any]:
    """子菜单：一句提示 + LINE 底部快捷按钮「6.x 短标题」（用户 2026-10-03 由卡片改为快捷按钮）。

    按钮标签用按钮词条（≤ 20 字符），点按后聊天里显示的文字（displayText）是「编号 完整标题」。"""
    items = [{"type": "action",
              "action": {"type": "postback", "label": quick_label(code, num),
                         "data": cards._data(cards.ACT_MANUAL_VIDEO, item=code),
                         "displayText": f"{num} {title(code)}"}}
             for code, num in ITEMS]
    return {"type": "text", "text": cards.TXT_MANUAL_PICK, "quickReply": {"items": items}}


def _https(url: str) -> bool:
    return url.startswith("https://") and len(url) <= _MAX_URL and not any(ch.isspace() for ch in url)


def video_message(code: str) -> Optional[Dict[str, Any]]:
    """该条目的 LINE 视频消息；没配置 / 配置不合格就是 None。"""
    import frappe

    if code not in _CODES:
        return None
    raw = frappe.conf.get(config_key(code))
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    if not isinstance(raw, dict):
        raw = {}
    video = str(raw.get("video") or "").strip()
    preview = str(raw.get("preview") or "").strip()
    if not (_https(video) and _https(preview)):
        # 配了却不合格：照常回"暂未上线"，但留一条日志给运维。
        logger.warning("[dms manual] %s 配置不合格：需要 https 的 video 与 preview", config_key(code))
        return None
    return {"type": "video", "originalContentUrl": video, "previewImageUrl": preview}


def open_manual(reply_token: str) -> None:
    out.reply(reply_token, manual_card())


def play(reply_token: str, code: str) -> None:
    """点 6.x：回覆视频；没上线就回一句明确的话。两条路都只用回覆令牌。"""
    message = video_message(code)
    if message is None:
        out.reply_text(reply_token, cards.TXT_MANUAL_NOT_READY)
        return
    out.reply(reply_token, message)
