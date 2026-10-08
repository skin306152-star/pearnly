# -*- coding: utf-8 -*-
"""定长列的真实宽度 —— 从 DBF 表头现取,不写死在代码里。

Express 的列宽跟版本/账套走:实测 70EXP 真表 `STMAS.STKDES` 是 C(50),而仓库里的合成表按
C(40) 建。把宽度写成常量就是在赌所有客户的表长一个样;赌输的表现不是报错而是静默错账 ——
写入侧按常量截、查找侧拿截过的名去比库里的原值,永远不等,同名商品被反复建成新档。
"""

from __future__ import annotations

from pathlib import Path

import dbf

from . import dbf_schema as S


def field_width(account_dir, table: str, field: str) -> int:
    """表 `table` 的定长列 `field` 声明宽度(字节)。表/列不存在直接抛 —— 不猜默认值。"""
    t = dbf.Table(str(Path(account_dir) / f"{table}.DBF"), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    t.open(dbf.READ_ONLY)
    try:
        return int(t.field_info(field).length)
    finally:
        t.close()


def fit(value, width: int) -> str:
    """裁到能落进该列的最长前缀(先 strip,再按 cp874 编码后的**字节数**卡)。

    列宽是字节数不是字符数。泰文在 cp874 下一字一字节,但混进非 cp874 字符时 dbf 会做替换,
    字符数就不再等于字节数,故以编码后长度兜底,避免写入溢出。
    """
    s = str(value or "").strip()[:width]
    while s and len(s.encode(S.CODEPAGE, "replace")) > width:
        s = s[:-1]
    return s
