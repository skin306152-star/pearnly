# -*- coding: utf-8 -*-
"""DMS LINE 会话内**全局命令**的词形判定（单一事实源）。

为什么要把词表收到这里：老产品最初把判据散在两个地方，结果出现过一个真机 bug ——
用户正在改姓名时打「เมนู」，菜单词被判成了新姓名写进 id_card。
**词表只留一份，调用方一律引用 `classify`**，不许就地写前缀比较：两处各写一份就是漂移的温床。

纯函数：只看词形，不读会话、不做 IO。
不含解绑词（webhook 层就地处理），也不含「ยกเลิก」（那是编辑态的取消，不是全局词）。
"""

from __future__ import annotations

from typing import Optional

CMD_RESET = "reset"
CMD_MENU = "menu"
CMD_GREETING = "greeting"

RESET_WORD = "เริ่มใหม่"

_MENU_PREFIX = "เมน"
_MENU_MAX_LEN = 6  # เมนู 的常见打错（เมน / เมนB · 真机实拍）都落在这个长度内
_GREETING_PREFIX = "สวัสดี"


def classify(text: str) -> Optional[str]:
    """文本 → 全局命令；没命中返回 None。"""
    stripped = (text or "").strip()
    if stripped == RESET_WORD:
        return CMD_RESET
    if stripped.casefold() == "menu":
        return CMD_MENU
    if len(stripped) <= _MENU_MAX_LEN and stripped.startswith(_MENU_PREFIX):
        return CMD_MENU
    if stripped.startswith(_GREETING_PREFIX):
        return CMD_GREETING
    return None
