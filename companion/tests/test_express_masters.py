# -*- coding: utf-8 -*-
"""按需请求 express_masters（小助手侧，ERP 前台）：只读读出一本账套的整套主档与各仓结存——只认本机能推送的同一组账套、
复制到本机再读、不动账套里任何文件；软删的行跳过；数字是字符串化的 Decimal；结构固定、缺的字段不省键；参数只收两个键。"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import dbf
import pytest

import _synth_account
from companion import express_masters, request_worker
from companion import dbf_schema as S
from companion.request_contract import KINDS, LEASE_SECONDS, MAX_BYTES, RequestFailed

_SCCOMP = "COMPNAM C(60); COMPCOD C(10); PATH C(150); GENDAT D; CANDEL C(1)"
_ISINFO = "THINAM C(60); MAINLOC C(4); NEGALLOW C(1); COSMTD C(1); USEDEP C(1)"
# 真表的商品主档另有这些列（合成规格只建写入器要碰的）
_STMAS_EXTRA = "BARCOD C(20); SELLPR1 B; SELLPR2 B; SELLPR3 B; SELLPR4 B; SELLPR5 B"
_PARTY_EXTRA = "ZIPCOD C(5); TELNUM C(50); PAYTRM N(3,0)"


def _table(path: Path, spec: str, rows):
    table = dbf.Table(str(path), spec, codepage=S.CODEPAGE, dbf_type=S.DBF_TYPE)
    table.open(dbf.READ_WRITE)
    try:
        for row in rows:
            table.append(row)
    finally:
        table.close()


def _append(path: Path, rows, deleted=()):
    table = dbf.Table(str(path), codepage=S.CODEPAGE)
    table.open(dbf.READ_WRITE)
    try:
        start = len(table)
        for row in rows:
            table.append(row)
        for index in deleted:
            dbf.delete(table[start + index])
    finally:
        table.close()


def _delete(path: Path, index: int):
    table = dbf.Table(str(path), codepage=S.CODEPAGE)
    table.open(dbf.READ_WRITE)
    try:
        dbf.delete(table[index])
    finally:
        table.close()


@pytest.fixture
def books(tmp_path):
    root = tmp_path / "EXP69"
    acct = _synth_account.build(root / "DAOWNUEA")
    (root / "secure").mkdir()
    _table(root / "secure" / "SCCOMP.DBF", _SCCOMP, [{"COMPNAM": "ดาวเหนือ", "COMPCOD": "DN", "PATH": "DAOWNUEA"}])
    _table(acct / "ISINFO.DBF", _ISINFO, [{"THINAM": "ดาวเหนือ", "MAINLOC": "01", "NEGALLOW": "Y", "COSMTD": "F",
                                           "USEDEP": "Y"}])
    _table(acct / "STMAS.DBF", S.TABLE_SPECS["STMAS"] + "; " + _STMAS_EXTRA, [
        {"STKCOD": "P01", "STKDES": "น้ำแข็งหลอด", "STKTYP": "0", "STKGRP": "ICE", "QUCOD": "ถง", "SQUCOD": "ถง",
         "SFACTOR": 1.0, "PQUCOD": "กล", "PFACTOR": 12.0, "SELLPR1": 0.1 + 0.2, "SELLPR2": 35.5, "SELLPR5": 1.23456789,
         "BARCOD": "8850001", "VATCOD": "1", "NEGALLOW": "N"},
        {"STKCOD": "S01", "STKDES": "ค่าขนส่ง", "STKTYP": "4"},
        {"STKCOD": "OLD", "STKDES": "ลบแล้ว", "STKTYP": "0"},
    ])
    _delete(acct / "STMAS.DBF", 2)
    _table(acct / "ARMAS.DBF", S.TABLE_SPECS["ARMAS"] + "; " + _PARTY_EXTRA, [
        {"CUSCOD": "C01", "CUSNAM": "ร้านป้าแดง", "TAXID": "0105560000001", "ORGNUM": 2, "ADDR01": "1 ถนนหนึ่ง",
         "ADDR03": "เมือง", "ZIPCOD": "31000", "TELNUM": "044-111111", "PAYTRM": 30},
        {"CUSCOD": "C02", "CUSNAM": "ลบแล้ว"},
    ])
    _delete(acct / "ARMAS.DBF", 1)
    # 编码里的不换行空格照原样给（写账按原样比对编码），名称里的当普通空格（10-23 验收）
    _append(acct / "APMAS.DBF", [{"SUPCOD": "ชาญ\xa0ฟู้ด", "SUPNAM": "ชาญ\xa0ฟู้ด", "TAXID": "0105556012345"}])
    _append(acct / "ISTAB.DBF", [
        {"TABTYP": "21", "TYPCOD": "01", "SHORTNAM": "สนญ.", "TYPDES": "คลังสำนักงานใหญ่"},
        {"TABTYP": "21", "TYPCOD": "02", "SHORTNAM": "สาขา"},
        {"TABTYP": "21", "TYPCOD": "03", "TYPDES": "ลบแล้ว"},
        {"TABTYP": "50", "TYPCOD": "SUTI", "TYPDES": "ฝ่ายขาย"},
    ], deleted=(2,))
    # 准备写账载荷要用的科目表（1.11.0 facts）：可记账、启用的才给
    _append(acct / "GLACC.DBF", [{"ACCNUM": "11-01-01-00", "ACCNAM": "เงินสด", "GROUP": "1", "ACCTYP": "0", "STATUS": "A"},
                                 {"ACCNUM": "11-00-00-00", "ACCNAM": "รวม", "GROUP": "1", "ACCTYP": "1", "STATUS": "A"}])
    _append(acct / "STLOC.DBF", [
        {"STKCOD": "P01", "LOCCOD": "01", "LOCBAL": 405747.7999999998, "LOCVAL": 1234.565},
        {"STKCOD": "P01", "LOCCOD": "02", "LOCBAL": -3.0},
        {"STKCOD": "P01", "LOCCOD": "03", "LOCBAL": 0.0, "LOCVAL": 0.0},  # 零结存：不给
        {"STKCOD": "OLD", "LOCCOD": "01", "LOCBAL": 9.0},  # 软删
    ], deleted=(3,))
    return SimpleNamespace(root=root, acct=acct)


def _cfg(books):
    return SimpleNamespace(express_root=str(books.root), authorized_roots=[], account_set="", account_dir="")


def _params(books):
    return {"account_set": str(books.acct), "account_root": str(books.root)}


def _read(books, params=None):
    result = express_masters.read_masters(_cfg(books), params or _params(books), sleep=lambda _s: None)
    return result, json.loads(gzip.decompress(result.content).decode("utf-8"))


def _state(folder: Path):
    return {path.name: (path.stat().st_size, path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in sorted(folder.iterdir()) if path.is_file()}


def test_reads_the_whole_master_and_stock_by_warehouse_without_touching_the_books(books):
    before = _state(books.acct)
    result, data = _read(books)
    assert _state(books.acct) == before  # 只读：账套里每个文件都不变
    assert list(data) == ["version", "account_set", "mark", "read_at", "settings", "customers", "suppliers", "products",
                          "units", "warehouses", "departments", "stock", "facts"]
    assert data["version"] == 2 and data["account_set"] == express_masters.express_read._norm(books.acct)
    # facts（ERP 直连后准备写账载荷用，ADR-025）：与心跳按账套上报同形状；ISINFO 没有默认科目列、没有存货科目组就给空
    assert data["facts"] == {"accounts": [{"code": "11-01-01-00", "name": "เงินสด", "type": "1"}], "mapping": {},
                             "catalog": {"isacc_candidates": []}}
    assert data["read_at"].endswith("Z")
    assert data["settings"] == {"mainloc": "01", "negallow": "Y", "cosmtd": "F", "usedep": "Y", "address": "", "phone": ""}
    assert data["customers"] == [{"code": "C01", "name": "ร้านป้าแดง", "tax_id": "0105560000001", "branch": "00002",
                                  "address": "1 ถนนหนึ่ง เมือง 31000", "phone": "044-111111", "payterm": "30"}]
    assert data["suppliers"] == [{"code": "ชาญ\xa0ฟู้ด", "name": "ชาญ ฟู้ด", "tax_id": "0105556012345", "branch": "00000",
                                  "address": "", "phone": "", "payterm": "0"}]
    ice, freight = data["products"]
    assert ice == {"code": "P01", "name": "น้ำแข็งหลอด", "kind": "stock", "group": "ICE", "unit": "ถง",
                   "sale_unit": "ถง", "sale_factor": "1", "purchase_unit": "กล", "purchase_factor": "12",
                   "prices": ["0.3", "35.5", "0", "0", "1.234568"], "barcode": "8850001", "vatcod": "1",
                   "negallow": "N"}
    # 合成表没有的列（BARCOD 等在 S01 那行是空）照样给键
    assert freight["kind"] == "non_stock" and freight["barcode"] == "" and freight["prices"] == ["0"] * 5
    assert {row["code"] for row in data["units"]} == {code for code, _s, _d in _synth_account.ISTAB_UNITS}
    assert data["warehouses"] == [{"code": "01", "name": "คลังสำนักงานใหญ่"}, {"code": "02", "name": "สาขา"}]
    assert data["departments"] == [{"code": "SUTI", "name": "ฝ่ายขาย"}]
    assert data["stock"] == [{"code": "P01", "loccod": "01", "qty": "405747.8", "value": "1234.57"},
                             {"code": "P01", "loccod": "02", "qty": "-3", "value": "0.00"}]
    assert result.meta["counts"] == {"customers": 1, "suppliers": 1, "products": 2, "units": 7, "warehouses": 2,
                                     "departments": 1, "stock": 2}
    assert result.meta["format"] == "json.gz" and "bad_rows" not in result.meta


def test_only_the_two_location_keys_are_accepted(books, tmp_path):
    with pytest.raises(RequestFailed) as error:
        _read(books, {**_params(books), "since": "2026-01-01"})
    assert error.value.code == "params_invalid"
    with pytest.raises(RequestFailed) as error:
        _read(books, {"account_set": 5, "account_root": str(books.root)})
    assert error.value.code == "params_invalid"
    other = _synth_account.build(tmp_path / "ELSEWHERE" / "X")
    with pytest.raises(RequestFailed) as error:
        _read(books, {"account_set": str(other), "account_root": str(books.root)})
    assert error.value.code == "account_not_allowed"  # 不在这个根的登记表里


def test_missing_tables_give_empty_lists_but_not_a_folder_without_the_masters(books):
    for name in ("ARMAS", "APMAS", "STLOC", "ISINFO"):
        (books.acct / f"{name}.DBF").unlink()
    _result, data = _read(books)
    assert data["customers"] == data["suppliers"] == data["stock"] == []
    assert data["settings"] == {"mainloc": "", "negallow": "", "cosmtd": "", "usedep": "", "address": "", "phone": ""}
    (books.acct / "STMAS.DBF").unlink()
    with pytest.raises(RequestFailed) as error:
        _read(books)
    assert error.value.code == "account_tables_missing"


def test_a_result_too_large_is_refused_not_cut(books, monkeypatch):
    monkeypatch.setitem(express_masters.MAX_BYTES, "express_masters", 10)
    with pytest.raises(RequestFailed) as error:
        _read(books)
    assert error.value.code == "too_large"


def test_a_torn_copy_is_read_again_then_given_up(books, monkeypatch):
    calls = []

    def torn(_folder):
        calls.append(1)
        raise express_masters.express_read._Unreadable("STLOC: torn")

    monkeypatch.setattr(express_masters, "collect", torn)
    with pytest.raises(RequestFailed) as error:
        _read(books)
    assert error.value.code == "account_unreadable" and len(calls) == express_masters.express_read.READS


def test_bad_rows_are_counted_in_the_meta(books, monkeypatch):
    real = express_masters._stock

    def flaky(rec, columns, gone):
        if str(rec["LOCCOD"]).strip() == "02":
            raise ValueError("bad")
        return real(rec, columns, gone)

    monkeypatch.setattr(express_masters, "_stock", flaky)
    result, data = _read(books)
    assert [row["loccod"] for row in data["stock"]] == ["01"]
    assert result.meta["bad_rows"] == {"STLOC": 1}


def test_numbers_are_decimal_strings():
    assert express_masters.money(0.1 + 0.2) == "0.30"
    assert express_masters.money(None) == "0.00"
    assert express_masters.number(405747.7999999998) == "405747.8"
    assert express_masters.number(-0.0) == "0"
    assert express_masters.number(1e-9) == "0"
    assert express_masters.number(12.0) == "12"


def test_the_request_thread_knows_how_to_read_the_masters():
    assert "express_masters" in KINDS
    assert request_worker.HANDLERS["express_masters"] is express_masters.read_masters
    assert MAX_BYTES["express_masters"] == 16 * 1024 * 1024 and LEASE_SECONDS["express_masters"] == 900


def test_the_mark_changes_only_when_a_master_table_changes(books):
    # 1.12.0 express_marks：ERP 每 10 分钟看一眼，标记变了才整套读（只看文件属性，不读内容、不碰账套）
    before = _state(books.acct)
    params = _params(books)
    first = json.loads(gzip.decompress(express_masters.read_marks(_cfg(books), params).content))["mark"]
    _result, data = _read(books)
    assert data["mark"] == first and _state(books.acct) == before
    (books.acct / "GLACC.DBF").write_bytes((books.acct / "GLACC.DBF").read_bytes())  # 不是主档表：标记不变
    assert express_masters.mark(books.acct) == first
    _append(books.acct / "ARMAS.DBF", [{"CUSCOD": "C09", "CUSNAM": "ลูกค้าใหม่"}])
    assert express_masters.mark(books.acct) != first
    assert "express_marks" in KINDS and MAX_BYTES["express_marks"] < MAX_BYTES["express_masters"]
    assert request_worker.HANDLERS["express_marks"] is express_masters.read_marks


def test_the_book_settings_carry_the_company_address_and_phone(books):
    _result, data = _read(books)
    assert set(data["settings"]) >= {"address", "phone"}
