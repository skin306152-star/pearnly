# -*- coding: utf-8 -*-
"""按需请求 account_history（小助手侧，科目建议 B）：只读汇总一本账套近 12 个月「供应商 → 常用科目」——只认本机能推送的
同一组账套、复制到本机再读、不动账套里任何文件；只算借方行，不算现金 / 进项税 / 应付与供应商档上的应付科目；作废、软删、
太早的单与作废的凭证不算；次数按单据数，同一张单几行同科目只算一次。"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import dbf
import pytest

import _synth_account
from companion import account_history, request_worker
from companion import dbf_schema as S
from companion.request_contract import MAX_BYTES, RequestFailed

TODAY = dt.date.today()
SINCE = TODAY - dt.timedelta(days=365)
_SCCOMP = "COMPNAM C(60); COMPCOD C(10); PATH C(150); GENDAT D; CANDEL C(1)"
_ISINFO = "THINAM C(60); TAXID C(15); ACCNUM00 C(15); ACCNUM06 C(15); ACCNUM07 C(15)"
STATIONERY, REPAIR, POWER = "53-20-01-00", "53-08-00-00", "53-10-00-00"
CASH, INPUT_VAT, PAYABLE, OWN_PAYABLE = "11-01-01-00", "11-05-00-00", "21-01-00-00", "21-01-00-02"


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


def _days(n):
    return TODAY - dt.timedelta(days=n)


def _doc(docnum, supplier, days, docstat="N"):
    return {"RECTYP": "3", "DOCNUM": docnum, "DOCDAT": _days(days), "SUPCOD": supplier, "DOCSTAT": docstat}


def _head(voucher, days, docstat="N"):
    return {"JNLTYP": "04", "VOUDAT": _days(days), "VOUCHER": voucher, "DOCSTAT": docstat}


def _line(voucher, days, account, trntyp, amount=100.0, seq="0"):
    return {"VOUCHER": voucher, "SEQIT": seq, "VOUDAT": _days(days), "ACCNUM": account, "TRNTYP": trntyp,
            "AMOUNT": amount}


def _purchase(voucher, days, expense, *, payable=PAYABLE):
    return [_line(voucher, days, expense, "0", 100.0, "0"), _line(voucher, days, INPUT_VAT, "0", 7.0, "3"),
            _line(voucher, days, payable, "1", 107.0, "6")]


@pytest.fixture
def books(tmp_path):
    root = tmp_path / "EXP69"
    acct = _synth_account.build(root / "DAOWNUEA")
    (root / "secure").mkdir()
    _table(root / "secure" / "SCCOMP.DBF", _SCCOMP, [{"COMPNAM": "ดาวเหนือ", "COMPCOD": "DN", "PATH": "DAOWNUEA"}])
    _table(acct / "ISINFO.DBF", _ISINFO, [{"THINAM": "ดาวเหนือ", "TAXID": "0105560000001", "ACCNUM00": CASH,
                                           "ACCNUM06": INPUT_VAT, "ACCNUM07": PAYABLE}])
    _append(acct / "APMAS.DBF", [
        {"SUPCOD": "S001", "SUPNAM": "ศรีกรุง เครื่องเขียน", "TAXID": "0105556012345", "ORGNUM": 1,
         "ACCNUM": OWN_PAYABLE},
        {"SUPCOD": "S002", "SUPNAM": "การไฟฟ้าส่วนภูมิภาค", "TAXID": "0994000165510", "ACCNUM": PAYABLE},
    ])
    _append(acct / "APTRN.DBF", [
        _doc("RR-001", "S001", 30), _doc("RR-002", "S001", 60), _doc("HP-003", "S001", 90),
        _doc("RR-004", "S001", 120, docstat="C"),  # 作废的单
        _doc("RR-005", "S001", 400),  # 一年多以前
        _doc("RR-006", "S001", 10),  # 软删
        _doc("RR-007", "S001", 15),  # 凭证作废
        _doc("RR-008", "S002", 20),
        _doc("rr-009 ", "S001", 5),  # 单号大小写、空格与凭证不同也是同一张
    ], deleted=(5,))
    _append(acct / "GLJNL.DBF", [_head(number, 1) for number in
                                 ("RR-001", "RR-002", "HP-003", "RR-004", "RR-005", "RR-006", "RR-008", "RR-009")]
            + [_head("RR-007", 15, docstat="C")])
    _append(acct / "GLJNLIT.DBF", [
        *_purchase("RR-001", 30, STATIONERY, payable=OWN_PAYABLE),
        _line("RR-001", 30, STATIONERY, "0", 5.0, "1"),  # 同一张单两行同科目：只算一张
        *_purchase("RR-002", 60, STATIONERY, payable=OWN_PAYABLE),
        # 现购：借费用、贷现金
        _line("HP-003", 90, REPAIR, "0", 50.0), _line("HP-003", 90, CASH, "1", 50.0),
        *_purchase("RR-004", 120, REPAIR), *_purchase("RR-005", 400, REPAIR), *_purchase("RR-006", 10, REPAIR),
        *_purchase("RR-007", 15, REPAIR), *_purchase("RR-008", 20, POWER), *_purchase("RR-009", 5, STATIONERY),
        # 借方记了供应商档上的应付（冲销）：不算
        _line("RR-009", 5, OWN_PAYABLE, "0", 1.0, "7"), _line("RR-009", 5, REPAIR, "1", 1.0, "8"),
        _line("JV-999", 5, REPAIR, "0", 9.0),  # 不是进项单据的凭证
    ])
    return SimpleNamespace(root=root, acct=acct)


def _cfg(books):
    return SimpleNamespace(express_root=str(books.root), authorized_roots=[], account_set="", account_dir="")


def _params(books, **extra):
    return {"account_set": str(books.acct), "account_root": str(books.root), "since": SINCE.isoformat(), **extra}


def _read(books, **extra):
    result = account_history.read_history(_cfg(books), _params(books, **extra), sleep=lambda _s: None)
    return result, json.loads(gzip.decompress(result.content).decode("utf-8"))


def _state(folder: Path):
    return {path.name: (path.stat().st_size, path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in sorted(folder.iterdir()) if path.is_file()}


def test_counts_each_suppliers_usual_accounts_without_touching_the_books(books):
    before = _state(books.acct)
    result, data = _read(books)
    assert _state(books.acct) == before  # 只读：账套里每个文件都不变
    assert result.meta == {"format": "json.gz", "version": account_history.RESULT_VERSION, "suppliers": 2,
                           "documents": 5}
    assert (data["version"], data["since"], data["account_set"]) == (3, SINCE.isoformat(), "DAOWNUEA")
    assert data["read_at"].endswith("Z")
    assert data["excluded"] == sorted([CASH, INPUT_VAT, PAYABLE])
    suppliers = {row["code"]: row for row in data["suppliers"]}
    assert suppliers["S001"]["name"] == "ศรีกรุง เครื่องเขียน"
    assert (suppliers["S001"]["tax_id"], suppliers["S001"]["branch"]) == ("0105556012345", "00001")
    # 文具科目 3 张（RR-001 两行只算一张）、修理 1 张（现购 HP-003）；进项税、应付、现金、供应商档上的应付都不算；
    # 作废 / 软删 / 太早 / 凭证作废的单不算
    assert suppliers["S001"]["accounts"] == [
        {"code": STATIONERY, "docs": 3, "last": _days(5).isoformat()},
        {"code": REPAIR, "docs": 1, "last": _days(90).isoformat()}]
    assert suppliers["S001"]["documents"] == 4
    assert suppliers["S002"]["accounts"] == [{"code": POWER, "docs": 1, "last": _days(20).isoformat()}]
    assert data["diagnostics"]["missing_tables"] == []


def test_ties_put_the_most_recent_first_and_accounts_are_capped(books, monkeypatch):
    monkeypatch.setattr(account_history, "ACCOUNTS_PER_SUPPLIER", 1)
    _result, data = _read(books)
    assert [row["code"] for row in data["suppliers"][0]["accounts"]] == [STATIONERY]
    lines = [*_purchase("RR-101", 2, "53-99-00-00")]
    _append(books.acct / "APTRN.DBF", [_doc("RR-101", "S002", 2)])
    _append(books.acct / "GLJNL.DBF", [_head("RR-101", 2)])
    _append(books.acct / "GLJNLIT.DBF", lines)
    monkeypatch.setattr(account_history, "ACCOUNTS_PER_SUPPLIER", 5)
    _result, data = _read(books)
    s002 = next(row for row in data["suppliers"] if row["code"] == "S002")
    assert [row["code"] for row in s002["accounts"]] == ["53-99-00-00", POWER]  # 都是 1 张：最近用过的在前


def test_suppliers_are_capped_by_how_many_documents_they_have(books, monkeypatch):
    monkeypatch.setattr(account_history, "SUPPLIERS_MAX", 1)
    _result, data = _read(books)
    assert [row["code"] for row in data["suppliers"]] == ["S001"]


def test_only_account_sets_this_computer_may_push_to_and_a_sane_since(books, tmp_path):
    other = _synth_account.build(tmp_path / "ELSEWHERE")
    with pytest.raises(RequestFailed) as caught:
        account_history.read_history(_cfg(books), _params(books, account_set=str(other)), sleep=lambda _s: None)
    assert caught.value.code == "account_not_allowed"
    for since in ("", "2026-13-01", (TODAY + dt.timedelta(days=1)).isoformat(),
                  (TODAY - dt.timedelta(days=account_history.SINCE_DAYS_MAX + 1)).isoformat()):
        with pytest.raises(RequestFailed) as caught:
            account_history.read_history(_cfg(books), _params(books, since=since), sleep=lambda _s: None)
        assert caught.value.code == "params_invalid"


def test_missing_tables_and_settings(books):
    (books.acct / "ISINFO.DBF").unlink()
    _result, data = _read(books)
    assert data["excluded"] == [] and "ISINFO" in data["diagnostics"]["missing_tables"]
    # 没有账套设置时进项税 / 应付照样只在借方以外：进项税借方行这时会被算上（服务端只建议费用科目）
    s001 = next(row for row in data["suppliers"] if row["code"] == "S001")
    assert INPUT_VAT in {row["code"] for row in s001["accounts"]}
    (books.acct / "GLJNLIT.DBF").unlink()
    with pytest.raises(RequestFailed) as caught:
        _read(books)
    assert caught.value.code == "account_tables_missing"


def test_a_result_too_large_is_refused(books, monkeypatch):
    monkeypatch.setitem(MAX_BYTES, "account_history", 10)
    with pytest.raises(RequestFailed) as caught:
        _read(books)
    assert caught.value.code == "too_large"


def test_the_request_thread_knows_how_to_read_the_history():
    assert request_worker.HANDLERS["account_history"] is account_history.read_history


# ── 结果版本 3：商品单入账方式（用户 10-17）──────────────────────────────


def _stock_line(docnum, code, days, people="", **extra):
    return {"DOCNUM": docnum, "SEQNUM": "1", "DOCDAT": _days(days), "STKCOD": code, "PEOPLE": people,
            "TRNQTY": 1.0, "NETVAL": 100.0, **extra}


def _with_stock(books):
    _append(books.acct / "STMAS.DBF", [{"STKCOD": "P01", "STKDES": "กระดาษ A4", "STKTYP": "0"},
                                        {"STKCOD": "SV1", "STKDES": "ค่าบริการ", "STKTYP": "3"}])
    _append(books.acct / "STCRD.DBF", [
        _stock_line("RR-001", "P01", 30, "S001"),  # 库存品：走库存
        _stock_line("RR-002", "SV1", 60, "S001"),  # 只有非库存品的明细、不动库存：记费用
        _stock_line("RR-004", "P01", 120, "S001"),  # 作废的单：不算
        _stock_line("RR-009", "P01", 5, "C999"),  # 单号撞上别家（PEOPLE 不是这家）：不挂
        _stock_line("RR-008", "SV1", 20, "", PHYBAL=5.0),  # 非库存品但动了库存：走库存
    ])


def test_v3_counts_stock_and_service_documents_per_supplier(books):
    _with_stock(books)
    _result, data = _read(books)
    suppliers = {row["code"]: row for row in data["suppliers"]}
    # HP-003 没有明细、RR-002 只有服务明细、RR-009 的明细是别家的：都记费用
    assert suppliers["S001"]["posting"] == {"stock": {"docs": 1, "last": _days(30).isoformat()},
                                            "service": {"docs": 3, "last": _days(5).isoformat()}}
    assert suppliers["S002"]["posting"] == {"stock": {"docs": 1, "last": _days(20).isoformat()},
                                            "service": {"docs": 0, "last": ""}}
    assert data["diagnostics"]["posting"] == "ok" and "stcrd_seconds" in data["diagnostics"]


def test_v3_leaves_out_documents_pearnly_wrote_and_suppliers_without_accounts_still_count(books):
    _with_stock(books)
    _append(books.acct / "APMAS.DBF", [{"SUPCOD": "S003", "SUPNAM": "ร้านวัสดุ", "TAXID": "0105556099999"}])
    _append(books.acct / "APTRN.DBF", [_doc("RR-020", "S003", 3), {**_doc("RR-021", "S003", 2), "USERID": "PEARNLY"}])
    _append(books.acct / "GLJNL.DBF", [_head("RR-020", 3), _head("RR-021", 2)])
    _append(books.acct / "STCRD.DBF", [_stock_line("RR-020", "P01", 3, "S003"), _stock_line("RR-021", "P01", 2, "S003")])
    _result, data = _read(books)
    s003 = next(row for row in data["suppliers"] if row["code"] == "S003")
    # 凭证没有借方行（没有常用科目）也交入账方式；Pearnly 写的 RR-021 不算
    assert (s003["accounts"], s003["documents"]) == ([], 0)
    assert s003["posting"]["stock"] == {"docs": 1, "last": _days(3).isoformat()}


def test_v3_sends_no_posting_when_it_cannot_tell(books, monkeypatch):
    # 存货明细或主档读不到：不交 posting（不猜），其余照旧
    _result, data = _read(books)
    assert data["diagnostics"]["posting"] == "no_stock_master"
    assert all("posting" not in row for row in data["suppliers"])
    _with_stock(books)
    (books.acct / "STMAS.DBF").unlink()
    _result, data = _read(books)
    assert data["diagnostics"]["posting"] == "tables_missing"
    assert all("posting" not in row for row in data["suppliers"]) and data["suppliers"]
    # 时间不够（整件请求剩下的时间里复制、扫不完）：不交（只扫一半会把走库存的单算成记费用）
    _table(books.acct / "STMAS.DBF", S.TABLE_SPECS["STMAS"], [{"STKCOD": "P01", "STKDES": "กระดาษ A4", "STKTYP": "0"}])
    monkeypatch.setattr(account_history, "POSTING_RESERVE_SECONDS", 10 ** 6)
    _result, data = _read(books)
    assert data["diagnostics"]["posting"] == "copy_too_slow"
    assert all("posting" not in row for row in data["suppliers"]) and data["suppliers"]
    found = account_history.collect(books.acct, SINCE, deadline=0.0)
    assert found["diagnostics"]["posting"] == "stcrd_too_slow"
    assert all("posting" not in row for row in found["suppliers"])


def _accounts_of(data):
    return {row["code"]: row["accounts"] for row in data["suppliers"]}


def test_v3_a_torn_stcrd_copy_keeps_the_accounts(books):
    # 复制途中 STCRD 被追加、副本末尾不完整：科目部分照交（v2 能交回的这里也要交回），只是没有 posting（审查 10-17）
    _result, before = _read(books)
    _with_stock(books)
    _append(books.acct / "STCRD.DBF", [_stock_line(f"XX-{n:03d}", "P01", 3, "S001") for n in range(50)])
    path = books.acct / "STCRD.DBF"
    path.write_bytes(path.read_bytes()[:-400])
    result, data = _read(books)
    assert data["version"] == 3 and result.meta["version"] == 3
    assert data["diagnostics"]["posting"] in ("stcrd_unreadable", "stcrd_bad_rows")
    assert all("posting" not in row for row in data["suppliers"])
    assert _accounts_of(data) == _accounts_of(before) and _accounts_of(data)


def test_v3_a_bad_stcrd_row_sends_no_posting(books, monkeypatch):
    # 读不出的坏行可能正是某张单唯一的库存行：整份不交，不把它算成记费用
    _with_stock(books)
    real = S.stcrd_no_stock_impact

    def broken(rec):
        if str(rec["DOCNUM"]).strip() == "RR-001":
            raise ValueError("bad bytes")
        return real(rec)

    monkeypatch.setattr(S, "stcrd_no_stock_impact", broken)
    monkeypatch.setattr(account_history, "STOCK_TYPE", "never")  # 让每行都走到读坏的那一步
    _result, data = _read(books)
    assert data["diagnostics"]["posting"] == "stcrd_bad_rows"
    assert data["diagnostics"]["bad_rows"]["STCRD"] == 1
    assert all("posting" not in row for row in data["suppliers"]) and _accounts_of(data)


def test_v3_a_locked_stcrd_keeps_the_accounts(books, monkeypatch):
    # STCRD 被独占复制不了：不等、不让整件失败，科目照交
    _with_stock(books)
    real = open

    def locked(path, *args, **kwargs):
        if Path(path).name.upper() == "STCRD.DBF" and "pearnly-posting-" not in str(path):
            raise PermissionError(path)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(account_history, "open", locked, raising=False)
    _result, data = _read(books)
    assert data["diagnostics"]["posting"] == "copy_busy"
    assert all("posting" not in row for row in data["suppliers"]) and _accounts_of(data)
