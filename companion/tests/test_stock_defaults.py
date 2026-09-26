# -*- coding: utf-8 -*-
"""建库存档的单位/类别默认值(账套众数)· 合成 VFP 表。

真账套那侧的取证在 tests/test_real_account_stock.py;这里只钉判据本身:
众数怎么算、并列取谁、取不到时返什么、单位众数要不要过合法性校验。
"""

from __future__ import annotations

import sys
from pathlib import Path

import dbf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _synth_account  # noqa: E402

from companion import dbf_schema as S  # noqa: E402
from companion import stock_defaults as SD  # noqa: E402


@pytest.fixture
def datat(tmp_path):
    return _synth_account.build(tmp_path / "70EXP" / "test")


def _add(d, **fields):
    t = dbf.Table(str(d / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        t.append(fields)
    finally:
        t.close()


def _seed(d, rows):
    for i, (typ, qucod, grp) in enumerate(rows, start=1):
        _add(
            d,
            STKCOD=f"S{i:04d}",
            STKDES=f"item {i}",
            STKTYP=typ,
            QUCOD=qucod,
            STKGRP=grp,
        )


def test_mode_picks_the_most_common_value():
    assert SD.mode(["a", "b", "a", "c", "a"]) == "a"


def test_mode_breaks_ties_by_first_appearance():
    # 并列时结果必须可复现 —— 换个顺序就换答案的话,同一账套两次建档会挂到不同类别。
    assert SD.mode(["b", "a", "a", "b"]) == "b"
    assert SD.mode(["a", "b", "b", "a"]) == "a"


def test_mode_ignores_blanks():
    assert SD.mode(["", "x", "", ""]) == "x"
    assert SD.mode(["", ""]) == ""
    assert SD.mode([]) == ""


def test_reads_modes_from_stock_items_only(datat):
    # 非库存档(服务/费用项)的单位类别不是库存品的默认 —— 混进来就是拿服务项的习惯当货品的。
    _seed(
        datat,
        [
            ("0", "ถง", "05"),
            ("0", "ถง", "05"),
            ("0", "กง", "OIL"),
            ("3", "คง", "คช"),
            ("3", "คง", "คช"),
            ("3", "คง", "คช"),
        ],
    )
    assert SD.read(datat) == SD.StockDefaults(qucod="ถง", stkgrp="05")


def test_soft_deleted_rows_do_not_vote(datat):
    """会计在 Express 里删掉的商品仍软删躺在 STMAS 里 —— 让它投票 = 拿删掉的货定今天的默认。"""
    _seed(datat, [("0", "ถง", "05"), ("0", "กง", "OIL"), ("0", "กง", "OIL")])
    t = dbf.Table(str(datat / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        for r in t:
            if (r.QUCOD or "").strip() == "กง":
                dbf.delete(r)
    finally:
        t.close()
    assert SD.read(datat) == SD.StockDefaults(qucod="ถง", stkgrp="05")


def test_empty_account_guesses_nothing(datat):
    # 一条库存品都没有 → 没有事实可依,两项都空。抄服务品档的值就是编。
    _seed(datat, [("3", "คง", "คช")])
    assert SD.read(datat) == SD.StockDefaults(qucod="", stkgrp="")


def test_illegal_unit_mode_is_dropped_not_written(datat):
    """众数单位不在本账套 ISTAB 里(账套换过版本/码被删)→ 返空,不写一个 Express 认不出的码。"""
    _seed(datat, [("0", "ZZ", "05"), ("0", "ZZ", "05")])
    out = SD.read(datat)
    assert out.qucod == ""
    assert out.stkgrp == "05"  # 类别不受单位码表影响,照样给
