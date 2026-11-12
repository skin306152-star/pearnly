# -*- coding: utf-8 -*-
"""缺供应商发票号的进货单照写（覆盖表 D-5，主会话 11-12，所有入口）。

Express 不要求供应商发票号：原生赊购单 17%、现购单 50% 没有（测试机只读统计，S 盘副本 1033 套，覆盖表 9.2 节）。以前电脑端缺了就拒收
``MISSING_REF_NO``。现在照写：单号照旧自编（单别 + 佛历日期 + 序号，与发票号无关），发票号栏（单头 APTRN.REFNUM、进项税记录 ISVAT.REFNUM、
库存卡 STCRD.REFNUM）留空，其余各栏与带发票号的同一张单相同。
"""

from __future__ import annotations

import shutil

from test_stock_location import _buy, _item, _no_runner, _purchase, _rows, _state, acct  # noqa: F401


def test_a_purchase_without_an_invoice_number_is_written_with_the_field_left_empty(acct, tmp_path):
    twin = tmp_path / "twin"
    shutil.copytree(acct, twin)
    for doctype in ("RR", "HP"):
        with_no = _buy(acct, _purchase([_item("ICE01", 2, 20.0, "stock_item")], doctype=doctype, ref_no="INV-77"))
        without = _buy(twin, _purchase([_item("ICE01", 2, 20.0, "stock_item")], doctype=doctype, ref_no=""))
        assert with_no.ok and without.ok, (with_no.error, without.error)
        assert with_no.docnum == without.docnum
        for table in ("APTRN", "ISVAT", "STCRD"):
            a, b = _rows(acct, table, DOCNUM=with_no.docnum), _rows(twin, table, DOCNUM=without.docnum)
            assert len(a) == len(b) >= 1, table
            for full, empty in zip(a, b):
                assert str(full["REFNUM"]).strip() == "INV-77" and str(empty["REFNUM"]).strip() == "", table
                assert [name for name in full if full[name] != empty[name]] == ["REFNUM"], table
    differing = sorted(name for name in _state(acct) if _state(acct)[name] != _state(twin)[name])
    assert differing == ["APTRN.DBF", "ISVAT.DBF", "STCRD.DBF"]


def test_a_header_only_purchase_without_an_invoice_number_is_written(acct):
    res = _buy(acct, _purchase([], ref_no=None, items_status="empty", base_amount="100.00", vat_amount="7.00", total_amount="107.00",
                               lines=[{"acc": "51-03-00-00", "side": "D", "amount": "100.00", "desc": "ซื้อ"},
                                      {"acc": "11-05-04-01", "side": "D", "amount": "7.00", "desc": "ภาษีซื้อ"},
                                      {"acc": "21-02-01-00", "side": "C", "amount": "107.00", "desc": "เจ้าหนี้"}]))
    assert res.ok, res.error
    assert str(_rows(acct, "APTRN", DOCNUM=res.docnum)[0]["REFNUM"]).strip() == ""
