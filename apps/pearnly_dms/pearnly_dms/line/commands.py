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

# 其他语言的同一组命令（用户 2026-09-27：LINE 跟随手机语言；提示里写的就是这些词）。
# 一律**整句相同**才算，不做前缀匹配 —— 逐问里用户会打姓名、备注，前缀太宽会误判。
RESET_WORDS = frozenset({RESET_WORD, "重新开始", "やり直し", "restart", "start over"})
MENU_WORDS = frozenset({"menu", "菜单", "選單", "メニュー"})
GREETING_WORDS = frozenset({"你好", "您好", "こんにちは", "hello", "hi"})


def classify(text: str) -> Optional[str]:
    """文本 → 全局命令；没命中返回 None。"""
    stripped = (text or "").strip()
    folded = stripped.casefold()
    if stripped == RESET_WORD or folded in RESET_WORDS:
        return CMD_RESET
    if folded in MENU_WORDS:
        return CMD_MENU
    if folded in GREETING_WORDS:
        return CMD_GREETING
    if len(stripped) <= _MENU_MAX_LEN and stripped.startswith(_MENU_PREFIX):
        return CMD_MENU
    if stripped.startswith(_GREETING_PREFIX):
        return CMD_GREETING
    return None
