# -*- coding: utf-8 -*-
"""采购建真库存(STKTYP=0)· 入库行 + STMAS 累加 · 合成账套验闭环前半条。

覆盖:
- `dbf_schema.stcrd_stock_in_row` 与既有期初行的字段等价(重构不改期初行的任何写出值);
- `stock_purchase.write_stock_in_lines`:新建/复用库存档 · 结存累加(不动 OPNBAL)· 存货科目归集;
- 非法行(qty≤0 / amount<0)当场抛错交人,绝不写负库存/负成本;
- 闭环:进货后同品即可卖(find_stock_item 返正库存 → stock_sale ① 走移动均价 COGS)。

跨单据的整条闭环(采购票 → 销售票,过真写盘器)在 test_purchase_stock_loop.py。
"""

from __future__ import annotations

import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path

import dbf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _synth_account  # noqa: E402

from companion import dbf_schema as S  # noqa: E402
from companion import stock_purchase as SP  # noqa: E402
from companion import stock_sale as SS  # noqa: E402

# 重构前 stcrd_opening_row 的真实写出值(逐字段固化)。stcrd_stock_in_row 抽出后,期初行
# 必须仍写出这一模一样的一行——否则会悄悄改掉客户已落账的期初库存口径。
_OPENING_GOLD = {
    "STKCOD": "PN00001",
    "LOCCOD": "01",
    "DOCNUM": "OB260601-001",
    "SEQNUM": "1",
    "DOCDAT": dt.date(2026, 6, 1),
    # REFNUM 显式写起后:采购行带供应商票号,期初行没有对手方票 → 恒空串(与不写这一列
    # 落盘等价,DBF 字符列默认就是空白),期初口径未变。
    "REFNUM": "",
    "DEPCOD": "01",
    "POSOPR": "9",
    "PEOPLE": "",
    "TRNQTY": 50.0,
    "TQUCOD": "ถง",
    "TFACTOR": 1.0,
    "UNITPR": 4.5,
    "DISCAMT": 0.0,
    "TRNVAL": 225.0,
    "NETVAL": 225.0,
    "XTRNQTY": 50.0,
    "XUNITPR": 4.5,
    "XTRNVAL": 225.0,
    "XSALVAL": 225.0,
    "STKDES": "น้ำแข็งหลอดเล็ก",
    "MREMBAL": 50.0,
    "MREMVAL": 225.0,
}


def test_opening_row_unchanged_after_extracting_stock_in_row():
    row = S.stcrd_opening_row(
        "OB260601-001",
        1,
        dt.date(2026, 6, 1),
        "PN00001",
        "น้ำแข็งหลอดเล็ก",
        50,
        4.5,
        rem_bal=50,
        rem_val=225.0,
        unit="ถัง",
        depcod="01",
    )
    assert row == _OPENING_GOLD


def test_stock_in_row_carries_party_and_line_value():
    """入库行 = 期初行 + 对手方(供应商)· 且货值列是**本行入库额**,不是结存额。"""
    row = S.stcrd_stock_in_row(
        "RR690601-001",
        2,
        dt.date(2026, 6, 1),
        "OIL01",
        "S001",
        "ดีเซล",
        100,
        25.0,
        val=2500.0,
        rem_bal=1100.0,
        rem_val=22500.0,
        unit="ลิตร",
    )
    assert row["PEOPLE"] == "S001"
    assert row["TRNQTY"] == 100.0 and row["XTRNQTY"] == 100.0
    assert row["TRNVAL"] == 2500.0 and row["NETVAL"] == 2500.0
    assert row["XUNITPR"] == 25.0 and row["XTRNVAL"] == 2500.0
    assert row["MREMBAL"] == 1100.0 and row["MREMVAL"] == 22500.0
    assert not S.stcrd_no_stock_impact(row)  # 带库存签名 → 真动库存


