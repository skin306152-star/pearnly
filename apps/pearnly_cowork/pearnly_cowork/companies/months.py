"""公司文件夹下那个月的子文件夹（ADR-020），纯函数，不连库、不向电脑发请求：只按电脑已上报的文件夹名认。

`month_folder(folders, base, year, month)`：`folders` 是电脑为这个根目录上报的相对路径（反斜杠分隔，最多 3 层），
`base` 是公司文件夹（根目录下的相对路径，"" = 根目录本身），`year` / `month` 是公历年月。

- 候选：`base` 下 1–2 层。`base` 自身已到上报深度时下面没报，直接回空。
- 逐段认（不分大小写，去掉空白与 `._-/`）：年 + 月数字（`2569-09`、`2026_09`、`202609`、`256909`、`09-2569`、
  `2569.9`）；年 + 月词（`ก.ย. 69`、`กันยายน 2569`、`Sep 2026`、`2026年9月`）；只有月（`09`、`9`、`ก.ย.`、`September`、
  `9月`）。4 位年 ≥ 2400 按佛历减 543；2 位年按佛历 25xx 补全。两位数字只有 1–12 才算月；单独的 4 位数只算年。
- 打分：一段里年、月都对上 3 分；上一层是同年、本层是月 2 分；只有月、且 `base` 下没有任何带年的段 1 分；其余 0 分。
- 取唯一的最高分；最高分并列时不选（`folder = None`），`candidates` 最多给 5 个，交给 CC 问一次；全是 0 分回空。
"""
from __future__ import annotations

import re
from typing import Iterable

from pearnly_cowork.contracts.history import MONTH_WORDS

REPORTED_DEPTH = 3  # 电脑只报到 3 层下级文件夹（同 `folder_intake.contracts.REPORTED_DEPTH`）
CANDIDATES_MAX = 5
_YEAR_MARK, _MONTH_MARK = chr(0x5E74), chr(0x6708)  # 文件夹名的中文写法「2026年9月」「9月」（不是界面文案）
_CJK_MONTH = re.compile(r"^(?:(\d{4})" + _YEAR_MARK + r")?(\d{1,2})" + _MONTH_MARK + "$")
_SEPARATORS = re.compile(r"[\s._\-/]+")


def _year(text: str) -> int | None:
    """4 位：≥ 2400 是佛历；2 位：佛历 25xx。合理范围外的不算年。"""
    if len(text) == 2:
        value = 2500 + int(text) - 543
    elif len(text) == 4:
        value = int(text)
        value = value - 543 if value >= 2400 else value
    else:
        return None
    return value if 1990 <= value <= 2200 else None


def _month(text: str) -> int | None:
    if not text or len(text) > 2:
        return None
    value = int(text)
    return value if 1 <= value <= 12 else None


def _word(text: str) -> int | None:
    for candidate in (text, text.rstrip("."), text.replace(".", "")):
        if candidate in MONTH_WORDS:
            return MONTH_WORDS[candidate]
    return None


def segment(text: str) -> tuple[int | None, int | None]:
    """一段文件夹名 → (公历年, 月)；认不出的部分为 None。"""
    raw = str(text or "").strip().casefold()
    compact = _SEPARATORS.sub("", raw)
    if not compact:
        return None, None
    cjk = _CJK_MONTH.match(compact)
    if cjk:
        year = _year(cjk.group(1)) if cjk.group(1) else None
        month = _month(cjk.group(2))
        return (year, month) if month else (None, None)
    numbers = re.findall(r"\d+", raw)
    word = re.sub(r"[\d\s._\-/]+", "", raw)  # 泰文缩写的点也去掉（MONTH_WORDS 里有不带点的写法）
    if word:
        month = _word(word)
        if not month or len(numbers) > 1:
            return None, None
        if not numbers:
            return None, month
        year = _year(numbers[0])
        return (year, month) if year else (None, None)
    if len(numbers) == 2:
        first, second = numbers
        if len(first) == 4 and _year(first) and _month(second):
            return _year(first), _month(second)
        if len(second) == 4 and _year(second) and _month(first):
            return _year(second), _month(first)
        return None, None
    if len(numbers) != 1:
        return None, None
    only = numbers[0]
    if len(only) == 6:
        if _year(only[:4]) and _month(only[4:]):
            return _year(only[:4]), _month(only[4:])
        if _month(only[:2]) and _year(only[2:]):
            return _year(only[2:]), _month(only[:2])
        return None, None
    if len(only) == 4:
        return _year(only), None
    if len(only) <= 2:
        return None, _month(only)
    return None, None


def _parts(path: str) -> list:
    return [part for part in str(path or "").split("\\") if part]


def month_folder(folders: Iterable[str] | None, base: str, year: int, month: int) -> dict:
    """→ `{"folder": 唯一选中的相对路径或 None, "candidates": [并列最高分的相对路径，最多 5 个]}`。"""
    empty = {"folder": None, "candidates": []}
    if folders is None or not year or not month:
        return empty
    base_parts = _parts(base)
    if len(base_parts) + 1 > REPORTED_DEPTH:
        return empty
    prefix = [part.casefold() for part in base_parts]
    below = {}
    for path in folders:
        parts = _parts(path)
        if len(parts) <= len(base_parts) or [p.casefold() for p in parts[:len(base_parts)]] != prefix:
            continue
        rest = parts[len(base_parts):]
        if 1 <= len(rest) <= 2:
            below["\\".join(parts)] = rest
    if not below:
        return empty
    any_year = any(segment(part)[0] for rest in below.values() for part in rest)
    parent_of_base = segment(base_parts[-1]) if base_parts else (None, None)
    scores = {}
    for path, rest in below.items():
        found_year, found_month = segment(rest[-1])
        parent = segment(rest[0]) if len(rest) == 2 else parent_of_base
        if found_year and found_month:
            score = 3 if (found_year, found_month) == (year, month) else 0
        elif found_month and parent[0] and not parent[1]:
            score = 2 if (parent[0], found_month) == (year, month) else 0
        elif found_month and not found_year:
            score = 1 if found_month == month and not any_year and not parent[0] else 0
        else:
            score = 0
        if score:
            scores[path] = score
    if not scores:
        return empty
    best = max(scores.values())
    top = sorted((path for path, score in scores.items() if score == best), key=lambda p: (p.casefold(), p))
    if len(top) == 1:
        return {"folder": top[0], "candidates": []}
    return {"folder": None, "candidates": top[:CANDIDATES_MAX]}


__all__ = ["month_folder", "segment"]
