"""B2 · set_opening_stock 单测(合成 VFP 表·不依赖客户数据)。

验:会计补期初(数量+单位成本)→ 建/复用库存目录档 + 置 STMAS 期初/结存(OPNBAL/MREMBAL…)
+ 写一条 STCRD 期初入库行 → 商品从缺货变【可卖】(find_stock_item 返 mrembal>0)。
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import dbf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _synth_account  # noqa: E402

from companion import dbf_schema as S  # noqa: E402
from companion import stock_sale as SS  # noqa: E402


@pytest.fixture
def acct(tmp_path):
    d = _synth_account.build(tmp_path / "70EXP" / "test")
    # 种一条 STKTYP=0 真库存模板(ensure_stock_item 克隆结构用·带 ACCCOD 科目联动)。
    t = dbf.Table(str(d / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    t.append(
        {
            "STKCOD": "TPL001",
            "STKDES": "template stock",
            "STKTYP": "0",
            "STKGRP": "FG",
            "ACCCOD": "ST01",
            "QUCOD": "ก",
            "VATCOD": "1",
            "MREMBAL": 0.0,
        }
    )
    t.close()
    # ST01 → 存货/COGS 科目(建库存档要据此判存货科目认不认,见 stock_template)
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


def test_opening_stock_makes_item_sellable(acct):
    docdat = dt.date(2026, 6, 1)
    res = SS.set_opening_stock(acct, [{"name": "น้ำแข็งหลอดเล็ก", "qty": 50, "unit_cost": 4.5}], docdat)
    assert res[0]["mode"] == "opening_set"
    stkcod = res[0]["stkcod"]

    # STMAS:期初 + 结存都写进去(OPNVAL/MREMVAL = 50 × 4.5 = 225)
    row = _stmas(acct, stkcod)
    assert float(row["OPNBAL"]) == 50.0
    assert float(row["OPNVAL"]) == 225.0
    assert float(row["MREMBAL"]) == 50.0
    assert float(row["MREMVAL"]) == 225.0
    assert float(row["TOTBAL"]) == 50.0

    # STCRD:一条期初入库行(带库存签名:XUNITPR=成本 · XTRNVAL=货值 · MREMBAL=结存)
    t = dbf.Table(str(acct / "STCRD.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    ob = [r for r in t if (r["STKCOD"] or "").strip() == stkcod]
    t.close()
    assert len(ob) == 1
    assert float(ob[0]["XUNITPR"]) == 4.5
    assert float(ob[0]["XTRNVAL"]) == 225.0
    assert float(ob[0]["MREMBAL"]) == 50.0

    # 商品现在可卖:find_stock_item 返回正库存(stock_sale ① 据此扣库存+算均价 COGS)
    hit = SS.find_stock_item(acct, "น้ำแข็งหลอดเล็ก")
    assert hit and hit["mrembal"] == 50.0 and hit["mremval"] == 225.0


def test_skip_when_item_already_has_stock(acct):
    docdat = dt.date(2026, 6, 1)
    SS.set_opening_stock(acct, [{"name": "ice A", "qty": 10, "unit_cost": 2}], docdat)
    # 再补一次同品(已有正库存)→ 不覆盖既有库存
    res = SS.set_opening_stock(acct, [{"name": "ice A", "qty": 99, "unit_cost": 9}], docdat)
    assert res[0]["mode"] == "skip_has_stock"
    assert SS.find_stock_item(acct, "ice A")["mrembal"] == 10.0


def test_reject_bad_qty_or_cost(acct):
    docdat = dt.date(2026, 6, 1)
    with pytest.raises(SS.StockItemNotFound):
        SS.set_opening_stock(acct, [{"name": "x", "qty": 0, "unit_cost": 5}], docdat)
