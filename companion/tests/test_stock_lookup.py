# -*- coding: utf-8 -*-
"""品名落档形/比对键/扫描的唯一口径。

核心回归:超过 STKDES 列宽的品名,**建档查重侧**与**卖货查货侧**必须给同一个答案。
两边口径一分家,长名就会「进货入进新建的空壳、销售在真档上找不到货」——C6 真账套的死锁。
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
from companion import product_master as PM  # noqa: E402
from companion import stock_lookup as SL  # noqa: E402
from companion import stock_sale as SS  # noqa: E402

# 60 字,长过 STMAS.STKDES 的 C(50) —— 真账套里这类品名有 29 个
LONG_NAME = "เพอร์ฟอร์มา เซมิ-ซิน 1 ล,PTT PERFORMA SEMI SYN 10W-40 (10/1L)"


@pytest.fixture
def acct(tmp_path):
    return _synth_account.build(tmp_path / "70EXP" / "test")


def _add_stmas(acct, **fields):
    t = dbf.Table(str(acct / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        t.append(fields)
    finally:
        t.close()


def test_norm_drops_whitespace_and_case():
    assert SL.norm(" A\xa0b  C ") == "abc"
    assert SL.norm("") == ""


def test_storage_des_follows_column_width(acct):
    stored = SL.storage_des(acct, LONG_NAME)
    assert len(stored) == 50  # 合成表照真表建 C(50)
    assert stored == LONG_NAME[:50]


def test_match_key_equals_normalized_stored_form(acct):
    """比对键 = 归一(落档形)。这一条成立,长名才可能比中库里的原值。"""
    assert SL.match_key(acct, LONG_NAME) == SL.norm(SL.storage_des(acct, LONG_NAME))


def test_long_name_hits_the_existing_row_on_both_lookup_paths(acct):
    """库里存的是被截过的 50 字原值,来件是 60 字全名 —— 两条查找路都必须命中同一条。"""
    _add_stmas(
        acct,
        STKCOD="58501732",
        STKDES=SL.storage_des(acct, LONG_NAME),
        STKTYP="0",
        ACCCOD="ST10",
        MREMBAL=120.0,
        MREMVAL=1440.0,
    )
    assert PM.find_stock_item_code(acct, LONG_NAME) == "58501732"  # 建档查重
    hit = SS.find_stock_item(acct, LONG_NAME)  # 卖货查货
    assert hit and hit["stkcod"] == "58501732" and hit["mrembal"] == 120.0


def test_find_rows_filters_by_stock_type(acct):
    _add_stmas(acct, STKCOD="SVC01", STKDES="ค่าบริการ", STKTYP="3")
    assert SL.first_code(acct, "ค่าบริการ", SL.NONSTOCK_TYPES) == "SVC01"
    assert SL.first_code(acct, "ค่าบริการ", (SL.STOCK_TYPE,)) is None


def test_find_rows_prefers_nothing_and_keeps_physical_order(acct):
    """同名多行:原样返回(调用方自己决定优先谁),顺序即物理行序。"""
    for code, bal in (("A1", 0.0), ("A2", 5.0)):
        _add_stmas(acct, STKCOD=code, STKDES="ซ้ำกัน", STKTYP="0", MREMBAL=bal)
    rows = SL.find_rows(acct, "ซ้ำกัน", (SL.STOCK_TYPE,))
    assert [r["stkcod"] for r in rows] == ["A1", "A2"]
    assert SS.find_stock_item(acct, "ซ้ำกัน")["stkcod"] == "A2"  # 卖货侧挑有货的那条


def test_empty_name_matches_nothing(acct):
    _add_stmas(acct, STKCOD="X1", STKDES="", STKTYP="0")
    assert SL.find_rows(acct, "   ", (SL.STOCK_TYPE,)) == []
