"""开始前四题真跨边（A3 / P2-2）：同一本合成账套，服务端用 CC 桌面版主档读回（``express_masters.collect``）得到的资料按
``express_rules.push.book_questions`` 出题；CC 桌面版拿同一张引导模式的单真写。两边结论逐题一致：

- 服务端说「要问 X」⇔ CC 写时停在 X 的码（负库存 → ``STOCK_INSUFFICIENT``、单位 → ``UNIT_CHOICE``、成本 → ``COST_BASIS_CHOICE``、
  现金科目 → ``CASH_ACCOUNT_CHOICE``），问的是同几行；
- 服务端说「不用问」⇔ CC 照写成功；
- 人答了（:func:`book_questions.apply` 写进载荷）→ CC 照写成功，不再停。

主档读回现在还不带 ``NEGAUTH`` / ``CHKNEGLEV`` / 标准成本（CC 桌面版下一版补，A3 评估），这里按那一版会报的样子从账套补上，
补法写在 :func:`masters`。
"""
from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

import dbf
import pytest

from account_rules_fixture import glacc, seed
from companion import dbf_schema as S
from companion import express_masters
from test_guided_payload import _set  # noqa: F401
from test_stock_location import _buy, _item, _no_runner, _purchase, _sale, _sell, acct  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "pearnly_core"))
from pearnly_core.express_rules.push import book_questions as B  # noqa: E402

CASH, PETTY, BANK = "11-01-01-00", "11-01-03-00", "11-01-02-00"
NEW = "น้ำแข็งแห้ง"
GUIDED = {"payload_version": 21, "guided": True}
CODES = {B.NEGATIVE: "STOCK_INSUFFICIENT", B.UNIT: "UNIT_CHOICE", B.COST: "COST_BASIS_CHOICE", B.CASH: "CASH_ACCOUNT_CHOICE"}


def _isinfo(acct, **fields):
    """改账套设置的几栏（ISINFO 只有一行；没有的栏照 ``account_rules_fixture.isinfo`` 的形状重建）。"""
    table = dbf.Table(str(acct / "ISINFO.DBF"), codepage=S.CODEPAGE)
    table.open(dbf.READ_ONLY)
    try:
        current = {name: str(table[0][name] or "").strip() for name in table.field_names}
    finally:
        table.close()
    from account_rules_fixture import isinfo

    isinfo(acct, **{**{k: v for k, v in current.items() if k != "THINAM"}, **fields})


def masters(acct):
    """服务端拿到的账套资料 = CC 桌面版主档读回，另补下一版要报的三样（账套 NEGAUTH / CHKNEGLEV、物料标准成本 STNPR）。"""
    data, _diag = express_masters.collect(acct)
    table = dbf.Table(str(acct / "ISINFO.DBF"), codepage=S.CODEPAGE)
    table.open(dbf.READ_ONLY)
    try:
        names = {name.upper() for name in table.field_names}
        row = table[0]
        data["settings"]["negauth"] = str(row["NEGAUTH"] or "").strip() if "NEGAUTH" in names else ""
        data["settings"]["chkneglev"] = str(row["CHKNEGLEV"] or "").strip() if "CHKNEGLEV" in names else ""
    finally:
        table.close()
    table = dbf.Table(str(acct / "STMAS.DBF"), codepage=S.CODEPAGE)
    table.open(dbf.READ_ONLY)
    try:
        cost = {str(r["STKCOD"]).strip(): float(r["STNPR"] or 0) for r in S.live_rows(table)}
    finally:
        table.close()
    for product in data["products"]:
        product["std_cost"] = str(cost.get(product["code"], 0))
    return data


def _new(mode, qty=2, **extra):
    return {"name": NEW, "code": "", "qty": str(qty), "unit_price": "30", "amount": f"{30 * qty:.2f}", "unit": "ถัง",
            "item_mode": mode, **extra}


def _write(acct, payload):
    return (_sell if payload.get("direction") == "sales" else _buy)(acct, payload)


def _agree(acct, payload, mode=B.DIRECT):
    """服务端出题与 CC 真写的结论一致；回服务端的结论。"""
    found = B.questions(B.facts_of(masters(acct)), payload, mode=mode)
    res = _write(acct, deepcopy(payload))
    first = (B.NEGATIVE if found["facts"] else found["needed"][0]["kind"]) if (found["facts"] or found["needed"]) else None
    if first is None:
        assert res.ok, (res.error_code, res.error)
    else:
        assert not res.ok and res.error_code == CODES[first], (first, res.error_code, res.error)
        if first in (B.UNIT, B.COST):
            asked = [line for q in found["needed"] if q["kind"] == first for line in q["lines"]]
            assert asked == [row["line"] for row in res.choice["lines"]]
    return found


