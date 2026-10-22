# -*- coding: utf-8 -*-
"""载荷 4：按载荷写指定仓库并维护各仓结存 STLOC（合成账套 · 过真写盘器）。

- 行上的 ``loccod`` 优先、表头的是行的默认；库存行写进那个仓，STLOC 照 Express 的记法记：LOCBAL 加减、期桶 QTY{期} /
  VAL{期}（ISPRD 财年期号）、最后进货 / 销货 / 变动日；没有那一行就照真表的样子新建；
- 仓库码不在 ISTAB（TABTYP = 21）里、超宽、不是文字 → 整单不写，回 ERR_LOCATION，不回落主仓；
- 一个 loccod 都没带的载荷与以前逐字节相同：不碰 STLOC、不多备份、不多重建索引；
- STLOC 进备份：写到一半失败回滚时 STLOC 一起还原；写完读回不符整单回滚。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import shutil
from pathlib import Path
from unittest import mock

import dbf
import pytest

import _synth_account
from account_rules_fixture import isinfo
from companion import cdx_reindex
from companion import dbf_sales as WS
from companion import dbf_schema as S
from companion import dbf_writer as W
from companion import stock_location

PURCHASE_ACC, INVENTORY_ACC, COGS_ACC = "51-03-00-00", "11-04-02-00", "51-01-00-00"
VAT_IN_ACC, AP_ACC, AR_ACC, REVENUE_ACC, VAT_OUT_ACC = "11-05-04-01", "21-02-01-00", "11-02-01-00", "41-01-00-00", \
    "11-05-04-02"


@pytest.fixture(autouse=True)
def _no_runner():
    with mock.patch.object(cdx_reindex, "runner_available", lambda: False):
        yield


def _append(path: Path, rows):
    table = dbf.Table(str(path), codepage=S.CODEPAGE)
    table.open(dbf.READ_WRITE)
    try:
        for row in rows:
            table.append(row)
    finally:
        table.close()


def _rows(acct: Path, name: str, **match):
    table = dbf.Table(str(acct / f"{name}.DBF"), codepage=S.CODEPAGE)
    table.open(dbf.READ_ONLY)
    try:
        return [{field: row[field] for field in table.field_names} for row in S.live_rows(table)
                if all(str(row[k] or "").strip() == v for k, v in match.items())]
    finally:
        table.close()


@pytest.fixture
def acct(tmp_path):
    acct = _synth_account.build(tmp_path / "70EXP" / "test")
    isinfo(acct, MAINLOC="01")
    _append(acct / "ISTAB.DBF", [{"TABTYP": "21", "TYPCOD": "01", "TYPDES": "คลังสำนักงานใหญ่"},
                                 {"TABTYP": "21", "TYPCOD": "02", "TYPDES": "คลังสาขา"}])
    _append(acct / "STMAS.DBF", [
        {"STKCOD": "ICE01", "STKDES": "น้ำแข็งหลอด", "STKTYP": "0", "ACCCOD": "ST01", "QUCOD": "ถง", "STKCLASS": "A",
         "MREMBAL": 5.0, "MREMVAL": 50.0, "TOTBAL": 5.0, "TOTVAL": 50.0},
        {"STKCOD": "ICE02", "STKDES": "น้ำแข็งก้อน", "STKTYP": "0", "ACCCOD": "ST01", "QUCOD": "ถง", "STKCLASS": "B"},
    ])
    _append(acct / "ISACC.DBF", [{"ACCTYP": "S", "ACCCOD": "ST01", "METHOD": "A", "ACCNUM01": INVENTORY_ACC,
                                  "ACCNUM02": COGS_ACC}])
    # 主仓已有一行：期初 5（Express 自己建的样子）
    _append(acct / "STLOC.DBF", [{"STKCOD": "ICE01", "LOCCOD": "01", "STKCLASS": "A", "LOCBAL": 5.0, "BEGBAL": 5.0,
                                  "BEGVAL": 50.0, "STATUS": "A", "LMOVDAT": dt.date(2025, 12, 1)}])
    return acct


def _item(code, qty, amount, mode, **extra):
    return {"name": code, "code": code, "qty": str(qty), "unit_price": str(amount / qty), "amount": f"{amount:.2f}",
            "unit": "ถัง", "item_mode": mode, **extra}


def _purchase(items, *, docdate="690115", **header):
    base = sum(float(item["amount"]) for item in items)
    vat = round(base * 0.07, 2)
    return {"doctype": "RR", "account_set": "DATAT", "docdate_be": docdate, "vat_period_be": docdate[:4] + "01",
            "ref_no": "PO-1", "supplier": {"code": "S001", "name": "โรงน้ำแข็ง", "tax_id": "", "prename": "",
                                           "supplier_new": False},
            "vat_rate": 7.0, "base_amount": f"{base:.2f}", "vat_amount": f"{vat:.2f}",
            "total_amount": f"{base + vat:.2f}",
            "lines": [{"acc": PURCHASE_ACC, "side": "D", "amount": f"{base:.2f}", "desc": "ซื้อ"},
                      {"acc": VAT_IN_ACC, "side": "D", "amount": f"{vat:.2f}", "desc": "ภาษีซื้อ"},
                      {"acc": AP_ACC, "side": "C", "amount": f"{base + vat:.2f}", "desc": "เจ้าหนี้"}],
            "items": items, "items_status": "ok", "items_account": PURCHASE_ACC, **header}


def _sale(items, *, docdate="690320", **header):
    base = sum(float(item["amount"]) for item in items)
    vat = round(base * 0.07, 2)
    return {"direction": "sales", "doctype": "IV", "account_set": "DATAT", "docdate_be": docdate,
            "vat_period_be": docdate[:4] + "01", "ref_no": "IV-1",
            "customer": {"code": "C001", "name": "ร้านป้าแดง", "tax_id": "", "prename": "", "customer_new": False},
            "vat_rate": 7.0, "base_amount": f"{base:.2f}", "vat_amount": f"{vat:.2f}",
            "total_amount": f"{base + vat:.2f}",
            "lines": [{"acc": AR_ACC, "side": "D", "amount": f"{base + vat:.2f}", "desc": "ลูกหนี้"},
                      {"acc": REVENUE_ACC, "side": "C", "amount": f"{base:.2f}", "desc": "รายได้"},
                      {"acc": VAT_OUT_ACC, "side": "C", "amount": f"{vat:.2f}", "desc": "ภาษีขาย"}],
            "items": items, "items_status": "ok", "items_account": REVENUE_ACC, **header}


def _buy(acct, payload, **kw):
    return W.write_purchase(payload, acct, allowed_sets=("DATAT",), selected_dir=str(acct),
                            make_backup=kw.pop("make_backup", False), **kw)


def _sell(acct, payload, **kw):
    return WS.write_sale(payload, acct, allowed_sets=("DATAT",), selected_dir=str(acct),
                         make_backup=kw.pop("make_backup", False), **kw)


def _state(folder: Path):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(folder.iterdir())
            if path.is_file()}


def _stloc(acct, code, loc):
    rows = _rows(acct, "STLOC", STKCOD=code, LOCCOD=loc)
    assert len(rows) <= 1
    return rows[0] if rows else None


def test_a_purchase_goes_into_the_named_warehouse_and_starts_its_stloc_row(acct):
    res = _buy(acct, _purchase([_item("ICE01", 100, 1000.0, "stock_item")], loccod="02"))
    assert res.ok, res.error
    card = _rows(acct, "STCRD", DOCNUM=res.docnum)
    assert [row["LOCCOD"].strip() for row in card] == ["02"]
    row = _stloc(acct, "ICE01", "02")
    assert row["LOCBAL"] == 100.0 and row["QTY1"] == 100.0 and row["VAL1"] == 1000.0  # 2026-01 = 财年第 1 期
    assert row["LPURDAT"] == dt.date(2026, 1, 15) == row["LMOVDAT"] and row["LSELDAT"] is None
    assert (row["STKCLASS"], row["STATUS"], row["LOCVAL"], row["BEGBAL"]) == ("A", "A", 0.0, 0.0)
    # 主仓那一行一动不动；商品主档的总结存照旧（全仓）
    assert _stloc(acct, "ICE01", "01")["LOCBAL"] == 5.0
    assert _rows(acct, "STMAS", STKCOD="ICE01")[0]["MREMBAL"] == 105.0


def test_a_sale_takes_out_of_the_line_warehouse_and_updates_the_same_row(acct):
    assert _buy(acct, _purchase([_item("ICE01", 100, 1000.0, "stock_item")], loccod="02")).ok
    res = _sell(acct, _sale([_item("ICE01", 30, 900.0, "stock_sale", loccod="02")]))
    assert res.ok, res.error
    assert [row["LOCCOD"].strip() for row in _rows(acct, "STCRD", DOCNUM=res.docnum)] == ["02"]
    row = _stloc(acct, "ICE01", "02")
    cogs = _rows(acct, "STCRD", DOCNUM=res.docnum)[0]["XTRNVAL"]
    assert row["LOCBAL"] == 70.0 and row["QTY3"] == -30.0 and row["VAL3"] == -cogs and cogs > 0
    assert row["QTY1"] == 100.0  # 进货那一期不变
    assert row["LSELDAT"] == dt.date(2026, 3, 20) == row["LMOVDAT"] and row["LPURDAT"] == dt.date(2026, 1, 15)
    assert row["LOCBAL"] == row["BEGBAL"] + sum(row[f"QTY{m}{s}"] for s in ("", "NY") for m in range(1, 13))


def test_line_codes_beat_the_header_and_lines_without_one_take_the_header(acct):
    res = _buy(acct, _purchase([_item("ICE01", 10, 100.0, "stock_item", loccod="01"),
                                _item("ICE02", 4, 80.0, "stock_item")], loccod="02"))
    assert res.ok, res.error
    assert {row["STKCOD"].strip(): row["LOCCOD"].strip() for row in _rows(acct, "STCRD", DOCNUM=res.docnum)} == \
        {"ICE01": "01", "ICE02": "02"}
    assert _stloc(acct, "ICE01", "01")["LOCBAL"] == 15.0  # 期初 5 + 10
    assert _stloc(acct, "ICE01", "01")["LMOVDAT"] == dt.date(2026, 1, 15)
    assert _stloc(acct, "ICE02", "02")["LOCBAL"] == 4.0 and _stloc(acct, "ICE02", "02")["STKCLASS"] == "B"
    assert _stloc(acct, "ICE02", "01") is None


@pytest.mark.parametrize("bad", [{"loccod": "09"}, {"loccod": "12345"}, {"loccod": 2}, {"items_loccod": "09"}])
def test_an_unknown_or_malformed_code_writes_nothing(acct, bad):
    before = _state(acct)
    items = [_item("ICE01", 10, 100.0, "stock_item", **({"loccod": bad.pop("items_loccod")}
                                                       if "items_loccod" in bad else {}))]
    res = _buy(acct, _purchase(items, **bad), make_backup=True)
    assert not res.ok and res.error_code == W.ERR_LOCATION == "ERR_LOCATION"
    assert _state(acct) == before  # 不回落主仓，一个字节都没写
    sale = _sell(acct, _sale([_item("ICE01", 1, 30.0, "stock_sale")], loccod="99"))
    assert not sale.ok and sale.error_code == "ERR_LOCATION"
    assert _state(acct) == before


def test_without_any_code_stloc_and_backup_and_reindex_are_untouched(acct, monkeypatch):
    backed, reindexed = [], []
    real_backup, real_reindex = W._backup, W._reindex_or_defer

    def backup(account_dir, tables=None):
        backed.append(tables)
        return real_backup(account_dir, tables)

    def reindex(*args, **kwargs):
        reindexed.append(kwargs)
        return real_reindex(*args, **kwargs)

    for module in (W, WS):
        monkeypatch.setattr(module, "_backup", backup)
        monkeypatch.setattr(module, "_reindex_or_defer", reindex)
    stloc = (acct / "STLOC.DBF").read_bytes()
    res = _buy(acct, _purchase([_item("ICE01", 10, 100.0, "stock_item")]), make_backup=True)
    assert res.ok, res.error
    sale = _sell(acct, _sale([_item("ICE01", 3, 90.0, "stock_sale")]), make_backup=True)
    assert sale.ok, sale.error
    assert (acct / "STLOC.DBF").read_bytes() == stloc
    assert backed == [S.PURCHASE_BACKUP_TABLES, S.SALES_BACKUP_TABLES]
    assert reindexed == [{}, {}]  # 没多传一个参数
    assert {row["LOCCOD"].strip() for row in _rows(acct, "STCRD")} == {"01"}  # 主仓 ISINFO.MAINLOC
    assert "stloc_bucket_not_written" not in (res.notes or {})


def test_a_failure_after_stloc_was_written_restores_it(acct, monkeypatch):
    before = _state(acct)

    def boom(*_args, **_kwargs):
        raise W._WriteError("forced", W.ERR_VERIFY)

    monkeypatch.setattr(W, "_verify", boom)
    res = _buy(acct, _purchase([_item("ICE01", 10, 100.0, "stock_item")], loccod="02"), make_backup=True)
    assert not res.ok and res.error_code == W.ERR_VERIFY
    assert _state(acct) == before  # STLOC 在备份里，一起还原


def test_a_readback_that_does_not_match_rolls_back(acct, monkeypatch):
    before = _state(acct)
    real = stock_location.apply

    def lying(account_dir, moves):
        expected, notes = real(account_dir, moves)
        return {key: {**values, "LOCBAL": values["LOCBAL"] + 1} for key, values in expected.items()}, notes

    monkeypatch.setattr(stock_location, "apply", lying)
    res = _buy(acct, _purchase([_item("ICE01", 10, 100.0, "stock_item")], loccod="02"), make_backup=True)
    assert not res.ok and res.error_code == W.ERR_VERIFY and res.evidence["table"] == "STLOC"
    assert _state(acct) == before


def test_a_date_outside_the_fiscal_calendar_keeps_the_balance_and_reports_the_bucket(acct):
    res = _buy(acct, _purchase([_item("ICE01", 10, 100.0, "stock_item")], loccod="02", docdate="710115"))
    assert res.ok, res.error
    row = _stloc(acct, "ICE01", "02")
    assert row["LOCBAL"] == 10.0 and all(row[f"QTY{m}{s}"] == 0 for s in ("", "NY") for m in range(1, 13))
    assert "2028-01-15" in res.notes["stloc_bucket_not_written"]


def test_non_stock_lines_take_the_code_but_never_touch_stloc(acct):
    stloc = (acct / "STLOC.DBF").read_bytes()
    items = [{"name": "ค่าขนส่ง", "qty": "1", "unit_price": "50", "amount": "50.00", "item_mode": "direct_account",
              "loccod": "02"}]
    res = _buy(acct, _purchase(items))
    assert res.ok, res.error
    assert [row["LOCCOD"].strip() for row in _rows(acct, "STCRD", DOCNUM=res.docnum)] == ["02"]
    assert (acct / "STLOC.DBF").read_bytes() == stloc


def test_the_plan_reads_only_the_warehouse_segment(acct):
    assert stock_location.warehouses(acct) == {"01": "คลังสำนักงานใหญ่", "02": "คลังสาขา"}
    assert stock_location.plan(acct, {"items": [{}]}) is None
    assert stock_location.plan(acct, {"loccod": "", "items": [{"loccod": None}]}) is None
    assert stock_location.plan(acct, {"loccod": "02", "items": [{"loccod": " 01 "}, {}]}) == ["01", "02"]
    with pytest.raises(stock_location.LocationError):
        stock_location.plan(acct, {"items": [{"loccod": "ลต"}]})  # 单位码段（TABTYP 20）的码不是仓库
    shutil.copy(acct / "STLOC.DBF", acct / "keep.DBF")
    (acct / "STLOC.DBF").unlink()
    with pytest.raises(stock_location.LocationError):
        stock_location.plan(acct, {"loccod": "02"})
