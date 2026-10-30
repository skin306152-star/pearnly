"""扫到的码怎么对上商品的条码（主会话 10-30：码制一次覆盖全，GS1 按类解析）。纯规则，不连库、不依赖 Frappe。

- 读码器给的原文可能带码制标识（原生读 GS1-128 前面有 ``]C1``、Data Matrix 有 ``]d2``）和 FNC1（读成 GS，``\\x1d``）：
  :func:`clean` 去掉这些，留下正文。
- GS1 码（Data Matrix、DataBar、GS1-128）的正文是应用标识串：``01`` + 14 位 GTIN，后面可能跟 ``17`` 有效期、``10`` 批号、
  ``21`` 序列号……（定长的直接接着，变长的以 GS 隔开或到结尾）。:func:`parse` 解出来；人手输的 ``(01)…(21)…`` 写法也认。
  我们只用 GTIN（按商品条码认）和序列号（给按台管理认台）；批号、有效期不用。
- GTIN 的几种长度是同一个号：14 位前面补 0 的就是 13 位 EAN，13 位以 0 开头的就是 12 位 UPC-A，UPC-E（8 位）展开就是 UPC-A。
  :func:`gtin_forms` 给出一个号的全部写法，认码时任一种对上商品的条码就算（WASM 把 UPC-A 读成带 0 的 13 位、UPC-E 读成展开的 13 位，
  商品上存的可能是短的写法）。只认校验位对的数字串，普通编码不会被当成 GTIN。
"""
from __future__ import annotations

import re

GS = "\x1d"
# 码制标识（ISO/IEC 15424）：`]` + 码制字母 + 一位修饰符，例如 ]C1（GS1-128）、]d2（GS1 Data Matrix）、]e0（DataBar）、]Q3（GS1 QR）
SYMBOLOGY_ID = re.compile(r"^\][A-Za-z][0-9A-Za-z]")
# 定长的应用标识：前缀 → (标识位数, 数据位数)。没列出的按变长（读到 GS 或结尾）
FIXED = {"00": (2, 18), "01": (2, 14), "02": (2, 14), "03": (2, 14), "11": (2, 6), "12": (2, 6), "13": (2, 6), "15": (2, 6),
         "16": (2, 6), "17": (2, 6), "20": (2, 2), "31": (4, 6), "32": (4, 6), "33": (4, 6), "34": (4, 6), "35": (4, 6),
         "36": (4, 6), "41": (3, 13), "402": (3, 17)}
# 变长应用标识的位数（数据最长 20 / 30 / 90，按 GS 或结尾截，不靠最长值）
VARIABLE = {"10": 2, "21": 2, "22": 2, "30": 2, "37": 2, "90": 2, "91": 2, "92": 2, "93": 2, "94": 2, "95": 2, "96": 2, "97": 2,
            "98": 2, "99": 2, "235": 3, "240": 3, "241": 3, "242": 3, "250": 3, "251": 3, "253": 3, "254": 3, "400": 3,
            "401": 3, "403": 3, "420": 3, "421": 3, "422": 3, "7003": 4, "8003": 4, "8004": 4, "8020": 4}
HRI = re.compile(r"\((\d{2,4})\)([^(]*)")


def clean(raw) -> str:
    """去掉码制标识、开头的 FNC1 与首尾空白；正文中间的 GS（变长字段的分隔）留着。"""
    text = str(raw or "").strip()
    text = SYMBOLOGY_ID.sub("", text)
    return text.lstrip(GS).strip()


def check_digit_ok(digits: str) -> bool:
    if not digits.isdigit() or len(digits) not in (8, 12, 13, 14):
        return False
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def _ai_at(text: str, at: int) -> tuple[str, int, int | None] | None:
    """``text[at:]`` 开头的应用标识：(标识, 标识位数, 定长数据位数 / 变长 None)。认不出回 None。"""
    for size in (4, 3, 2):
        prefix = text[at:at + size]
        if prefix in VARIABLE and VARIABLE[prefix] == size:
            return prefix, size, None
    for prefix, (size, length) in FIXED.items():
        if text.startswith(prefix, at):
            return text[at:at + size], size, length
    return None


