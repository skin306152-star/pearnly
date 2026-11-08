# -*- coding: utf-8 -*-
"""这台电脑的「Express 位置」（用户 10-24）：只读用户添加的这些盘 / 文件夹下的 Express 年度文件夹和账套。

用户原话：「我选择了c盘，就只扫描c盘的exp，别搞出s盘，或者可以添加其它文件盘，不止可以选择一个目标，可以选择多个目标，
某些事务所或者公司一个exp主体或者放在多个盘」。所以：

- 位置是整台电脑一份（全局设置 ``express_places``），Cowork 与 ERP 成员共用；设置里添加 / 移除。
- 心跳只扫、只报位置下面的年度；新建的年度只在位置下面找（不再整台电脑、局域网去搜）。
- 位置可以是盘符、上层文件夹，也可以就是某个年度文件夹（那就只读这一年）。要覆盖以后新建的年度，就选上层文件夹 / 盘。
- 清单按用户选的原样存（:func:`add`）：一个位置在另一个下面也照样单列（11-08 生产 KORN：位置 P:\\ 下再选 P:\\69EXP 被合并悄悄吞掉，
  只剩一个位置又不能删，怎么点都改不成只读这一年）。扫描、上报按并集（:func:`within`）；找年度时的去重（:func:`merge`）只在内部用。
  只有第一次装自动找的、1.12.0 升级换算的（不是人选的）才取年度文件夹所在的上一级（:func:`place_of`），好让以后新年度自动进来。

**每个位置给哪种成员读**（11-07，共用电脑隔离评估审定的做法 B）：一台电脑可以同时连事务所的 Cowork 和某个客户的 ERP
（ADR-025，每种通道一个站点）。位置整机一份时，ERP 成员会把位置下事务所全部客户的账套报给这个客户的 ERP（ERP 照着自动建公司、
同步整套主档）。所以每个位置记一份「给哪几种成员」（全局设置 ``place_channels``，``[(位置, (通道, ...)), ...]``）：

- 没登记的位置按这台电脑此刻有哪些 Express 成员补上（:func:`settle_channels`）：有 Cowork 成员时只给 Cowork（新加的、
  升级时已有的都一样，ERP 要人在设置里勾）；没有 Cowork 成员（只有 ERP）时两种都给（以后再连 Cowork 也照样读得到）；
  一个 Express 成员都没有时先不补，等第一个成员连上再按它补。
- 心跳只扫、只报给这个成员的那些位置（:func:`places_for`）；给它的一个都没有就什么都不报（不是「没有位置清单 = 全报」）。
- 年度文件夹、授权数据根照旧整台电脑一份（找新年度、补数据根不按成员分），只在扫描、上报这一步按成员筛。

本模块只做路径判断，不读盘、不碰界面与成员记录，谁都可以引用（profile_store 升级换算也用）。
"""

from __future__ import annotations

import ntpath
import re
from typing import Dict, Iterable, List, Tuple

from . import channel

# 位置能给的成员种类（channel.py 的通道名），设置页按这个顺序摆两个勾
CHANNELS = (channel.COWORK, channel.CORE)


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


def add(places: Iterable[str], picked: str) -> List[str]:
    """位置清单加上选的这一个：按选的原样存，同一个文件夹（不同写法）才算重复；在别的位置里面、外面都照样单列。"""
    kept: List[str] = []
    for place in [*(places or ()), picked]:
        text = str(place or "").strip()
        if text and not any(same_root(text, known) for known in kept):
            kept.append(text)
    return kept


def merge(places: Iterable[str]) -> List[str]:
    """找年度、自动换算用的去重：一个位置在另一个下面的只留外面那个（C:\\ 下面就不再单找 C:\\PearnlyExpress）。顺序按先来后到。
    只在内部用，不拿它改用户选的清单（:func:`add`）。"""
    kept: List[str] = []
    for place in places or ():
        text = str(place or "").strip()
        if not text or any(under(text, known) for known in kept):
            continue
        kept = [known for known in kept if not under(known, text)] + [text]
    return kept


def channels_map(pairs) -> Dict[str, Tuple[str, ...]]:
    """``place_channels`` → ``{规范化的位置: (通道, ...)}``；认不出的通道名丢掉，写法不对的条目跳过。"""
    found: Dict[str, Tuple[str, ...]] = {}
    for item in pairs or ():
        if not isinstance(item, (list, tuple)) or len(item) != 2 or not isinstance(item[1], (list, tuple)):
            continue
        key = norm_root(item[0])
        if key:
            found[key] = tuple(name for name in CHANNELS if name in item[1])
    return found


def normalize_channels(pairs) -> tuple:
    """文件里读到的 ``place_channels``（JSON 是列表套列表）→ ``((位置, (通道, ...)), ...)``，可放进不可变的全局设置。"""
    out = []
    for item in pairs or ():
        if isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[1], (list, tuple)):
            place = str(item[0] or "").strip()
            if place:
                out.append((place, tuple(name for name in CHANNELS if name in item[1])))
    return tuple(out)


def default_channels(present: Iterable[str]) -> Tuple[str, ...]:
    """没登记的位置给谁：有 Cowork 成员只给 Cowork；没有（只有 ERP）两种都给；一个 Express 成员都没有回空（先不补）。"""
    present = set(present or ())
    if not present:
        return ()
    return (channel.COWORK,) if channel.COWORK in present else CHANNELS


def settle_channels(places: Iterable[str], pairs, present: Iterable[str]) -> tuple:
    """按当前位置清单整理 ``place_channels``：移除了的位置去掉、已登记的照旧、没登记的按 :func:`default_channels` 补上。"""
    known = channels_map(pairs)
    fallback = default_channels(present)
    out = []
    for place in places or ():
        text = str(place or "").strip()
        key = norm_root(text)
        if not key:
            continue
        if key in known:
            out.append((text, known[key]))
        elif fallback:
            out.append((text, fallback))
    return tuple(out)


def channels_of(place: str, pairs) -> Tuple[str, ...]:
    """这个位置给哪几种成员读；没登记的（还没有成员时加的、测试里直接构造的配置）两种都给，同 11-07 以前。"""
    return channels_map(pairs).get(norm_root(place), CHANNELS)


def places_for(places: Iterable[str], pairs, kind: str) -> List[str]:
    """给 ``kind`` 这种成员读的位置（保持原顺序）。"""
    return [place for place in places or () if kind in channels_of(place, pairs)]


def shows_channels(places: Iterable[str], pairs, present: Iterable[str]) -> bool:
    """设置页要不要在每个位置后面摆「Cowork / ERP」两个勾：这台电脑同时有两种 Express 成员，或有哪种成员缺了某个位置
    （例如原来的 Cowork 成员断开了、剩下的 ERP 成员还没勾）。只有一种成员、位置都给它时界面和以前一样。"""
    present = [name for name in CHANNELS if name in set(present or ())]
    if len(present) >= 2:
        return True
    return any(name not in channels_of(place, pairs) for place in places or () for name in present)
