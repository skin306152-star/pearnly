# -*- coding: utf-8 -*-
"""单据行附加说明 ARTRNRM（按台号码，载荷 17）：原生行号字节、条数 / 长度闸、读回、作废。"""

from __future__ import annotations

import dbf
import pytest

from companion import dbf_remarks as R
from companion import dbf_schema as S

from tests import _synth_account


@pytest.fixture()
def blank(tmp_path):
    return _synth_account.build(tmp_path / "acct")


def _raw(acct):
    table = dbf.Table(str(acct / "ARTRNRM.DBF"), codepage=S.CODEPAGE)
    table.open(dbf.READ_ONLY)
    try:
        return [(r.DOCNUM.strip(), R._raw_seqnum(r), r.REMARK.rstrip(), dbf.is_deleted(r)) for r in table]
    finally:
        table.close()


def test_seqnum_is_right_aligned_three_then_padded_to_eight():
    assert R.seqnum_text(1) == "  1     "
    assert R.seqnum_text(12) == " 12     "
    assert R.seqnum_text("3") == "  3     "


def test_items_without_remarks_touch_nothing():
    items = [{"name": "a"}, {"name": "b", "remarks": []}, {"name": "c", "remarks": ["  "]}]
    assert R.tables(items) == ()
    assert R.planned(enumerate(items, start=1)) == []
    assert R.problem(items) is None


def test_problem_rejects_long_and_too_many():
    assert R.problem([{"remarks": ["ก" * 50]}]) is None  # 泰文一字一字节（cp874）
    assert R.problem([{"remarks": ["x" * 51]}])[0] == R.ERR_TOO_LONG
    assert R.problem([{"remarks": [str(n) for n in range(10)]}]) is None
    assert R.problem([{}, {"remarks": [str(n) for n in range(11)]}])[0] == R.ERR_TOO_MANY


def test_append_writes_native_bytes_in_order_and_verifies(blank):
    items = [{"remarks": ["เลขตัวถัง MP1TFR41JTG032993", "เลขเครื่อง FUK575 สี ELG"]}, {"name": "no units"},
             {"remarks": ["S/N 352948094653751  352948094652259"]}]
    rows = R.planned(enumerate(items, start=1))
    since = R.append(blank, "IV0000041", rows)
    R.verify(blank, "IV0000041", rows, since)
    assert _raw(blank) == [("IV0000041", "  1     ", "เลขตัวถัง MP1TFR41JTG032993", False),
                          ("IV0000041", "  1     ", "เลขเครื่อง FUK575 สี ELG", False),
                          ("IV0000041", "  3     ", "S/N 352948094653751  352948094652259", False)]
    with open(blank / "ARTRNRM.DBF", "rb") as f:
        assert "เลขตัวถัง".encode("cp874") in f.read()


def test_verify_only_reads_rows_appended_now(blank):
    R.append(blank, "IV0000041", [(R.seqnum_text(1), "old")])  # 同号单早有说明（销项单号是票面号，可能撞）
    rows = [(R.seqnum_text(1), "new")]
    since = R.append(blank, "IV0000041", rows)
    R.verify(blank, "IV0000041", rows, since)
    with pytest.raises(R.RemarkError):
        R.verify(blank, "IV0000041", [(R.seqnum_text(2), "new")], since)


def test_void_marks_line_remarks_deleted_and_keeps_header_ones(blank):
    R.append(blank, "OU0000007", [(R.seqnum_text(1), "S/N 1"), ("@1", "head"), (R.seqnum_text(2), "S/N 2")])
    R.append(blank, "OU0000008", [(R.seqnum_text(1), "other doc")])
    assert R.void(blank, "OU0000007") == 2
    assert [(d, s.strip(), t, deleted) for d, s, t, deleted in _raw(blank)] == [
        ("OU0000007", "1", "S/N 1", True), ("OU0000007", "@1", "head", False), ("OU0000007", "2", "S/N 2", True),
        ("OU0000008", "1", "other doc", False)]
    assert R.void(blank, "OU0000007") == 0  # 再作废一次不重复打


# ── 各写路带号（载荷 17）──────────────────────────────────────────────

from companion import dbf_void  # noqa: E402
from test_dbf_stock import _entry, _line, _write as _stock_write, book  # noqa: E402,F401
from test_dbf_transfer import _transfer, _line as _rl_line, _write as _rl_write, rl_book  # noqa: E402,F401
from test_stock_location import _buy, _item, _purchase, _sale, _sell, _state  # noqa: E402,F401
from test_stock_location import acct  # noqa: E402,F401