def parse(raw) -> dict | None:
    """GS1 应用标识串 → ``{"01": GTIN-14, "21": 序列号, ...}``；不是 GS1 串（或没有 01）回 None。

    判成 GS1 的只有两种：括号写法 ``(01)…``；以 ``01`` + 校验位对的 14 位开头的正文（读码器去掉 FNC1 后都是这样）。
    别的码（普通编码、URL、13 位 EAN）不会被当成 GS1。"""
    text = clean(raw)
    if not text:
        return None
    if text.startswith("("):
        fields = {ai: value.strip() for ai, value in HRI.findall(text)}
    else:
        if not (text.startswith("01") and len(text) >= 16 and check_digit_ok(text[2:16])):
            return None
        fields, at = {}, 0
        while at < len(text):
            if text[at] == GS:
                at += 1
                continue
            found = _ai_at(text, at)
            if not found:
                break  # 认不出的标识：前面已经解出来的（GTIN 在最前面）照用，后面的不猜
            ai, size, length = found
            start = at + size
            if length is not None:
                fields[ai] = text[start:start + length]
                at = start + length
            else:
                end = text.find(GS, start)
                end = len(text) if end < 0 else end
                fields[ai] = text[start:end]
                at = end
    gtin = fields.get("01", "")
    if not (len(gtin) == 14 and check_digit_ok(gtin)):
        return None
    return fields


def _upce_to_upca(upce: str) -> str | None:
    """8 位 UPC-E（数制 0 / 1）→ 12 位 UPC-A；不是合法 UPC-E 回 None。"""
    if len(upce) != 8 or not upce.isdigit() or upce[0] not in "01":
        return None
    system, d, check = upce[0], upce[1:7], upce[7]
    last = d[5]
    if last in "012":
        body = d[0:2] + last + "0000" + d[2:5]
    elif last == "3":
        body = d[0:3] + "00000" + d[3:5]
    elif last == "4":
        body = d[0:4] + "00000" + d[4]
    else:
        body = d[0:5] + "0000" + last
    upca = system + body + check
    return upca if check_digit_ok(upca) else None


def _upca_to_upce(upca: str) -> str | None:
    """12 位 UPC-A → 能压缩时的 8 位 UPC-E（展开回去要一模一样才算）。"""
    if len(upca) != 12 or upca[0] not in "01":
        return None
    system, m, check = upca[0], upca[1:11], upca[11]
    for candidate in (m[0:2] + m[7:10] + m[2], m[0:3] + m[8:10] + "3", m[0:4] + m[9] + "4", m[0:5] + m[9]):
        if len(candidate) == 6:
            upce = system + candidate + check
            if _upce_to_upca(upce) == upca:
                return upce
    return None


def gtin_forms(value) -> list[str]:
    """一个 GTIN 的全部写法（14 / 13 / 12 位、UPC-E 与 UPC-A 互转）；不是校验位对的 8 / 12 / 13 / 14 位数字回 []。"""
    digits = str(value or "").strip()
    forms = []
    if len(digits) == 8 and digits.isdigit():
        expanded = _upce_to_upca(digits)
        if expanded:
            forms += [digits, *gtin_forms(expanded)]
        if check_digit_ok(digits):  # 同一个 8 位也可能是 EAN-8
            forms += [digits, digits.zfill(14)]
        return list(dict.fromkeys(forms))
    if not check_digit_ok(digits) or len(digits) == 8:
        return []
    full = digits.zfill(14)
    forms = [full]
    for length in (13, 12):
        if full[:14 - length] == "0" * (14 - length):
            forms.append(full[14 - length:])
    upce = _upca_to_upce(full[2:]) if full[:2] == "00" else None
    if upce:
        forms.append(upce)
    return list(dict.fromkeys(forms))


def lookup(raw) -> dict:
    """认码时要查的值：``exact`` 按原文对所有码（原文与去掉码制标识后的正文）；``gtin`` 按条码对（GS1 的 GTIN，或原文本身就是 GTIN
    的各种长度写法）；``serial`` GS1 的序列号（按台管理认台用）。"""
    text = str(raw or "").strip()
    body = clean(text)
    fields = parse(text) or {}
    gtin = gtin_forms(fields["01"]) if fields.get("01") else gtin_forms(body)
    exact = list(dict.fromkeys(value for value in (text, body) if value))
    return {"exact": exact, "gtin": [value for value in gtin if value not in exact], "serial": fields.get("21", "")}
