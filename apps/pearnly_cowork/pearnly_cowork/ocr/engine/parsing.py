"""Actual Vertex JSON grammar from ec2e5c957e6b (all invoice stages)."""
import json
import re
from typing import Optional

def _first_json_object(s: str) -> str:
    """抠出首个括号平衡的 {...} 子串(字符串内的括号/转义不计);找不到抛 JSONDecodeError。"""
    start = s.find('{')
    if start < 0:
        raise json.JSONDecodeError('no JSON object found', s, 0)
    (depth, in_str, esc) = (0, False, False)
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    raise json.JSONDecodeError('unbalanced JSON object', s, start)

def _fold_stacked(items: list, stacked: Optional[str]):
    """模型把一张图 / 一页上的几张票写成对象数组（2026-09-28 压测：两到四张油票的照片每次都这样回）：
    第一张作主票，其余照提示词本来的约定放进 ``stacked``（嵌套的也摊平）；没给 ``stacked`` 只收单个对象。"""
    if not items or not all(isinstance(item, dict) for item in items):
        return items
    if len(items) == 1:
        return items[0]
    if not stacked:
        return items
    first = dict(items[0])
    extras = list(first.get(stacked) or [])
    for item in items[1:]:
        item = dict(item)
        nested = item.pop(stacked, None) or []
        extras.append(item)
        extras.extend(nested if isinstance(nested, list) else [])
    first[stacked] = extras
    return first

def _parse_json(text: str, *, stacked: Optional[str] = None) -> dict:
    """Parse Gemini's response as JSON, stripping markdown fences if present.

    With response_mime_type='application/json' Gemini SHOULD not add ```json
    fences. But occasionally it does anyway. Be defensive.

    Raises json.JSONDecodeError on parse failure.

    有意变化（旧线上整批失败）：模型把票写成对象数组时按 ``_fold_stacked`` 收下；不是对象的数组仍按解析失败。
    """
    s = text.strip()
    if s.startswith('```'):
        first_nl = s.find('\n')
        if first_nl > 0:
            s = s[first_nl + 1:]
        else:
            s = s[3:]
        if s.rstrip().endswith('```'):
            s = s.rstrip()[:-3].rstrip()
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        obj = json.loads(_first_json_object(s))
    if isinstance(obj, list):
        obj = _fold_stacked(obj, stacked)
    if not isinstance(obj, dict):
        raise json.JSONDecodeError(f'expected JSON object, got {type(obj).__name__}', s, 0)
    return obj
