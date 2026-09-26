# -*- coding: utf-8 -*-
"""自动建库存档时,单位(QUCOD)和类别(STKGRP)从账套自己的众数取。

**为什么不跟克隆模板**:模板是「挂众数存货科目的那一行」,它的单位纯属那一行商品是什么货。
真账套取证(2026-07-25):70EXP 162 个 STKTYP=0 的 QUCOD 是 พค45 / กน30 / กง25 / ถง22 / ลต20,
模板行给的是 กง —— 跟票面单位、跟这套账的习惯都没关系,只是 DBF 行序的产物。故票面给了单位
就按票面的(过 unit_code 解析成本账套的合法码),票面没给才退到账套众数,并让调用方标出来
「这是猜的」,会计能去 Express 核一眼。

**STKGRP 反过来**:同一份取证里 70EXP 的 05 占 144/162、69SINCER 的 FG 占 32/37,众数就是这套账
「货归哪一类」的事实,自动填不必惊动人。VATCOD 不在这里管 —— 空才是常态(70EXP 空 101 / 有 61)。

账套里一个 STKTYP=0 都没有(新客户第一次建库存品)→ 众数无从取,两项一律返空:拿服务品档的
单位/类别冒充库存品的默认,就是从没有事实的地方编事实。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Iterable, List, NamedTuple

import dbf

from . import dbf_schema as S
from . import stock_lookup
from . import unit_code


class StockDefaults(NamedTuple):
    """账套既有库存品给出的建档默认值(取不到的项为空串)。"""

    qucod: str
    stkgrp: str


def mode(values: Iterable[str]) -> str:
    """出现次数最多的非空值;并列时取先出现的那个(同一份数据每次跑结果一样)。"""
    seen = [v for v in values if v]
    if not seen:
        return ""
    counts = Counter(seen)
    order = {}
    for i, v in enumerate(seen):
        order.setdefault(v, i)
    return min(counts, key=lambda v: (-counts[v], order[v]))


def read(account_dir) -> StockDefaults:
    """账套里既有库存品(STKTYP=0 活行)的单位/类别众数。一条都没有 → 两项都空。

    单位众数还要过 unit_code.resolve:库里存着的码理应合法,但账套换过 Express 版本、
    ISTAB 被人删过码的情况真实存在,写一个该账套认不出的单位比留空更糟。
    """
    qucods, stkgrps = _stock_columns(account_dir)
    return StockDefaults(
        qucod=unit_code.resolve(account_dir, mode(qucods)),
        stkgrp=mode(stkgrps),
    )


def _stock_columns(account_dir) -> tuple:
    """一次扫表取回 (全部 QUCOD, 全部 STKGRP) —— 两项各扫一遍就是白读一遍 STMAS。"""
    qucods: List[str] = []
    stkgrps: List[str] = []
    t = dbf.Table(str(Path(account_dir) / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        for r in S.live_rows(t):
            if str(_gv(r, "STKTYP")) != stock_lookup.STOCK_TYPE:
                continue
            qucods.append(_gv(r, "QUCOD"))
            stkgrps.append(_gv(r, "STKGRP"))
    finally:
        t.close()
    return qucods, stkgrps


def _gv(rec, field: str, default: str = "") -> str:
    try:
        v = rec[field]
    except Exception:
        return default
    return v.strip() if isinstance(v, str) else v
