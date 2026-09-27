# -*- coding: utf-8 -*-
"""自动建库存档:克隆哪条(众数科目)+ 选中的存货科目认不认(守门)。

回归点:模板此前按物理行序取第一条,存货科目于是由 DBF 行顺序决定;而同一张 ISACC 里
ST02/ST03/ST05 之类的联动键指的是**费用**科目,行序一变就把存货直接记成费用,且零守门。

另一半是回落:账里一条库存品都没有时抄服务品档 + 调用方传入的存货科目组(同样过守门),
只有连科目组都没有才 escalate —— 「选了库存就一定能落」剩下的唯一死胡同。
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
from companion import stock_template as ST  # noqa: E402

CLONE = ("STKGRP", "QUCOD", "ACCCOD")

# 取自 70EXP 真账套 ISACC:前两个是真存货科目,其余是这套账里真实存在的"看着像库存、其实不是"
_ISACC = (
    ("ST01", "11-04-02-00"),  # สินค้าสำเร็จรูป(น้ำมันใส)
    ("ST10", "11-04-04-00"),  # สินค้าสำเร็จรูป(น้ำมันเครื่อง)
    ("ST02", "52-08-00-00"),  # ค่าใช้จ่าย-ส่งเสริมการขาย(费用)
    ("ST06", "14-01-03-00"),  # อุปกรณ์สำนักงาน(固定资产)
    ("STXX", ""),  # 联动键在但没配科目
)


@pytest.fixture
def acct(tmp_path):
    d = _synth_account.build(tmp_path / "70EXP" / "test")
    t = dbf.Table(str(d / "ISACC.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    for code, inv in _ISACC:
        t.append(
            {
                "ACCTYP": "S",
                "ACCCOD": code,
                "METHOD": "A",
                "ACCNUM01": inv,
                "ACCNUM02": "51-01-00-00",
            }
        )
    t.close()
    return d


def _stock(acct, stkcod, acccod, stkgrp="FG", qucod="ลต"):
    t = dbf.Table(str(acct / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        t.append(
            {
                "STKCOD": stkcod,
                "STKDES": stkcod,
                "STKTYP": "0",
                "ACCCOD": acccod,
                "STKGRP": stkgrp,
                "QUCOD": qucod,
            }
        )
    finally:
        t.close()


def _service(acct, stkcod, acccod, stkgrp="SV", qucod="คง"):
    """一条服务品档(STKTYP=3)—— 账里没库存品时的回落模板。"""
    t = dbf.Table(str(acct / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        t.append(
            {
                "STKCOD": stkcod,
                "STKDES": stkcod,
                "STKTYP": "3",
                "ACCCOD": acccod,
                "STKGRP": stkgrp,
                "QUCOD": qucod,
            }
        )
    finally:
        t.close()


def test_picks_the_dominant_acccod_not_the_first_row(acct):
    """行序第一条挂 ST01,但多数品挂 ST10 → 取 ST10(真账套 146/162 就是这个形状)。"""
    _stock(acct, "OIL01", "ST01", stkgrp="AA")
    for i in range(3):
        _stock(acct, f"ENG0{i}", "ST10", stkgrp="BB", qucod="ถง")
    tpl = ST.pick(acct, CLONE)
    assert tpl["ACCCOD"] == "ST10"
    assert tpl["STKGRP"] == "BB" and tpl["QUCOD"] == "ถง"  # 结构也来自那一类的真品


def test_ties_break_on_physical_order_for_reproducibility(acct):
    _stock(acct, "A1", "ST10")
    _stock(acct, "B1", "ST01")
    assert ST.pick(acct, CLONE)["ACCCOD"] == "ST10"


def test_rows_without_acccod_are_not_templates(acct):
    _stock(acct, "NOACC", "")
    _stock(acct, "OIL01", "ST01")
    assert ST.pick(acct, CLONE)["ACCCOD"] == "ST01"


def test_no_stock_rows_and_no_acccod_escalates(acct):
    """账里没库存品、调用方也没给存货科目组 → 这是「选库存」剩下的唯一 escalate。"""
    with pytest.raises(ST.StockAccGroupRequired):
        ST.pick(acct, CLONE)


def test_no_stock_rows_falls_back_to_a_service_item(acct):
    """账里没库存品 → 抄服务品档拿结构,存货科目用传入的账套配置值(不抄服务品那个)。"""
    _service(acct, "SRV1", "003U", stkgrp="HW", qucod="คง")
    tpl = ST.pick(acct, CLONE, acccod="ST01")
    assert tpl["ACCCOD"] == "ST01"  # 服务品的 003U 指向费用科目,一个字都不许抄
    assert tpl["STKGRP"] == "HW" and tpl["QUCOD"] == "คง"


def test_empty_account_still_builds_with_just_the_account_group(acct):
    """连一条非库存档都没有的空账套:只带存货科目组也要能建,别把用户堵死。"""
    assert ST.pick(acct, CLONE, acccod="ST10") == {"ACCCOD": "ST10"}


def test_passed_in_acccod_goes_through_the_same_gate(acct):
    """回落路不是守门的后门:传进来的科目组照样过 GLACC 判定。"""
    _service(acct, "SRV1", "003U")
    with pytest.raises(ST.InventoryAccountUnfit):
        ST.pick(acct, CLONE, acccod="ST02")  # 费用科目


def test_the_chosen_group_wins_over_the_dominant_one(acct):
    """会计为账套选过组(每个账套选一次)→ 新建库存品挂所选的组,结构抄同组的真品,不看众数。"""
    for i in range(3):
        _stock(acct, f"ENG0{i}", "ST10", stkgrp="BB")
    _stock(acct, "OIL01", "ST01", stkgrp="AA", qucod="กก")
    tpl = ST.pick(acct, CLONE, acccod="ST01")
    assert tpl == {"STKGRP": "AA", "QUCOD": "กก", "ACCCOD": "ST01"}


def test_a_chosen_group_without_items_borrows_the_dominant_structure(acct):
    """所选的组下还没有库存品:结构抄众数那一类,存货科目组换成所选。"""
    _stock(acct, "OIL01", "ST01", stkgrp="AA", qucod="ลต")
    tpl = ST.pick(acct, CLONE, acccod="ST10")
    assert tpl == {"STKGRP": "AA", "QUCOD": "ลต", "ACCCOD": "ST10"}


def test_the_chosen_group_goes_through_the_same_gate(acct):
    """所选的组照样过 GLACC 判定:选中费用科目的组也不建档。"""
    _stock(acct, "OIL01", "ST01")
    with pytest.raises(ST.InventoryAccountUnfit):
        ST.pick(acct, CLONE, acccod="ST02")


def test_rejects_expense_backed_account(acct):
    """众数科目指向费用科目 → 抛,不把存货记成费用。"""
    for i in range(2):
        _stock(acct, f"PROMO{i}", "ST02")
    with pytest.raises(ST.InventoryAccountUnfit) as e:
        ST.pick(acct, CLONE)
    assert "52-08-00-00" in str(e.value)


def test_rejects_fixed_asset_account(acct):
    """资产类但不是存货(办公设备)→ 也挡下:GROUP 对不代表科目对。"""
    _stock(acct, "DESK", "ST06")
    with pytest.raises(ST.InventoryAccountUnfit):
        ST.pick(acct, CLONE)


def test_rejects_acccod_without_inventory_account(acct):
    _stock(acct, "ORPHAN", "STXX")
    with pytest.raises(ST.InventoryAccountUnfit):
        ST.pick(acct, CLONE)


def test_rejects_account_missing_from_chart(acct):
    _stock(acct, "GHOST", "ST99")  # ISACC 里没有这个联动键
    with pytest.raises(ST.InventoryAccountUnfit):
        ST.pick(acct, CLONE)


@pytest.mark.parametrize("acccod,ok", [("ST01", True), ("ST10", True), ("ST02", False)])
def test_assert_inventory_account_matches_real_chart_facts(acct, acccod, ok):
    if ok:
        assert ST.assert_inventory_account(acct, acccod).startswith("11-04-")
    else:
        with pytest.raises(ST.InventoryAccountUnfit):
            ST.assert_inventory_account(acct, acccod)