CAR = ["เลขตัวถัง MP1TFR41JTG032993", "เลขเครื่อง FUK575 สี ELG"]


def _remarks(acct, docnum):
    return [(s, t, deleted) for d, s, t, deleted in _raw(acct) if d == docnum]


def test_a_purchase_and_a_sale_carry_the_unit_numbers_under_their_lines(acct):
    res = _buy(acct, _purchase([_item("ICE01", 1, 900.0, "stock_item", remarks=CAR),
                                      _item("ICE02", 1, 5.0, "stock_item")]))
    assert res.ok, res.error
    assert _remarks(acct, res.docnum) == [("  1     ", CAR[0], False), ("  1     ", CAR[1], False)]
    sold = _sell(acct, _sale([_item("ICE02", 1, 9.0, "stock_sale"), _item("ICE01", 1, 990.0, "stock_sale", remarks=CAR)]))
    assert sold.ok, sold.error
    assert _remarks(acct, sold.docnum) == [("  2     ", CAR[0], False), ("  2     ", CAR[1], False)]


def test_without_remarks_the_book_is_byte_for_byte_what_it_was(acct, tmp_path):
    import shutil

    twin = tmp_path / "twin"
    shutil.copytree(acct, twin)
    payload = _purchase([_item("ICE01", 2, 20.0, "stock_item")])
    assert _buy(acct, payload).ok
    assert _buy(twin, {**payload, "items": [{**payload["items"][0], "remarks": []}]}).ok
    assert _state(acct) == _state(twin)
    assert _raw(acct) == []


@pytest.mark.parametrize("remarks, code", [(["x" * 51], R.ERR_TOO_LONG), ([str(n) for n in range(11)], R.ERR_TOO_MANY)])
def test_remarks_that_express_cannot_hold_write_nothing(acct, remarks, code):
    before = _state(acct)
    res = _buy(acct, _purchase([_item("ICE01", 1, 900.0, "stock_item", remarks=remarks)]))
    assert not res.ok and res.error_code == code
    assert _state(acct) == before


def test_a_failed_remark_readback_rolls_the_whole_document_back(acct, monkeypatch):
    before = _state(acct)
    monkeypatch.setattr(R, "verify", lambda *a, **k: (_ for _ in ()).throw(R.RemarkError("boom")))
    res = _buy(acct, _purchase([_item("ICE01", 1, 900.0, "stock_item", remarks=CAR)]), make_backup=True)
    assert not res.ok and res.error_code == "READBACK_VERIFY_FAILED"
    assert _state(acct) == before


def test_a_stock_entry_carries_them_and_its_void_deletes_them_like_express(book):
    res = _stock_write(book, _entry("OU", [_line("ICE01", 1, remarks=["S/N 352948094653751"])]))
    assert res.ok, res.error
    assert _remarks(book, res.docnum) == [("  1     ", "S/N 352948094653751", False)]
    out = dbf_void.void_stock_entry(
        {"payload_version": 8, "direction": "stock", "action": "void", "doctype": "OU", "docnum": res.docnum,
         "items": [{"seq": 1, "code": "ICE01", "loccod": "01", "qty": "1"}]}, book, selected_dir=str(book), make_backup=True)
    assert out.ok, out.error
    assert _remarks(book, res.docnum) == [("  1     ", "S/N 352948094653751", True)]


def test_a_transfer_hangs_them_once_per_pair_and_its_void_keeps_them_like_express(rl_book):
    res = _rl_write(rl_book, _transfer([_rl_line("ICE01", 1), _rl_line("ICE01", 1, remarks=CAR)]))
    assert res.ok, res.error
    assert _remarks(rl_book, res.docnum) == [("  2     ", CAR[0], False), ("  2     ", CAR[1], False)]
    out = dbf_void.void_stock_entry(
        {"payload_version": 14, "direction": "stock", "action": "void", "doctype": "RL", "docnum": res.docnum,
         "items": [{"seq": 1, "code": "ICE01", "loccod": "01", "qty": "1"}, {"seq": 2, "code": "ICE01", "loccod": "01", "qty": "1"}]},
        rl_book, selected_dir=str(rl_book), make_backup=False)
    assert out.ok, out.error
    assert [deleted for _s, _t, deleted in _remarks(rl_book, res.docnum)] == [False, False]
