# -*- coding: utf-8 -*-
"""销售单的销售员照 Express 取客户档上的（覆盖表 G-A15，主会话 11-12；所有入口，不加载荷键、不问、不显示）。

原生（测试机只读统计，S 盘副本 1033 套，覆盖表第 9 节）：赊销单 90%、现销单 93.5% 带销售员，客户档 59% 带；我们直写以前完全不写。
写 IV / HS 时读客户档 ``ARMAS.SLMCOD`` 写进单头 ``ARTRN.SLMCOD``，明细行 ``STCRD.SLMCOD`` 也带同一个（S 盘副本 1033 套：单头有销售员的
销货单，明细 1024 万行与单头相同、约 4 千行空，11-12 只读统计）；客户档没有销售员、没有这一栏、客户是这张单新建的 → 留空，别的栏一个字节不变。
"""

from __future__ import annotations

import shutil

import dbf

from account_rules_fixture import seed
from companion import account_rules
from companion import dbf_schema as S
from test_stock_location import _item, _no_runner, _rows, _sale, _sell, _state, acct  # noqa: F401


def _customers(acct):
    table = dbf.Table(str(acct / "ARMAS.DBF"), codepage=S.CODEPAGE)
    table.open(dbf.READ_WRITE)
    table.add_fields("SLMCOD C(10)")
    table.close()
    lines = dbf.Table(str(acct / "STCRD.DBF"), codepage=S.CODEPAGE)  # 真账套的库存卡有这一栏（夹具里没有）
    lines.open(dbf.READ_WRITE)
    if "SLMCOD" not in {name.upper() for name in lines.field_names}:
        lines.add_fields("SLMCOD C(10)")
    lines.close()
    seed(acct, "ARMAS", [{"CUSCOD": "C001", "CUSNAM": "ร้านป้าแดง", "CUSTYP": "00", "SLMCOD": "S07"},
                         {"CUSCOD": "C002", "CUSNAM": "ร้านลุงดำ", "CUSTYP": "00"}])


def _to(payload, code):
    payload["customer"] = {**payload["customer"], "code": code}
    return payload


def test_a_sale_carries_the_salesman_of_the_customer(acct):
    _customers(acct)
    for doctype, ref in (("IV", "IV-1"), ("HS", "HS-1")):
        res = _sell(acct, _sale([_item("ICE01", 1, 30.0, "stock_sale")], doctype=doctype, ref_no=ref))
        assert res.ok, res.error
        assert _rows(acct, "ARTRN", DOCNUM=res.docnum)[0]["SLMCOD"].strip() == "S07", doctype
    assert account_rules.party_salesman(acct, "C001") == "S07"


def test_a_customer_without_a_salesman_leaves_it_empty(acct):
    _customers(acct)
    res = _sell(acct, _to(_sale([_item("ICE01", 1, 30.0, "stock_sale")]), "C002"))
    assert res.ok and _rows(acct, "ARTRN", DOCNUM=res.docnum)[0]["SLMCOD"].strip() == ""
    new = _sale([_item("ICE01", 1, 30.0, "stock_sale")], ref_no="IV-3")
    new["customer"] = {"code": "", "name": "ร้านใหม่เอี่ยม", "tax_id": "0105555000033", "prename": "", "customer_new": True}
    res = _sell(acct, new)
    assert res.ok and res.created_party and _rows(acct, "ARTRN", DOCNUM=res.docnum)[0]["SLMCOD"].strip() == ""
    assert account_rules.party_salesman(acct, "C002") == "" and account_rules.party_salesman(acct, "NOPE") == ""


def test_only_the_salesman_field_differs(acct, tmp_path, frozen_clock):
    """同一张单写进两本只差「客户档有没有销售员」的账套：写出来的单据只差 ARTRN.SLMCOD 这一栏。"""
    _customers(acct)
    twin = tmp_path / "twin"
    shutil.copytree(acct, twin)
    table = dbf.Table(str(twin / "ARMAS.DBF"), codepage=S.CODEPAGE)
    table.open(dbf.READ_WRITE)
    for rec in table:
        with rec as row:
            row["SLMCOD"] = ""
    table.close()
    payload = _sale([_item("ICE01", 2, 60.0, "stock_sale"), _item("ICE02", 1, 30.0, "stock_sale")])
    from companion import dbf_sales

    first = _sell(acct, payload)
    second = dbf_sales.write_sale(payload, twin, allowed_sets=("DATAT",), selected_dir=str(twin), make_backup=False)
    assert first.ok and second.ok and first.docnum == second.docnum
    differing = sorted(name for name in _state(acct) if _state(acct)[name] != _state(twin)[name])
    assert differing == ["ARMAS.DBF", "ARTRN.DBF", "STCRD.DBF"]
    for table in ("ARTRN", "STCRD"):
        for with_slm, without in zip(_rows(acct, table, DOCNUM=first.docnum), _rows(twin, table, DOCNUM=first.docnum)):
            assert [name for name in with_slm if with_slm[name] != without[name]] == ["SLMCOD"], table


def test_every_line_of_a_sale_carries_the_salesman_too(acct):
    """库存行（有货、没货）与只记账的行都带单头的销售员。"""
    _customers(acct)
    res = _sell(acct, _sale([_item("ICE01", 1, 30.0, "stock_sale"), _item("ICE02", 1, 30.0, "stock_sale"),
                             _item("ค่าขนส่ง", 1, 50.0, "direct_account")], ref_no="IV-L"))
    assert res.ok, res.error
    lines = _rows(acct, "STCRD", DOCNUM=res.docnum)
    assert len(lines) == 3 and all(str(r["SLMCOD"]).strip() == "S07" for r in lines)


def test_a_book_whose_customer_table_has_no_salesman_column_is_written_as_before(acct):
    res = _sell(acct, _sale([_item("ICE01", 1, 30.0, "stock_sale")]))
    assert res.ok and _rows(acct, "ARTRN", DOCNUM=res.docnum)[0]["SLMCOD"].strip() == ""
