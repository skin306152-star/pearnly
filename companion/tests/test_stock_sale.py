# -*- coding: utf-8 -*-
"""V2-b 卖库存品:选了库存就一定落得下去 · 合成账套验三分支。

① 有货:库存精确扣减 + STCRD 库存出行带均价成本 + GL 含 COGS 分录(借成本/贷存货)+ 借贷平。
② 有档没货:卖成更负;STNPR>0 按标准成本结转,STNPR 空则不结转(行成本写 0·reason 标出)。
③ 账里没这品:建 STKTYP=0 档再卖成负,无成本基础不结转(created=True + reason 标出)。
唯一 escalate:账套没配存货科目组(见 test_stock_template)。
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import dbf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _synth_account  # noqa: E402

from companion import cdx_reindex  # noqa: E402
from companion import dbf_sales as WS  # noqa: E402
from companion import dbf_schema as S  # noqa: E402
from companion import stock_sale as SS  # noqa: E402


@pytest.fixture
def acct(tmp_path, monkeypatch):
    monkeypatch.setattr(cdx_reindex, "runner_available", lambda: False)
    # 走共用骨架:带 GLACC/ISTAB —— 建库存档要靠它们判存货科目/单位码,漏建就测不到生产那条路
    d = _synth_account.build(tmp_path / "70EXP" / "test")
    # 种一个有库存的商品(STKTYP=0·均价成本 20)+ ISACC 存货/COGS 科目映射
    t = dbf.Table(str(d / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    t.append(
        {
            "STKCOD": "OIL01",
            "STKDES": "ดีเซล",
            "STKTYP": "0",
            "ACCCOD": "ST01",
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


def _stock_sale(**over):
    p = {
        "direction": "sales",
        "doctype": "HS",
        "account_set": "DATAT",
        "docdate_be": "690228",
        "vat_period_be": "690201",
        "ref_no": "IVSTK1",
        "customer": {
            "code": "C001",
            "name": "cus",
            "tax_id": "",
            "prename": "",
            "customer_new": False,
        },
        "vat_rate": 7.0,
        "base_amount": "600.00",
        "vat_amount": "42.00",
        "total_amount": "642.00",
        "lines": [
            {"acc": "11-01-01-00", "side": "D", "amount": "642.00", "desc": "cash"},
            {"acc": "41-01-00-00", "side": "C", "amount": "600.00", "desc": "rev"},
            {"acc": "11-05-04-02", "side": "C", "amount": "42.00", "desc": "vat"},
        ],
        "items": [
            {
                "name": "ดีเซล",
                "qty": "30",
                "unit_price": "20",
                "amount": "600",
                "item_mode": "stock_sale",
            }
        ],
        "items_status": "ok",
        "items_account": "41-01-00-00",
    }
    p.update(over)
    return p


def _rows(d, name, **m):
    t = dbf.Table(str(d / f"{name}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        return [r for r in t if all((r[k] or "").strip() == v for k, v in m.items())]
    finally:
        t.close()


def test_stock_sale_reduces_inventory_and_posts_cogs(acct):
    res = WS.write_sale(
        _stock_sale(),
        acct,
        allowed_sets=("DATAT",),
        selected_dir=str(acct),
        make_backup=False,
    )
    assert res.ok, res.error
    # 库存扣 30(均价 20):1000→970 · 货值 20000→19400
    stm = _rows(acct, "STMAS", STKCOD="OIL01")[0]
    assert abs(float(stm.MREMBAL) - 970.0) < 0.01
    assert abs(float(stm.MREMVAL) - 19400.0) < 0.01
    # STCRD 库存出行带成本(XUNITPR=均价 / XTRNVAL=COGS)
    stc = _rows(acct, "STCRD", DOCNUM=res.docnum)
    assert len(stc) == 1
    assert abs(float(stc[0].XUNITPR) - 20.0) < 0.01
    assert abs(float(stc[0].XTRNVAL) - 600.0) < 0.01
    # GL 含 COGS 分录(SEQIT 8 借成本 / 9 贷存货)且借贷平
    gl = _rows(acct, "GLJNLIT", VOUCHER=res.docnum)
    seqits = {(r.SEQIT or "").strip() for r in gl}
    assert "8" in seqits and "9" in seqits
    dr = sum(Decimal(str(r.AMOUNT)) for r in gl if (r.TRNTYP or "").strip() == "0")
    cr = sum(Decimal(str(r.AMOUNT)) for r in gl if (r.TRNTYP or "").strip() == "1")
    assert dr == cr
    cogs = next(r for r in gl if (r.SEQIT or "").strip() == "8")
    assert (cogs.ACCNUM or "").strip() == "51-01-00-00"  # COGS 科目来自 ISACC.ACCNUM02


def _sell_one(acct, name, qty, unit_price, amount, **over):
    return WS.write_sale(
        _stock_sale(
            items=[
                {
                    "name": name,
                    "qty": qty,
                    "unit_price": unit_price,
                    "amount": amount,
                    "item_mode": "stock_sale",
                }
            ]
        ),
        acct,
        allowed_sets=("DATAT",),
        selected_dir=str(acct),
        make_backup=over.pop("make_backup", False),
        **over,
    )


def test_unknown_item_is_created_and_sold_into_negative(acct):
    """③ 账里没这品 → 建 STKTYP=0 档 + 卖成负库存 + 不结转成本(没有成本基础,绝不编)。"""
    res = _sell_one(acct, "น้ำมันเครื่องใหม่", "3", "200", "600")
    assert res.ok, res.error
    # 建了真库存档(STKTYP=0),结存 -3、货值 0
    new = [
        r
        for r in _rows(acct, "STMAS")
        if (r.STKDES or "").strip() == "น้ำมันเครื่องใหม่"
    ]
    assert len(new) == 1 and (new[0].STKTYP or "").strip() == "0"
    assert abs(float(new[0].MREMBAL) + 3.0) < 0.01
    assert float(new[0].MREMVAL or 0) == 0.0
    # 行成本写 0(禁拿售价 200 倒推),且这一行没产 COGS 分录
    stc = _rows(acct, "STCRD", DOCNUM=res.docnum)
    assert len(stc) == 1
    assert float(stc[0].XUNITPR or 0) == 0.0 and float(stc[0].XTRNVAL or 0) == 0.0
    gl = _rows(acct, "GLJNLIT", VOUCHER=res.docnum)
    assert not [r for r in gl if (r.SEQIT or "").strip() in ("8", "9")]
    dr = sum(Decimal(str(r.AMOUNT)) for r in gl if (r.TRNTYP or "").strip() == "0")
    cr = sum(Decimal(str(r.AMOUNT)) for r in gl if (r.TRNTYP or "").strip() == "1")
    assert dr == cr
    # 诚实线:回执逐行标出「新建 + 未结转成本」
    assert res.line_modes[0]["created"] is True
    assert res.line_modes[0]["reason"] == SS.REASON_NEW_ITEM
    # 既有库存品 OIL01 一动没动
    assert abs(float(_rows(acct, "STMAS", STKCOD="OIL01")[0].MREMBAL) - 1000.0) < 0.01


def test_zero_stock_item_without_standard_cost_sells_uncosted(acct):
    """② 有档没货 + STNPR 空 → 卖成负,不结转 COGS,reason 让会计捞得回来。"""
    _zero_stock_item(acct, name="น้ำมันหมด")
    res = _sell_one(acct, "น้ำมันหมด", "1", "600", "600")
    assert res.ok, res.error
    oil2 = _rows(acct, "STMAS", STKCOD="OIL02")[0]
    assert abs(float(oil2.MREMBAL) + 1.0) < 0.01  # 0 → -1
    assert float(oil2.MREMVAL or 0) == 0.0
    stc = _rows(acct, "STCRD", DOCNUM=res.docnum)
    assert float(stc[0].XUNITPR or 0) == 0.0 and float(stc[0].XTRNVAL or 0) == 0.0
    assert not [
        r
        for r in _rows(acct, "GLJNLIT", VOUCHER=res.docnum)
        if (r.SEQIT or "").strip() == "8"
    ]
    assert res.line_modes[0]["created"] is False
    assert res.line_modes[0]["reason"] == SS.REASON_NO_COST


def test_negative_stock_with_standard_cost_carries_it(acct):
    """② 有档没货 + STNPR>0 → 按标准成本结转(真账负结存出货行 XUNITPR 恰等于 STNPR)。"""
    _zero_stock_item(acct, name="น้ำมันหมด", bal=-2.0, val=-30.0, stnpr=15.0)
    res = _sell_one(acct, "น้ำมันหมด", "4", "150", "600")
    assert res.ok, res.error
    oil2 = _rows(acct, "STMAS", STKCOD="OIL02")[0]
    assert abs(float(oil2.MREMBAL) + 6.0) < 0.01  # -2 → -6
    assert abs(float(oil2.MREMVAL) + 90.0) < 0.01  # -30 - 4×15
    stc = _rows(acct, "STCRD", DOCNUM=res.docnum)
    assert abs(float(stc[0].XUNITPR) - 15.0) < 0.01
    assert abs(float(stc[0].XTRNVAL) - 60.0) < 0.01
    gl = _rows(acct, "GLJNLIT", VOUCHER=res.docnum)
    cogs = [r for r in gl if (r.SEQIT or "").strip() == "8"]
    assert len(cogs) == 1 and abs(float(cogs[0].AMOUNT) - 60.0) < 0.01
    dr = sum(Decimal(str(r.AMOUNT)) for r in gl if (r.TRNTYP or "").strip() == "0")
    cr = sum(Decimal(str(r.AMOUNT)) for r in gl if (r.TRNTYP or "").strip() == "1")
    assert dr == cr
    assert res.line_modes[0]["reason"] == SS.REASON_STANDARD_COST


def test_mixed_known_and_unknown_both_land(acct):
    """同单:一件有货(均价 COGS)+ 一件账里没档(建档卖负·无成本)→ 整单落地,GL 仍平。"""
    res = WS.write_sale(
        _stock_sale(
            items=[
                {
                    "name": "ดีเซล",  # 有货(OIL01·均价 20)
                    "qty": "10",
                    "unit_price": "20",
                    "amount": "200",
                    "item_mode": "stock_sale",
                },
                {
                    "name": "สินค้าใหม่เอี่ยม",  # 账里没档 → 建档卖负
                    "qty": "1",
                    "unit_price": "400",
                    "amount": "400",
                    "item_mode": "stock_sale",
                },
            ],
        ),
        acct,
        allowed_sets=("DATAT",),
        selected_dir=str(acct),
        make_backup=True,
    )
    assert res.ok, res.error
    oil = _rows(acct, "STMAS", STKCOD="OIL01")[0]
    assert abs(float(oil.MREMBAL) - 990.0) < 0.01
    assert abs(float(oil.MREMVAL) - 19800.0) < 0.01  # 20000 - 10×20
    fresh = [
        r
        for r in _rows(acct, "STMAS")
        if (r.STKDES or "").strip() == "สินค้าใหม่เอี่ยม"
    ]
    assert len(fresh) == 1 and abs(float(fresh[0].MREMBAL) + 1.0) < 0.01
    # COGS 只来自有成本的那一行(10×20),GL 借贷仍平
    gl = _rows(acct, "GLJNLIT", VOUCHER=res.docnum)
    cogs = [r for r in gl if (r.SEQIT or "").strip() == "8"]
    assert len(cogs) == 1 and abs(float(cogs[0].AMOUNT) - 200.0) < 0.01
    dr = sum(Decimal(str(r.AMOUNT)) for r in gl if (r.TRNTYP or "").strip() == "0")
    cr = sum(Decimal(str(r.AMOUNT)) for r in gl if (r.TRNTYP or "").strip() == "1")
    assert dr == cr
    reasons = [m["reason"] for m in res.line_modes]
    assert reasons == ["", SS.REASON_NEW_ITEM]


def _zero_stock_item(
    acct, name="น้ำมันใหม่", stkcod="OIL02", bal=0.0, val=0.0, stnpr=0.0
):
    """种一个 STKTYP=0 的零/负库存品。stnpr=标准成本(0=没有成本基础)。"""
    t = dbf.Table(str(acct / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    t.append(
        {
            "STKCOD": stkcod,
            "STKDES": name,
            "STKTYP": "0",
            "ACCCOD": "ST01",
            "STNPR": stnpr,
            "MREMBAL": bal,
            "MREMVAL": val,
            "TOTBAL": bal,
            "TOTVAL": val,
        }
    )
    t.close()


def test_mixed_stock_and_service_lines_land_in_one_sale(acct):
    """混行(载荷 19,ERP 按行选出库 / 只记账,11-06):服务费行夹在两段库存行之间,一张单写完;SEQNUM 照载荷顺序不撞;
    只有库存行扣库存、结转成本,两段库存行的成本分录合成一对(同一次写完)。"""
    items = [
        {"name": "ดีเซล", "qty": "10", "unit_price": "20", "amount": "200", "item_mode": "stock_sale"},
        {"name": "ค่าบริการ", "qty": "1", "unit_price": "100", "amount": "100", "item_mode": "non_stock_item"},
        {"name": "ดีเซล", "qty": "15", "unit_price": "20", "amount": "300", "item_mode": "stock_sale"},
    ]
    res = WS.write_sale(
        _stock_sale(items=items, payload_version=19),
        acct,
        allowed_sets=("DATAT",),
        selected_dir=str(acct),
        make_backup=True,
    )
    assert res.ok, res.error
    stc = _rows(acct, "STCRD", DOCNUM=res.docnum)
    assert [int((r.SEQNUM or "").strip()) for r in stc] == [1, 2, 3]
    assert [(r.STKDES or "").strip() for r in stc] == ["ดีเซล", "ค่าบริการ", "ดีเซล"]
    assert not S.stcrd_no_stock_impact(stc[0])
    assert S.stcrd_no_stock_impact(stc[1])
    assert not S.stcrd_no_stock_impact(stc[2])
    stm = _rows(acct, "STMAS", STKCOD="OIL01")[0]
    assert abs(float(stm.MREMBAL) - 975.0) < 0.01  # 只扣两段柴油 25
    gl = _rows(acct, "GLJNLIT", VOUCHER=res.docnum)
    cogs = [r for r in gl if (r.SEQIT or "").strip() == "8"]
    assert len(cogs) == 1 and abs(float(cogs[0].AMOUNT) - 500.0) < 0.01  # 25 × 均价 20
    dr = sum(Decimal(str(r.AMOUNT)) for r in gl if (r.TRNTYP or "").strip() == "0")
    cr = sum(Decimal(str(r.AMOUNT)) for r in gl if (r.TRNTYP or "").strip() == "1")
    assert dr == cr
    assert [m["seq"] for m in res.line_modes] == [1, 2, 3]
    assert [m["mode"] for m in res.line_modes][0::2] == ["stock_sale", "stock_sale"]
    # 原生 IV / HS(S 盘 95 本人录的单,11-07):两种行都是 POSOPR 9、XSALVAL 记税前额;库存行 X 栏记成本,非库存行 X 栏是 0
    assert {(r.POSOPR or "").strip() for r in stc} == {S.STCRD_POSOPR}
    assert [float(r.XSALVAL) for r in stc] == [200.0, 100.0, 300.0]
    assert (float(stc[1].XUNITPR), float(stc[1].XTRNVAL)) == (0.0, 0.0)
    assert float(stc[0].XTRNVAL) > 0 and float(stc[2].XTRNVAL) > 0


def test_merge_gl_rows_sums_the_same_account_once():
    rows = [("8", "51", "0", Decimal("2")), ("9", "11", "1", Decimal("2")), ("8", "51", "0", Decimal("3"))]
    assert WS.merge_gl_rows(rows) == [("8", "51", "0", Decimal("5")), ("9", "11", "1", Decimal("2"))]
