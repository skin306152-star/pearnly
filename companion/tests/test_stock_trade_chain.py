# -*- coding: utf-8 -*-
"""CC 写进货 / 销货的库存行与库存单同一套取价与批次栏（``stock_costing``，K 系列第一项 11-22）。

- 分仓计价（LOC_COST = Y）：进货成为这个仓链上新的一批、LOCVAL 增加；销货按这个仓的链取最早仍有剩余的批，明细记链写前结存与指针，
  LOCVAL 减少；出超的部分按 0、链停在 0（原生）。以前两边都只动主档那条链，STLOC 的链与 LOCVAL 不动（11-18 实测）。
- 平均成本：销货按主档批次链 MREMVAL / MREMBAL 取价（原生 4.2），以前按总结存均价。
- 混行单分几段写时同一个取价状态接着用。
"""

from __future__ import annotations

import datetime as dt

import pytest

from account_rules_fixture import isinfo
from test_dbf_stock import _chain, _receipt
from test_stock_location import _buy, _item, _purchase, _rows, _sale, _sell, _stloc, acct  # noqa: F401

HEAD = "202603050RR0000010     1"


@pytest.fixture
def by_place(acct):
    """先进先出 + 分仓计价：ICE01 的 01 仓链 = RR0000010 的 10 个（单价 45）+ RR0000009 剩 2 个（单价 40）。"""
    isinfo(acct, MAINLOC="01", ISPERPETUA="Y", COSMTD="F", LOC_COST="Y", NEGALLOW="Y")
    _receipt(acct, "RR0000009", dt.date(2026, 1, 1), 10, 400)
    _receipt(acct, "RR0000010", dt.date(2026, 1, 5), 10, 450)
    _chain(acct, "ICE01", "01", MLOTNUM=HEAD, MREMBAL=12.0, MREMVAL=530.0, LOCVAL=530.0, LOCBAL=12.0)
    return acct


def test_a_purchase_in_a_book_costed_by_warehouse_becomes_a_lot_on_that_warehouse_chain(by_place):
    res = _buy(by_place, _purchase([_item("ICE01", 4, 200.0, "stock_item")]))
    assert res.ok, res.error
    line = _rows(by_place, "STCRD", DOCNUM=res.docnum)[0]
    # 原生增加行：成为新的一批，明细记这个仓的链写前结存与当前批的指针
    assert (line["POSOPR"], line["MLOTNUM"].strip(), line["MREMBAL"], line["MREMVAL"]) == ("0", HEAD, 12.0, 530.0)
    assert (line["LOTBAL"], line["LOTVAL"], line["LUNITPR"]) == (4.0, 200.0, 50.0)
    lot = f"202601150{res.docnum:<12}  1"
    place = _stloc(by_place, "ICE01", "01")
    assert (place["MLOTNUM"].strip(), place["MREMBAL"], place["MREMVAL"], place["LOCVAL"]) == (lot, 16.0, 730.0, 730.0)
    master = _rows(by_place, "STMAS", STKCOD="ICE01")[0]
    assert (master["MLOTNUM"].strip(), master["MREMBAL"], master["MREMVAL"]) == (lot, 16.0, 730.0)


def test_a_sale_in_a_book_costed_by_warehouse_takes_that_warehouse_earliest_lot_and_moves_its_chain(by_place):
    res = _sell(by_place, _sale([_item("ICE01", 3, 300.0, "stock_sale")]))
    assert res.ok, res.error
    line = _rows(by_place, "STCRD", DOCNUM=res.docnum)[0]
    # 出 3 = RR0000009 剩的 2 × 40 + 1 × 45（以前按总结存均价 10，主档 50 / 5）
    assert (line["XTRNVAL"], line["MLOTNUM"].strip(), line["MREMBAL"], line["MREMVAL"]) == (125.0, HEAD, 12.0, 530.0)
    place = _stloc(by_place, "ICE01", "01")
    assert (place["MLOTNUM"].strip(), place["MREMBAL"], place["MREMVAL"], place["LOCVAL"]) == (HEAD, 9.0, 405.0, 405.0)


def test_selling_more_than_the_warehouse_holds_costs_only_what_is_there_like_native(by_place):
    """原生 1-戊① / 11-19 允许负库存实录：结存为正但不够时只按现有货值计，多出的按 0；以前按均价 × 全部数量。"""
    res = _sell(by_place, _sale([_item("ICE01", 15, 1500.0, "stock_sale")]))
    assert res.ok, res.error
    line = _rows(by_place, "STCRD", DOCNUM=res.docnum)[0]
    assert line["XTRNVAL"] == 530.0
    place = _stloc(by_place, "ICE01", "01")
    assert (place["MLOTNUM"].strip(), place["MREMBAL"], place["MREMVAL"], place["LOCVAL"]) == ("-" + HEAD[1:], 0.0, 0.0, 0.0)


def test_a_sale_in_an_average_cost_book_takes_the_master_lot_chain_not_the_total_average(acct):
    """原生 4.2（平均成本账套）：出库取主档批次链 MREMVAL / MREMBAL；总结存均价与链不同时按链。"""
    from test_dbf_stock import _hold

    isinfo(acct, MAINLOC="01", ISPERPETUA="Y", COSMTD="A")
    _hold(acct, "ICE01", 5.0, 60.0)  # 链与总结存都是 5 / 60（单价 12）
    import dbf
    from companion import dbf_schema

    table = dbf.Table(str(acct / "STMAS.DBF"), codepage=dbf_schema.CODEPAGE)
    table.open(dbf.READ_WRITE)
    try:
        for rec in dbf_schema.live_rows(table):
            if rec.STKCOD.strip() == "ICE01":
                with rec as row:
                    row["TOTBAL"], row["TOTVAL"] = 8.0, 80.0  # 总结存均价 10
    finally:
        table.close()
    res = _sell(acct, _sale([_item("ICE01", 2, 200.0, "stock_sale")]))
    assert res.ok, res.error
    assert _rows(acct, "STCRD", DOCNUM=res.docnum)[0]["XTRNVAL"] == 24.0


def test_stock_lines_split_by_a_service_line_share_one_costing_state(by_place):
    """混行单（库存 / 非库存 / 库存）分三段写：后一段接着前一段已经扣过的批，不重读写前的链。"""
    items = [_item("ICE01", 2, 200.0, "stock_sale"), _item("SV01", 1, 50.0, "direct"), _item("ICE01", 2, 200.0, "stock_sale")]
    res = _sell(by_place, _sale(items))
    assert res.ok, res.error
    values = sorted(row["XTRNVAL"] for row in _rows(by_place, "STCRD", DOCNUM=res.docnum) if row["STKCOD"].strip() == "ICE01")
    assert values == [80.0, 90.0]  # 先 RR0000009 剩的 2 × 40，再 RR0000010 的 2 × 45
    place = _stloc(by_place, "ICE01", "01")
    assert (place["MREMBAL"], place["LOCVAL"]) == (8.0, 360.0)
