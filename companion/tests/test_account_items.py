"""直接记科目的明细行要的主档商品(编码 = 科目码),照 Express 原生(11-02)。

S 盘副本 1037 本只读扫:原生明细的商品码一律在商品主档里,会计「直接记科目」时先建一件编码就是科目码的非库存商品;
CC 以前只写科目码,Express 原生作废按码查商品报 Message#. 2101、单子停在「作废不完整」(69EXP\\TEST 实录)。
现在写这种行前先保证有这件商品(有就复用、没有就建、同码是库存品就停下交人),1.12.11 以前写下的存量行在这本账套下一张
带明细的单的写前备份里补建,提交成功才记补过。合成账套,不依赖客户数据。
"""
from __future__ import annotations

import sys
from pathlib import Path

import dbf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion import dbf_schema as S  # noqa: E402
from companion import dbf_writer as W  # noqa: E402
from companion import product_master as P  # noqa: E402
from companion import worker  # noqa: E402

from test_dbf_writer import _payload_items, _rows, datat  # noqa: E402,F401

ACCOUNT = "11-04-02-00"


def _append(d, name, row):
    t = dbf.Table(str(d / f"{name}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        t.append(row)
    finally:
        t.close()


def _write(d, **over):
    return W.write_purchase(_payload_items(**over), d, allowed_sets=("DATAT",), selected_dir=str(d),
                            make_backup=True)


def test_the_item_is_made_once_with_the_account_code_and_reused(datat):
    _append(datat, "GLACC", {"ACCNUM": ACCOUNT, "ACCNAM": "ซื้อน้ำมัน"})
    first = _write(datat)
    second = _write(datat, ref_no="INV-9002")
    assert first.ok and second.ok, (first.error, second.error)
    items = _rows(datat, "STMAS", STKCOD=ACCOUNT)
    assert len(items) == 1  # 第二张复用,不重建
    assert (items[0].STKTYP or "").strip() == "3" and (items[0].STKDES or "").strip() == "ซื้อน้ำมัน"
    assert (_rows(datat, "ISACC", ACCCOD=(items[0].ACCCOD or "").strip())[0].ACCNUM01 or "").strip() == ACCOUNT
    assert first.line_modes[0]["mode"] == "direct_account" and first.line_modes[0]["created"] is True
    assert second.line_modes[0]["created"] is False


def test_an_item_the_accountant_made_with_the_account_code_is_used_as_it_is(datat):
    _append(datat, "STMAS", {"STKCOD": ACCOUNT, "STKDES": "ค่าน้ำมัน", "STKTYP": "5", "ACCCOD": "0028"})
    res = _write(datat)
    assert res.ok, res.error
    items = _rows(datat, "STMAS", STKCOD=ACCOUNT)
    assert [(i.STKTYP.strip(), i.STKDES.strip(), i.ACCCOD.strip()) for i in items] == [("5", "ค่าน้ำมัน", "0028")]
    assert _rows(datat, "ISACC") == []  # 人家的商品不动、不另建映射


def test_a_stock_item_with_the_account_code_stops_the_document_for_a_person(datat):
    _append(datat, "STMAS", {"STKCOD": ACCOUNT, "STKDES": "น้ำมันดีเซล", "STKTYP": "0"})
    res = _write(datat)
    assert not res.ok and res.error_code == P.ERR_ACCOUNT_ITEM_STOCK
    assert worker._outcome_for(res.error_code) == "needs_review"
    assert _rows(datat, "APTRN") == [] and _rows(datat, "STCRD") == []  # 整单回滚
    assert len(_rows(datat, "STMAS", STKCOD=ACCOUNT)) == 1


def _old_cc_document(d, docnum, code, userid=S.PEARNLY_USERID):
    """1.12.11 以前 CC(或会计)写下的单:明细只写了科目码、主档里没有这件商品。"""
    _append(d, "APTRN", {"DOCNUM": docnum, "RECTYP": "0", "USERID": userid, "NETAMT": 107.0})
    _append(d, "STCRD", {"DOCNUM": docnum, "STKCOD": code, "SEQNUM": "  1", "STKDES": "ค่าบริการ", "TRNQTY": 1.0,
                         "NETVAL": 100.0, "TRNVAL": 100.0})


def test_old_cc_lines_get_their_items_with_the_next_document_and_only_once(datat):
    _append(datat, "GLACC", {"ACCNUM": "53-01-00-00", "ACCNAM": "ค่าบริการ"})
    _append(datat, "GLACC", {"ACCNUM": "53-02-00-00", "ACCNAM": "ค่าเช่า"})
    _old_cc_document(datat, "RR0000001", "53-01-00-00")
    _old_cc_document(datat, "RR0000002", "53-02-00-00", userid="ACC01")  # 会计录的旧单:商品被删过,不碰
    assert P.backfill_due(datat)
    res = _write(datat)
    assert res.ok, res.error
    assert _rows(datat, "STMAS", STKCOD="53-01-00-00")
    assert not _rows(datat, "STMAS", STKCOD="53-02-00-00")
    assert not P.backfill_due(datat)  # 提交成功才记补过
    _old_cc_document(datat, "RR0000003", "53-02-00-00")  # 补过以后再出现的不再扫(新写的单自己会建)
    assert _write(datat, ref_no="INV-9003").ok
    assert not _rows(datat, "STMAS", STKCOD="53-02-00-00")


def test_a_rolled_back_document_leaves_the_book_to_be_backfilled_again(datat):
    _append(datat, "GLACC", {"ACCNUM": "53-01-00-00", "ACCNAM": "ค่าบริการ"})
    _old_cc_document(datat, "RR0000001", "53-01-00-00")
    _append(datat, "STMAS", {"STKCOD": ACCOUNT, "STKDES": "น้ำมันดีเซล", "STKTYP": "0"})  # 这一张会停下、整单回滚
    assert not _write(datat).ok
    assert not _rows(datat, "STMAS", STKCOD="53-01-00-00")  # 补建跟着回滚
    assert P.backfill_due(datat)  # 下次还补

