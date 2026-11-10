# -*- coding: utf-8 -*-
"""按需请求 express_doc_check（录过的票先核账本，用户 11-10）：只读单头，回每张点名的单在账本里还在不在。

在且没作废 → present；打了删除标记 / 作废（DOCSTAT C）/ 单头里没有 → gone；表不在、方向不认识 → unknown。
"""

from __future__ import annotations

import gzip
import json
import types

import dbf
import pytest

import _synth_account
from companion import dbf_schema as S
from companion import express_doc_check, request_contract, request_worker
from companion.request_contract import RequestFailed


def _append(path, rows, deleted=()):
    table = dbf.Table(str(path), codepage=S.CODEPAGE)
    table.open(dbf.READ_WRITE)
    try:
        for row in rows:
            table.append(row)
            if row.get("DOCNUM") in deleted:
                dbf.delete(table[-1])
    finally:
        table.close()


@pytest.fixture
def book(tmp_path):
    acct = _synth_account.build(tmp_path / "70EXP" / "test")
    _append(acct / "APTRN.DBF", [{"DOCNUM": "IV0001", "DOCSTAT": "N"}, {"DOCNUM": "IV0002", "DOCSTAT": "C"},
                                 {"DOCNUM": "IV0003", "DOCSTAT": "N"},
                                 {"DOCNUM": "IV0004", "DOCSTAT": "N"}, {"DOCNUM": "IV0004", "DOCSTAT": "C"}],
            deleted=("IV0003",))
    _append(acct / "ARTRN.DBF", [{"DOCNUM": "INV-77", "DOCSTAT": "N"}])
    return acct


def _items(*rows):
    return [{"doc_id": f"D{index}", "docnum": docnum, "direction": direction}
            for index, (docnum, direction) in enumerate(rows, start=1)]


def test_each_named_document_is_present_gone_or_unknown(book):
    out = express_doc_check.check(book, _items(("IV0001", "purchase"), ("IV0002", "purchase"), ("IV0003", "purchase"),
                                               ("IV9999", "purchase"), ("iv0004 ", "purchase"), ("INV-77", "sales"),
                                               ("IV0001", "sales"), ("IV0001", "stock")))
    assert [row["state"] for row in out] == ["present", "gone", "gone", "gone", "present", "present", "gone", "unknown"]
    assert [row["doc_id"] for row in out] == [f"D{n}" for n in range(1, 9)]
    assert out[4]["docnum"] == "iv0004 "  # 原样带回服务端给的单号


def test_a_missing_head_table_is_unknown_not_gone(book):
    (book / "ARTRN.DBF").unlink()
    out = express_doc_check.check(book, _items(("INV-77", "sales"), ("IV0001", "purchase")))
    assert [row["state"] for row in out] == ["unknown", "present"]


def test_the_handler_reads_the_chosen_account_set_and_returns_gzip_json(book):
    cfg = types.SimpleNamespace(account_set=str(book), account_dir=str(book), express_root="", authorized_roots=())
    result = express_doc_check.check_docs(cfg, {"ledger": "express", "account_set": str(book),
                                                "items": _items(("IV0001", "purchase"))})
    assert json.loads(gzip.decompress(result.content)) == {
        "items": [{"doc_id": "D1", "docnum": "IV0001", "state": "present"}]}
    assert result.meta == {"format": "json.gz"}


@pytest.mark.parametrize("params", [
    {"items": []},
    {"items": _items(*[("IV0001", "purchase")] * 51)},
    {"items": [{"doc_id": "D1", "docnum": " ", "direction": "purchase"}]},
    {"items": [{"doc_id": "D1", "docnum": "IV1", "direction": "purchase", "extra": "x"}]},
    {"items": _items(("IV0001", "purchase")), "ledger": "mrerp"},
    {"items": _items(("IV0001", "purchase")), "unknown": 1},
])
def test_bad_params_are_refused(book, params):
    with pytest.raises(RequestFailed) as error:
        express_doc_check.check_docs(types.SimpleNamespace(), params)
    assert error.value.code == "params_invalid"


def test_the_kind_is_declared_and_handled():
    assert "express_doc_check" in request_contract.KINDS
    assert request_worker.HANDLERS["express_doc_check"] is express_doc_check.check_docs
    assert request_contract.LEASE_SECONDS["express_doc_check"] == 120