@pytest.fixture
def acct(tmp_path):
    d = _synth_account.build(tmp_path / "70EXP" / "test")
    # 一条有库存的真库存品(均价 20·兼作 ensure_stock_item 的克隆模板)+ ISACC 存货/COGS 映射
    t = dbf.Table(str(d / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    t.append(
        {
            "STKCOD": "OIL01",
            "STKDES": "ดีเซล",
            "STKTYP": "0",
            "STKGRP": "FG",
            "ACCCOD": "ST01",
            "QUCOD": "ลต",
            "VATCOD": "1",
            "MREMBAL": 1000.0,
            "MREMVAL": 20000.0,
            "TOTBAL": 1000.0,
            "TOTVAL": 20000.0,
        }
    )
    t.close()
    t = dbf.Table(str(d / "ISACC.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    t.append(
        {
            "ACCTYP": "S",
            "ACCCOD": "ST01",
            "METHOD": "A",
            "ACCNUM01": "11-04-02-00",
            "ACCNUM02": "51-01-00-00",
        }
    )
    t.close()
    return d


def _stmas(acct, stkcod):
    t = dbf.Table(str(acct / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        return next(r for r in t if (r["STKCOD"] or "").strip() == stkcod)
    finally:
        t.close()


def _stcrd(acct, docnum):
    t = dbf.Table(str(acct / "STCRD.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        return [r for r in t if (r["DOCNUM"] or "").strip() == docnum]
    finally:
        t.close()


def _item(name, qty, amount, unit_price=None, unit=""):
    return {
        "name": name,
        "qty": str(qty),
        "unit_price": str(unit_price if unit_price is not None else amount / qty),
        "amount": str(amount),
        "unit": unit,
    }


def test_purchase_accumulates_into_existing_stock(acct):
    """复用既有库存品:结存/货值累加(1000+100=1100 · 20000+2500=22500),期初列不动。"""
    modes, by_acc = SP.write_stock_in_lines(
        acct,
        "RR690601-001",
        dt.date(2026, 6, 1),
        [_item("ดีเซล", 100, 2500, unit="ลิตร")],
        "S001",
    )
    row = _stmas(acct, "OIL01")
    assert float(row["MREMBAL"]) == 1100.0
    assert float(row["MREMVAL"]) == 22500.0
    assert float(row["TOTBAL"]) == 1100.0
    assert float(row["TOTVAL"]) == 22500.0
    assert float(row["OPNBAL"] or 0) == 0.0  # 采购是累加,不是置期初
    assert float(row["OPNVAL"] or 0) == 0.0

    stc = _stcrd(acct, "RR690601-001")
    assert len(stc) == 1
    assert float(stc[0]["XUNITPR"]) == 25.0  # 净额单价 = 2500/100
    assert float(stc[0]["XTRNVAL"]) == 2500.0  # 本行入库货值
    assert float(stc[0]["MREMBAL"]) == 1100.0  # 入库后结存
    assert (stc[0]["PEOPLE"] or "").strip() == "S001"

    assert modes[0]["stkcod"] == "OIL01" and modes[0]["created"] is False
    assert modes[0]["mode"] == "stock_item"
    assert by_acc == {"11-04-02-00": Decimal("2500.00")}  # 归集到存货科目


def test_purchase_creates_stock_item_when_absent(acct):
    """账里没这品 → 建 STKTYP=0 库存档(不是服务档)+ 真入库,不再只建空壳。"""
    modes, by_acc = SP.write_stock_in_lines(
        acct,
        "RR690601-002",
        dt.date(2026, 6, 1),
        [_item("น้ำแข็งหลอดเล็ก", 50, 225, unit="ถัง")],
        "S001",
    )
    stkcod = modes[0]["stkcod"]
    assert modes[0]["created"] is True
    row = _stmas(acct, stkcod)
    assert str(row["STKTYP"]).strip() == "0"
    assert float(row["MREMBAL"]) == 50.0
    assert float(row["MREMVAL"]) == 225.0
    assert by_acc == {"11-04-02-00": Decimal("225.00")}


def test_purchase_makes_item_sellable(acct):
    """闭环:进货后同品对销售侧即「有正库存」→ 走 stock_sale ① 移动均价 COGS(均价 4.5)。"""
    SP.write_stock_in_lines(
        acct,
        "RR690601-003",
        dt.date(2026, 6, 1),
        [_item("น้ำแข็งหลอดเล็ก", 50, 225, unit="ถัง")],
        "S001",
    )
    hit = SS.find_stock_item(acct, "น้ำแข็งหลอดเล็ก")
    assert hit and hit["mrembal"] == 50.0 and hit["mremval"] == 225.0
    assert hit["mremval"] / hit["mrembal"] == 4.5


def test_two_receipts_roll_moving_average(acct):
    """两次进货(不同单价)滚动加权:1000@20 + 500@30 → 1500 件 / 35000 → 均价 23.33。"""
    for seq, (qty, amt) in enumerate(((1000, 20000), (500, 15000)), start=1):
        SP.write_stock_in_lines(
            acct,
            f"RR69060{seq}-009",
            dt.date(2026, 6, seq),
            [_item("ผงซักฟอก", qty, amt)],
            "S001",
        )
    hit = SS.find_stock_item(acct, "ผงซักฟอก")
    assert hit["mrembal"] == 1500.0 and hit["mremval"] == 35000.0
    assert round(hit["mremval"] / hit["mrembal"], 2) == 23.33


@pytest.mark.parametrize(
    "qty,amount", [(0, 100), (-2, 100), (5, -100)]  # 零数量 / 负数量 / 负金额
)
def test_reject_illegal_qty_or_amount(acct, qty, amount):
    with pytest.raises(SP.StockPurchaseError):
        SP.write_stock_in_lines(
            acct,
            "RR690601-004",
            dt.date(2026, 6, 1),
            [_item("ดีเซล", qty, amount, unit_price=1)],
            "S001",
        )


def test_reject_when_account_set_has_no_stock_accounts(acct):
    """账套没配存货/COGS 科目 → 交人,不静默入库(否则 GL 归不了类)。"""
    t = dbf.Table(str(acct / "ISACC.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    for r in t:
        dbf.delete(r)
    t.close()
    with pytest.raises(SS.StockItemNotFound):
        SP.write_stock_in_lines(
            acct,
            "RR690601-005",
            dt.date(2026, 6, 1),
            [_item("ดีเซล", 10, 250)],
            "S001",
        )
