# -*- coding: utf-8 -*-
"""这台电脑的「Express 位置」（用户 10-24）：只读用户添加的这些盘 / 文件夹下的 Express 年度文件夹和账套。

用户原话：「我选择了c盘，就只扫描c盘的exp，别搞出s盘，或者可以添加其它文件盘，不止可以选择一个目标，可以选择多个目标，
某些事务所或者公司一个exp主体或者放在多个盘」。所以：

- 位置是整台电脑一份（全局设置 ``express_places``），Cowork 与 ERP 成员共用；设置里添加 / 移除。
- 心跳只扫、只报位置下面的年度；新建的年度只在位置下面找（不再整台电脑、局域网去搜）。
- 位置可以是盘符、上层文件夹，也可以就是某个年度文件夹（那就只读这一年）。

本模块只做路径判断，不读盘、不碰界面与成员记录，谁都可以引用（profile_store 升级换算也用）。
"""

from __future__ import annotations

import ntpath
import re
from typing import Iterable, List


def norm_root(path) -> str:
    """同一个文件夹的不同写法算一个：Qt 选择窗口给的是 C:/a\\b 这种正斜杠（2026-10-10 测试机合并后多出一条重复）。"""
    text = str(path or "").strip()
    return ntpath.normpath(text).lower().rstrip("\\/") if text else ""


def same_root(left, right) -> bool:
    return bool(left) and bool(right) and norm_root(left) == norm_root(right)


def under(root, place) -> bool:
    """``root`` 就是 ``place`` 或在它下面。"""
    child, parent = norm_root(root), norm_root(place)
    return bool(child) and bool(parent) and (child == parent or child.startswith(parent.rstrip("\\") + "\\"))


def within(root, places: Iterable[str]) -> bool:
    return any(under(root, place) for place in places or ())


def parent_of(path: str) -> str:
    head = ntpath.dirname(str(path or "").rstrip("\\/"))
    return head + "\\" if re.fullmatch(r"[A-Za-z]:", head) else head


def place_of(root: str) -> str:
    """一个年度文件夹所在的位置：它的上一级；上一级是年份数字目录的（S:\\2569\\EXP69）再上一级（S:\\），
    这样同一位置下别的年份（S:\\2568\\EXP68）和以后新建的年度都在里面。"""
    parent = parent_of(root)
    if re.fullmatch(r"\d{2,4}", ntpath.basename(parent.rstrip("\\")) or ""):
        parent = parent_of(parent) or parent
    return parent or str(root or "").strip()


def merge(places: Iterable[str]) -> List[str]:
    """去重；一个位置在另一个下面的只留外面那个（添加了 C:\\ 就不再单列 C:\\PearnlyExpress）。顺序按先来后到。"""
    kept: List[str] = []
    for place in places or ():
        text = str(place or "").strip()
        if not text or any(under(text, known) for known in kept):
            continue
        kept = [known for known in kept if not under(known, text)] + [text]
    return kept
