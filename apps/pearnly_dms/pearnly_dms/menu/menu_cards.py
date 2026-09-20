# -*- coding: utf-8 -*-
"""DMS LINE 菜单卡：入口行卡（标题区 + 整行可点的行卡）。

照搬老产品 `services/line_dms/menu_cards.py`（线上基准 `501589f0`，布局与配色未改）。
文案与动作名仍以 `dms_cards` 为单一来源，这里只管排版。

⚠️ **图标地址换了来源**。老产品把图标写死在自己的域名上
（`https://pearnly.com/static/dms/line-icons`）—— 那在"一个域名一个产品"的时代没问题；
新平台**一家租户一个域名**，写死就等于所有租户的菜单卡都去别人的站点取图。
所以改成**本站点自己的地址**（`frappe.utils.get_url()` + `/assets/pearnly_dms/...`），
图片随 app 走（`public/images/menu/`）。

第 3/4 项与底部菜单共用 links.account_action，指向本站的身份验证、门户交接及
本人 DMS 凭据维护入口。未配置 LIFF 时不显示无法使用的入口。
"""

from __future__ import annotations

from typing import Any, Dict
from pearnly_dms.menu.links import account_action

from pearnly_dms.line.cards import (
    ACT_MENU_BOOKING,
    ACT_MENU_CUSTOMER,
    ACT_MENU_QUERY,
    TXT_MENU_D1,
    TXT_MENU_D2,
    TXT_MENU_D3,
    TXT_MENU_D4,
    TXT_MENU_D5,
    TXT_MENU_HINT,
    TXT_MENU_ITEM1,
    TXT_MENU_ITEM2,
    TXT_MENU_ITEM3,
    TXT_MENU_ITEM4,
    TXT_MENU_ITEM5,
    TXT_MENU_PICK,
    TXT_MENU_PICK_SUB,
    TXT_MENU_TITLE,
    _data,
)

# 行卡配色主题（accent 编号/箭头 · soft 图标圆底 · border 卡边）
THEME_BLUE = {"accent": "#2f6bff", "soft": "#eaf0ff", "border": "#dfe7ff"}
THEME_PINK = {"accent": "#f25c6e", "soft": "#fdecef", "border": "#f9d9de"}
THEME_PURPLE = {"accent": "#7656d6", "soft": "#f0ebff", "border": "#e5dcff"}
THEME_GREEN = {"accent": "#198c6a", "soft": "#e8f7f1", "border": "#d3eee4"}
THEME_ORANGE = {"accent": "#c26a16", "soft": "#fff2e3", "border": "#f5dfc7"}


def icon_base() -> str:
    """本站点的图标根地址（随 app 的 `public/images/menu/` 走）。"""
    import frappe

    return frappe.utils.get_url().rstrip("/") + "/assets/pearnly_dms/images/menu"


def menu_icon_disc(icon: str, soft: str, size: str, img: str) -> Dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "width": size,
        "height": size,
        "cornerRadius": "999px",
        "backgroundColor": soft,
        "justifyContent": "center",
        "alignItems": "center",
        "contents": [
            {"type": "image", "url": f"{icon_base()}/{icon}.png?v=1", "size": img}
        ],
    }


def menu_item(
    num: str,
    icon: str,
    theme: Dict[str, str],
    title: str,
    desc: str,
    action: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """整行可点的菜单项：编号圆徽、图标、标题说明和箭头。`action=None` = 不可点（不画箭头）。"""
    accent = theme["accent"]
    row = {
        "type": "box",
        "layout": "horizontal",
        "spacing": "md",
        "margin": "md",
        "cornerRadius": "14px",
        "borderColor": theme["border"],
        "borderWidth": "1px",
        "paddingAll": "14px",
        "alignItems": "center",
        "contents": [
            menu_icon_disc(icon, theme["soft"], "46px", "26px"),
            {
                "type": "text",
                "text": num,
                "size": "xxl",
                "weight": "bold",
                "color": accent,
                "flex": 0,
                "gravity": "center",
            },
            {
                "type": "box",
                "layout": "vertical",
                "flex": 1,
                "contents": [
                    {
                        "type": "text",
                        "text": title,
                        "size": "sm",
                        "weight": "bold",
                        "color": "#1b1b2b",
                        "wrap": True,
                    },
                    {
                        "type": "text",
                        "text": desc,
                        "size": "xxs",
                        "color": "#8a8a8a",
                        "wrap": True,
                        "margin": "xs",
                    },
                ],
            },
        ],
    }
    if action:
        row["action"] = action
        row["contents"].append(
            {
                "type": "text",
                "text": "›",
                "size": "xl",
                "color": accent,
                "flex": 0,
                "gravity": "center",
            }
        )
    return row


def menu_card(
    can_query: bool = False,
) -> Dict[str, Any]:
    """入口菜单：标题区 + 整行可点的行卡（1 建档 / 2 订车 / 5 查资料）。

    `can_query` → 是否显示第 5 项"搜索资料"（判据在后端，这里只管画不画）。
    """
    head = {
        "type": "box",
        "layout": "horizontal",
        "spacing": "md",
        "alignItems": "center",
        "contents": [
            menu_icon_disc("menu-head", "#eaf0ff", "40px", "22px"),
            {
                "type": "box",
                "layout": "vertical",
                "flex": 1,
                "contents": [
                    {
                        "type": "text",
                        "text": TXT_MENU_PICK,
                        "size": "sm",
                        "weight": "bold",
                        "wrap": True,
                    },
                    {
                        "type": "text",
                        "text": TXT_MENU_PICK_SUB,
                        "size": "xxs",
                        "color": "#8a8a8a",
                        "wrap": True,
                        "margin": "xs",
                    },
                ],
            },
        ],
    }
    contents = [
        head,
        {"type": "separator", "margin": "lg", "color": "#eeeef4"},
        menu_item(
            "1",
            "menu-1",
            THEME_BLUE,
            TXT_MENU_ITEM1,
            TXT_MENU_D1,
            {"type": "postback", "data": _data(ACT_MENU_CUSTOMER)},
        ),
        menu_item(
            "2",
            "menu-2",
            THEME_PINK,
            TXT_MENU_ITEM2,
            TXT_MENU_D2,
            {"type": "postback", "data": _data(ACT_MENU_BOOKING)},
        ),
    ]
    for num, kind, theme, title, desc in (
        ("3", "portal", THEME_PURPLE, TXT_MENU_ITEM3, TXT_MENU_D3),
        ("4", "credentials", THEME_GREEN, TXT_MENU_ITEM4, TXT_MENU_D4),
    ):
        action = account_action(kind, title)
        if action:
            contents.append(menu_item(num, "menu-" + num, theme, title, desc, action))
    if can_query:
        contents.append(
            menu_item(
                "5",
                "menu-5",
                THEME_ORANGE,
                TXT_MENU_ITEM5,
                TXT_MENU_D5,
                {"type": "postback", "data": _data(ACT_MENU_QUERY)},
            )
        )
    contents.append(
        {
            "type": "text",
            "text": TXT_MENU_HINT,
            "size": "xxs",
            "color": "#aaaaaa",
            "align": "center",
            "wrap": True,
            "margin": "lg",
        }
    )
    body = {
        "type": "box",
        "layout": "vertical",
        "paddingAll": "16px",
        "contents": contents,
    }
    return {"type": "flex", "altText": TXT_MENU_TITLE, "contents": {"type": "bubble", "body": body}}