def _decided(found, picks):
    decided = {kind: dict(values) for kind, values in found["auto"].items()}
    for q in found["needed"]:
        if q["kind"] in picks:
            decided.setdefault(q["kind"], {})[q["subject"]] = picks[q["kind"]]
    return decided


def test_a_purchase_unit_that_is_not_the_items_is_asked_by_both_and_the_answer_writes(acct):
    payload = _purchase([_item("ICE01", 2, 20.0, "stock_item", unit="กล่อง")], **GUIDED)
    found = _agree(acct, payload)
    assert [q["kind"] for q in found["needed"]] == [B.UNIT]
    assert [opt["code"] for opt in found["needed"][0]["options"]] == ["ถง", "factor"]
    res = _write(acct, B.apply(deepcopy(payload), _decided(found, {B.UNIT: "factor:6"})))
    assert res.ok, (res.error_code, res.error)


def test_units_the_item_has_and_new_items_with_a_known_unit_are_not_asked_by_either(acct):
    _agree(acct, _purchase([_item("ICE01", 2, 20.0, "stock_item", unit="ถัง")], **GUIDED))
    _agree(acct, _purchase([_new("stock_item")], ref_no="PO-2", **GUIDED))


def test_a_new_item_with_an_unknown_unit_is_asked_from_the_unit_table(acct):
    payload = _purchase([_new("stock_item", unit="โหล")], **GUIDED)
    found = _agree(acct, payload)
    assert "ขด" in [opt["code"] for opt in found["needed"][0]["options"]]
    assert _write(acct, B.apply(deepcopy(payload), _decided(found, {B.UNIT: "ขด"}))).ok


def test_a_new_item_sold_in_a_book_without_negative_stock_then_its_cost(acct):
    _isinfo(acct, NEGALLOW="N")
    payload = _sale([_new("stock_sale")], **GUIDED)
    found = _agree(acct, payload)
    assert [q["kind"] for q in found["needed"]] == [B.NEGATIVE]
    allowed = B.apply(deepcopy(payload), _decided(found, {B.NEGATIVE: B.ALLOW}))
    again = _agree(acct, allowed)  # 负库存过了，两边都停在成本
    assert [q["kind"] for q in again["needed"]] == [B.COST]
    assert _write(acct, B.apply(allowed, _decided(again, {B.COST: "given:4"}))).ok


def test_an_existing_item_short_is_a_fact_for_both(acct):
    _isinfo(acct, NEGALLOW="N")
    found = _agree(acct, _sale([_item("ICE01", 6, 60.0, "stock_sale")], **GUIDED))
    assert found["facts"][0]["rows"][0]["need"] == "6" and found["facts"][0]["rows"][0]["have"] == "5"
    _agree(acct, _sale([_item("ICE01", 5, 50.0, "stock_sale")], ref_no="IV-2", **GUIDED))


@pytest.mark.parametrize("level", ["0", "2"])
def test_whole_book_and_per_warehouse_levels_agree(acct, level):
    _isinfo(acct, NEGALLOW="N", CHKNEGLEV=level)
    _agree(acct, _sale([_item("ICE01", 3, 30.0, "stock_sale", loccod="02")], **GUIDED))


def test_a_sale_without_a_cost_basis_in_a_book_that_allows_negative(acct):
    _set(acct, "STMAS", "STKCOD", "ICE02", STNPR=0.0)
    payload = _sale([_item("ICE01", 1, 30.0, "stock_sale"), _item("ICE02", 1, 30.0, "stock_sale")], **GUIDED)
    found = _agree(acct, payload)
    assert [q["lines"] for q in found["needed"]] == [[2]]
    assert _write(acct, B.apply(deepcopy(payload), _decided(found, {B.COST: "none"}))).ok
    _set(acct, "STMAS", "STKCOD", "ICE02", STNPR=7.0)
    _agree(acct, _sale([_item("ICE02", 1, 30.0, "stock_sale")], ref_no="IV-3", **GUIDED))


def test_a_cash_sale_whose_cash_account_cannot_be_posted(acct):
    _isinfo(acct, ACCNUM00="11-01-00-00")
    glacc(acct, (CASH, "เงินสด"), (PETTY, "เงินสดย่อย"), (BANK, "ธนาคาร"))
    seed(acct, "GLACC", [{"ACCNUM": "11-01-00-00", "ACCNAM": "เงินสดและเงินฝาก", "ACCTYP": "1"}])
    payload = _sale([_item("ICE01", 1, 30.0, "stock_sale")], doctype="HS", **GUIDED)
    found = _agree(acct, payload)
    [question] = found["needed"]
    assert question["kind"] == B.CASH and [opt["code"] for opt in question["options"]][:2] == [CASH, PETTY]
    assert _write(acct, B.apply(deepcopy(payload), _decided(found, {B.CASH: PETTY}))).ok
