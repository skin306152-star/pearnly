# -*- coding: utf-8 -*-
"""写盘前三重一致性闸的"公司名"一证(目录 ISINFO.THINAM == 客户所选账套公司)。

写盘目标的三重一致性:① 目录 == 所选目录(dbf_schema.account_dir_allowed)·
② 账套名 == 所选名(adapter allowed)· ③ 该目录 ISINFO 公司 == 所选公司(本模块)。
第三证防"同尾目录/改名串账套":真去所选目录读一次 ISINFO 公司名比对。

全程只读;ISINFO 缺/坏/不符 → 视为不一致(fail-safe·宁可拒写不冒险写错账套)。
expected 为空 → 跳过此证(调用方未提供期望公司时不强加)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Tuple

import dbf

from . import dbf_schema as S

_INFO_DBF = "ISINFO.DBF"


def _norm(s) -> str:
    """公司名归一:去不间断空格 + 普通空格 + 大小写(Express 词间用 \\xa0)。"""
    return str(s or "").replace("\xa0", "").replace(" ", "").strip().lower()


def read_company(account_dir: str | Path) -> str:
    """只读账套目录 ISINFO 首行公司名 THINAM;无/坏档返回 ''。"""
    d = Path(account_dir)
    for cand in (_INFO_DBF, _INFO_DBF.lower()):
        p = d / cand
        if not p.exists():
            continue
        t = dbf.Table(str(p), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
        t.open(dbf.READ_ONLY)
        try:
            if len(t):
                return str(getattr(t[0], "THINAM", "") or "").replace("\xa0", " ").strip()
        finally:
            t.close()
    return ""


def account_company_matches(account_dir: str | Path, expected_company: str) -> Tuple[bool, str]:
    """所选目录 ISINFO 公司 == expected_company(归一比对)。

    expected 空 → 跳过(放行);ISINFO 读不到/不符 → (False, 原因)。
    """
    want = (expected_company or "").strip()
    if not want:
        return True, "company check skipped (no expected)"
    try:
        got = read_company(account_dir)
    except Exception as e:  # 坏档/锁定 → fail-safe 拒
        return False, f"ISINFO 读失败: {e}"
    if not got:
        return False, f"{account_dir} 无 ISINFO 公司名"
    if _norm(got) != _norm(want):
        return False, f"公司不符: got={got!r} want={want!r}"
    return True, f"company≈{want!r}"
