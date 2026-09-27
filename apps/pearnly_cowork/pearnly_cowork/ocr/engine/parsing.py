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

def _parse_json(text: str) -> dict:
    """Parse Gemini's response as JSON, stripping markdown fences if present.

    With response_mime_type='application/json' Gemini SHOULD not add ```json
    fences. But occasionally it does anyway. Be defensive.

    Raises json.JSONDecodeError on parse failure.

    有意变化（旧线上整批失败）：经济档模型偶尔把唯一一张票包成 ``[{...}]``，拆开取那一张；
    多个对象的数组仍按解析失败（交重试），不猜哪张是主票。
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
    if isinstance(obj, list) and len(obj) == 1 and isinstance(obj[0], dict):
        obj = obj[0]
    if not isinstance(obj, dict):
        raise json.JSONDecodeError(f'expected JSON object, got {type(obj).__name__}', s, 0)
    return obj
