"""模型给的 Express / MR.ERP 编码换回主档原样编码（纯契约，不依赖框架和 IO；11-15 真机：收款客户编码 ``นาย\\xa0ทดสอบ``）。

为什么要有：CC（对话模型）复述编码时空白会变（不换行空格变普通空格，11-15 真模型实验 3/3），而库的排序规则
``utf8mb4_unicode_ci`` 把不换行空格与空格当成相等，服务端按等号查得到、却把模型给的串原样存进草稿；CC 桌面版写 Express
按原样认编码，就拒（``CUSTOMER_NOT_IN_ARMAS``）。Express 里只差空白的两个编码是两件不同的档（`express.masters._first`），
所以不能在写入端放宽，只能在收入参时换回主档原样的那一个。

用法（各业务的主档查法不同，候选由调用方查，这里只判）：

1. :func:`like_pattern` 给一个 ``like`` 条件，把空白随便变过的候选都取回来（库的排序规则再把全角、大小写一并放宽）；
2. :func:`resolve` 在候选里按原样逐字找；找不到按 :func:`key` 等价找，唯一一个就回它的原样编码；几个或没有交回调用方
   （回到「问用户选」，不让模型自己判「不存在」）。
"""
import re
import unicodedata

EXACT, LOOSE, SEVERAL, NONE = "exact", "loose", "several", "none"

# isspace() 不认、但复述时会丢或会多出来的不可见字符
INVISIBLE = ("​", "‌", "⁠", "﻿")
_SPACES = re.compile(r"\s+")


def key(code) -> str:
    """比较用的形状：NFKC（全角→半角）、去掉不可见字符、各种空白（不换行、全角、制表）并成一个空格、去首尾。不折大小写：
    只差大小写的两件档按两件算（几个就问人）。"""
    text = unicodedata.normalize("NFKC", str(code or ""))
    for char in INVISIBLE:
        text = text.replace(char, "")
    return _SPACES.sub(" ", text).strip()


def like_pattern(code) -> str:
    """取候选用的 ``like`` 串：按 :func:`key` 后的各段之间放 ``%``（空白怎么变都取得回）；``%`` ``_`` ``\\`` 转义。空串回 ``""``。"""
    parts = [re.sub(r"([\\%_])", r"\\\1", part) for part in key(code).split(" ") if part]
    return "%".join(parts)


def resolve(given, candidates) -> dict:
    """``candidates``：主档里的原样编码（可重复，按出现先后）。回 ``{"status", "code", "matches"}``：

    - ``exact``：有逐字相同的（去首尾空白后也算，服务端一向 strip 入参）→ ``code`` 是它；
    - ``loose``：没有逐字相同的，按 :func:`key` 等价的只有一个 → ``code`` 是主档里的原样编码；
    - ``several``：等价的有几个（只差空白 / 全角的几件档）→ ``matches`` 交人选；
    - ``none``：一个都不像 → ``code`` 为空。
    """
    text = str(given or "")
    seen = list(dict.fromkeys(str(item) for item in candidates or () if item is not None and str(item) != ""))
    for candidate in seen:
        if candidate == text or candidate == text.strip():
            return {"status": EXACT, "code": candidate, "matches": [candidate]}
    wanted = key(text)
    same = [candidate for candidate in seen if wanted and key(candidate) == wanted]
    if len(same) == 1:
        return {"status": LOOSE, "code": same[0], "matches": same}
    if same:
        return {"status": SEVERAL, "code": "", "matches": same}
    return {"status": NONE, "code": "", "matches": []}


def choose(given, options) -> str:
    """候选已在手时的简写：认得出（逐字或唯一等价）回主档原样，认不出回去首尾空白的原串（调用方照旧按「不在候选里」拒）。"""
    found = resolve(given, options)
    return found["code"] if found["status"] in (EXACT, LOOSE) else str(given or "").strip()


__all__ = ["EXACT", "LOOSE", "SEVERAL", "NONE", "key", "like_pattern", "resolve", "choose"]
