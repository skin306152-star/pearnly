# -*- coding: utf-8 -*-
"""OCR 单位串 → 本账套合法的 Express 单位码(ISTAB `TABTYP='20'` 单位表)。

单位码不是"单位名的前两个字"。真账套 70EXP 的 ISTAB 里 `ถัง` 的码是 `ถง`、`ชิ้น` 是 `ชน`;
截前两字得到的 `ถั`/`ชิ` 在该表 39 个合法码里根本不存在,写进 `STMAS.QUCOD` 就是一个
Express 认不出的单位(同一件商品的 `STCRD.TQUCOD` 走映射反而是对的,主档与卡片自相矛盾)。

码表随账套走(同一个 `กระป๋อง`,这套账是 `กง` 而内置映射给的是 `กป`),所以先查客户自己的
ISTAB;查不到才退回内置映射,且内置映射的结果同样要在该账套的合法码里 —— 否则宁可返空,
让调用方保留从同账套模板克隆来的单位(见 product_master 的"字段禁硬编码"原则)。
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

import dbf

from . import dbf_meta
from . import dbf_schema as S

_UNIT_TABTYP = "20"  # ISTAB 里单位码那一段


def resolve(account_dir, unit: str) -> str:
    """单位串 → 本账套的 `QUCOD`。解析不出/不合法一律返 ''(调用方保留模板值,不硬塞)。"""
    key = _norm(unit)
    if not key:
        return ""
    rows = _unit_rows(account_dir)
    if not rows:
        return ""
    for code, short, des in rows:
        if key in (_norm(code), _norm(short), _norm(des)):
            return _accept(account_dir, code)
    # 账套码表里没有这个说法 → 退回内置映射,但结果必须仍是该账套的合法码
    fallback = S.unit_to_tqucod(unit)
    if fallback and any(fallback == code for code, _s, _d in rows):
        return _accept(account_dir, fallback)
    return ""


def legal_codes(account_dir) -> List[str]:
    """本账套 ISTAB 里的全部合法单位码(诊断/测试用)。"""
    return [code for code, _s, _d in _unit_rows(account_dir)]


def _accept(account_dir, code: str) -> str:
    """码宽超过 `STMAS.QUCOD` 就返空:截短会造出另一个(可能不存在的)码,比不写更糟。"""
    width = dbf_meta.field_width(account_dir, "STMAS", "QUCOD")
    return code if len(code.encode(S.CODEPAGE, "replace")) <= width else ""


def _unit_rows(account_dir) -> List[Tuple[str, str, str]]:
    """ISTAB 单位段 → [(TYPCOD, SHORTNAM, TYPDES)]。表缺失/读不动 → 空表,调用方回落模板。"""
    path = Path(account_dir) / "ISTAB.DBF"
    if not path.is_file():
        return []
    t = dbf.Table(str(path), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        out = []
        for r in t:
            if dbf.is_deleted(r) or str(_gv(r, "TABTYP")) != _UNIT_TABTYP:
                continue
            code = _gv(r, "TYPCOD")
            if code:
                out.append((code, _gv(r, "SHORTNAM"), _gv(r, "TYPDES")))
        return out
    finally:
        t.close()


def _norm(text: str) -> str:
    s = (text or "").strip().lower()
    return "".join(ch for ch in s if not ch.isspace() and ch != "\xa0")


def _gv(rec, field: str, default=""):
    try:
        v = rec[field]
    except Exception:
        return default
    return v.strip() if isinstance(v, str) else v
