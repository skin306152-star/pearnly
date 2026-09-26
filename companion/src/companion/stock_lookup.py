# -*- coding: utf-8 -*-
"""按品名找 STMAS 商品主档 —— 落档形、归一口径、扫描,三样都只有这一份。

品名落进定长列 `STKDES`,超长会被截。**存进去的形**与**拿来比的形**必须是同一个变换,
否则长名永远比不中已被截断的存量值:判成新品 → 重复建档。

C6 真账套验收踩的正是这条:建档侧按 40 字截了再比(库里是 50 字原值)判成新品、把货入进
新建的空壳;销售侧不截、比中了客户那条真档 —— 那条档余额 0,于是 STOCK_ITEM_NOT_FOUND,
闭环从中间断掉。所以建档查重与销售查货共用本模块的一次扫描,口径不给第二种可能。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional

import dbf

from . import dbf_meta
from . import dbf_schema as S

STOCK_TYPE = "0"  # 真库存货品
NONSTOCK_TYPES = ("3", "4", "5")  # 服务/费用等非库存型


def norm(name: str) -> str:
    """品名归一(仅用于比对):去所有空白(含泰文不断行空格 \\xa0)+ 小写。

    保守——不删泰文声调符,避免把不同品名误并(声调符过度归一曾在客户建档踩坑)。
    """
    s = (name or "").strip().lower()
    return "".join(ch for ch in s if not ch.isspace() and ch != "\xa0")


def storage_des(account_dir, name: str) -> str:
    """品名落到 STMAS.STKDES 的规范形(strip + 按该账套真实列宽裁)。"""
    return dbf_meta.fit(name, dbf_meta.field_width(account_dir, "STMAS", "STKDES"))


def match_key(account_dir, name: str) -> str:
    """来件品名 → 比对键:先按列宽裁(与落档同一变换)再归一。

    两个长名只在列宽之后才不同时,会归一成同一个键 —— STKDES 装不下区分,复用既有档是唯一
    合理解,总好过建出两条外观一模一样的档。
    """
    return norm(storage_des(account_dir, name))


def find_rows(account_dir, name: str, types: Iterable[str]) -> List[Dict]:
    """按名扫 STMAS,返回同名且 STKTYP 命中 `types` 的行(保持物理行序)。

    返回结构统一给三个调用方用:建档查重只看 stkcod,卖库存要看结存和科目联动键。
    """
    target = match_key(account_dir, name)
    if not target:
        return []
    allow = tuple(str(t) for t in types)
    t = dbf.Table(str(Path(account_dir) / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        out: List[Dict] = []
        for r in t:
            if dbf.is_deleted(r) or str(_gv(r, "STKTYP")) not in allow:
                continue
            if norm(_gv(r, "STKDES")) != target:
                continue
            out.append(
                {
                    "stkcod": _gv(r, "STKCOD"),
                    "mrembal": float(_gv(r, "MREMBAL") or 0),
                    "mremval": float(_gv(r, "MREMVAL") or 0),
                    "acccod": _gv(r, "ACCCOD"),
                }
            )
        return out
    finally:
        t.close()


def first_code(account_dir, name: str, types: Iterable[str]) -> Optional[str]:
    """同名主档的首个非空 STKCOD;无匹配返 None。建档幂等查重用。"""
    for row in find_rows(account_dir, name, types):
        if row["stkcod"]:
            return row["stkcod"]
    return None


def _gv(rec, field: str, default=""):
    try:
        v = rec[field]
    except Exception:
        return default
    return v.strip() if isinstance(v, str) else v
