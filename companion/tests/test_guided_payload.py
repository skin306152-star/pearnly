# -*- coding: utf-8 -*-
"""载荷 21「引导录入」（约定：docs/work/2026-11-12-cc-guided-entry-express-coverage.md 第 8 节；``guided``）。合成账套 · 过真写盘器。

- 版本：21 在支持集里；版本 ≤ 20 的载荷一个新键都不读（带了也与没带逐字节相同）。
- 新键的类型与取值不合格 → ``PAYLOAD_INVALID``（到期日早于单据日带子码 ``INVALID_DUE_DATE``）；值不在这本账套里 → 各自的拒收码，不回落默认。
- 给了就照写：``unit_code``（配合 ``factor`` 换算）、``acccod`` / ``stkgrp`` / ``negallow``（新建库存品）、``cost_choice`` / ``cost``（出库成本）、
  ``due_date_be``（到期日与账期天数）、``cash_account``（现购贷它、现销借它）、对方块 ``type`` / ``payterm``。
- 引导模式（``guided: true``）：原来「缺了就用默认值写」的情形整单不写，回 ``UNIT_CHOICE`` / ``COST_BASIS_CHOICE`` / ``CASH_ACCOUNT_CHOICE``
  并带候选（回执 ``meta.choice``）；不带 ``guided`` 的同一张单照旧写。负库存先核，过了才问成本。
- 缺陷 G-B1：新建库存品四个单位同码、三个换算系数写 1（不抄模板物料的）。
"""

from __future__ import annotations

import datetime as dt

import dbf
import pytest

from account_rules_fixture import glacc, isinfo, seed
from companion import dbf_schema as S
from companion import guided
from companion import payload_contract
from companion import product_master as PM
from companion import stock_sale as SS
from companion import worker
from companion.purchase_adapter import build_purchase_entry, validate_purchase_payload
from companion.sales_adapter import build_sales_entry, validate_sales_payload
from test_stock_location import (COGS_ACC, INVENTORY_ACC, _append, _buy, _item, _no_runner, _purchase,  # noqa: F401
                                 _rows, _sale, _sell, _state, acct)

CASH, BANK, HEADER_ACC = "11-01-01-00", "11-01-02-00", "11-01-00-00"
V21 = {"payload_version": 21}
GUIDED = {"payload_version": 21, "guided": True}
NEW = "น้ำแข็งแห้ง"


def _new(mode, qty=2, amount=60.0, **extra):
    return {"name": NEW, "code": "", "qty": str(qty), "unit_price": str(amount / qty), "amount": f"{amount:.2f}",
            "unit": "ถัง", "item_mode": mode, **extra}


def _set(acct, table, key, code, **fields):
    handle = dbf.Table(str(acct / f"{table}.DBF"), codepage=S.CODEPAGE)
    handle.open(dbf.READ_WRITE)
    try:
        for rec in S.live_rows(handle):
            if str(rec[key]).strip() == code:
                with rec as row:
                    for name, value in fields.items():
                        row[name] = value
    finally:
        handle.close()


def _cash_book(acct, cash=CASH):
    isinfo(acct, MAINLOC="01", ACCNUM00=cash)
    glacc(acct, (CASH, "เงินสด"), (BANK, "ธนาคาร"))
    seed(acct, "GLACC", [{"ACCNUM": HEADER_ACC, "ACCNAM": "เงินสดและเงินฝาก", "ACCTYP": "1"}])


def _journal(acct, docnum):
    return [(str(r["ACCNUM"]).strip(), str(r["TRNTYP"]).strip(), round(float(r["AMOUNT"]), 2))
            for r in _rows(acct, "GLJNLIT", VOUCHER=docnum)]


# ---------------- 版本与旧任务 ----------------


def test_version_21_is_supported_and_the_key_lists_are_fixed():
    assert payload_contract.GUIDED_PAYLOAD_VERSION == 21 in payload_contract.SUPPORTED_PAYLOAD_VERSIONS
    assert payload_contract.check_payload_version({"payload_version": 21}) is None
    assert payload_contract.GUIDED_KEYS == {
        "header": ("guided", "due_date_be", "cash_account"),
        "line": ("unit_code", "acccod", "stkgrp", "cost_choice", "cost", "negallow"),
        "party": ("type", "payterm"),
    }
    assert (guided.HEADER_KEYS, guided.LINE_KEYS, guided.PARTY_KEYS) == tuple(payload_contract.GUIDED_KEYS.values())


def test_an_older_payload_reads_none_of_the_new_keys(acct):
    """版本 ≤ 20 的载荷带了新键也不读：解析出来的写入计划与没带的逐字段相同，写进账套的每一张表逐字节相同。"""
    stray_line = {"unit_code": "ขด", "acccod": "NOPE", "stkgrp": "??", "cost_choice": "given", "cost": "9", "negallow": "N"}
    for version in ({}, {"payload_version": 20}):
        plain = _sale([_item("ICE01", 2, 60.0, "stock_sale")], **version)
        stray = _sale([_item("ICE01", 2, 60.0, "stock_sale", **stray_line)], guided=True, due_date_be="690101",
                      cash_account="NOPE", **version)
        stray["customer"].update(type="ZZ", payterm=45)
        assert validate_sales_payload(stray, allowed=("DATAT",)).ok
        assert build_sales_entry(stray) == build_sales_entry(plain)
        bought = _purchase([_item("ICE01", 2, 20.0, "stock_item")], **version)
        stray_buy = _purchase([_item("ICE01", 2, 20.0, "stock_item", **stray_line)], guided=True, due_date_be="690101", **version)
        stray_buy["supplier"].update(type="ZZ", payterm=45)
        assert build_purchase_entry(stray_buy) == build_purchase_entry(bought)
    assert not guided.active({"payload_version": 20}) and guided.active(V21) and not guided.guided(V21)
    assert guided.guided(GUIDED) and not guided.guided({"guided": True})


def test_an_older_payload_with_stray_keys_writes_the_same_bytes(acct, tmp_path):
    import shutil

    twin = tmp_path / "twin"
    shutil.copytree(acct, twin)
    plain = _sale([_item("ICE01", 2, 60.0, "stock_sale")])
    stray = _sale([_item("ICE01", 2, 60.0, "stock_sale", unit_code="ขด", cost_choice="given", cost="9")], guided=True,
                  due_date_be="690101")
    first, second = _sell(acct, plain), WS_sell(twin, stray)
    assert first.ok and second.ok and first.line_modes == second.line_modes
    assert _state(acct) == _state(twin)


def WS_sell(folder, payload):
    from companion import dbf_sales

    return dbf_sales.write_sale(payload, folder, allowed_sets=("DATAT",), selected_dir=str(folder), make_backup=False)


# ---------------- 类型与取值 ----------------


@pytest.mark.parametrize("change, sub_code", [
    ({"guided": "yes"}, "PAYLOAD_INVALID"),
    ({"due_date_be": "690230"}, "PAYLOAD_INVALID"),  # 没有这一天
    ({"due_date_be": "690319"}, "INVALID_DUE_DATE"),  # 早于单据日 690320
    ({"cash_account": CASH}, "PAYLOAD_INVALID"),  # 赊销不带现金科目
])
def test_bad_header_keys_refuse_the_credit_sale(acct, change, sub_code):
    before = _state(acct)
    payload = _sale([_item("ICE01", 1, 30.0, "stock_sale")], **V21, **change)
    assert validate_sales_payload(payload, allowed=("DATAT",)).error_code == sub_code
    res = _sell(acct, payload, make_backup=True)
    assert not res.ok and res.error_code == "PAYLOAD_INVALID" and res.error.startswith(sub_code)
    assert _state(acct) == before


def test_header_keys_only_go_on_the_documents_they_belong_to(acct):
    cash_sale = _sale([_item("ICE01", 1, 30.0, "stock_sale")], doctype="HS", **V21)
    assert validate_sales_payload({**cash_sale, "due_date_be": "690401"}, allowed=("DATAT",)).error_code == "PAYLOAD_INVALID"
    assert validate_sales_payload({**cash_sale, "cash_account": ""}, allowed=("DATAT",)).error_code == "PAYLOAD_INVALID"
    assert validate_sales_payload({**cash_sale, "cash_account": BANK}, allowed=("DATAT",)).ok
    credit_buy = _purchase([_item("ICE01", 1, 10.0, "stock_item")], **V21)
    assert validate_purchase_payload({**credit_buy, "cash_account": CASH}, allowed=("DATAT",)).error_code == "PAYLOAD_INVALID"
    assert validate_purchase_payload({**credit_buy, "due_date_be": "690114"}, allowed=("DATAT",)).error_code == "INVALID_DUE_DATE"
    assert validate_purchase_payload({**credit_buy, "due_date_be": "690115"}, allowed=("DATAT",)).ok  # 当天到期可以
    assert validate_purchase_payload({**credit_buy, "doctype": "HP", "due_date_be": "690201"},
                                     allowed=("DATAT",)).error_code == "PAYLOAD_INVALID"


@pytest.mark.parametrize("line", [
    {"unit_code": ""}, {"acccod": 5}, {"stkgrp": " "}, {"negallow": "A"}, {"negallow": None},
    {"cost_choice": "maybe"}, {"cost_choice": "given"}, {"cost_choice": "given", "cost": "-1"},
    {"cost_choice": "given", "cost": 5}, {"cost_choice": "given", "cost": "abc"},
])
def test_bad_line_keys_refuse_the_sale(acct, line):
    payload = _sale([_item("ICE01", 1, 30.0, "stock_sale", **line)], **V21)
    res = _sell(acct, payload)
    assert not res.ok and res.error_code == "PAYLOAD_INVALID", (line, res.error_code, res.error)


def test_cost_choice_only_goes_on_a_stock_line_of_a_sale(acct):
    assert validate_sales_payload(_sale([_item("ICE01", 1, 30.0, "stock_sale", cost_choice="none")], **V21), allowed=("DATAT",)).ok
    assert validate_sales_payload(_sale([_item("ICE01", 1, 30.0, "stock_sale", cost_choice="given", cost="0")], **V21),
                                  allowed=("DATAT",)).ok
    buy = _purchase([_item("ICE01", 1, 10.0, "stock_item", cost_choice="none")], **V21)
    assert validate_purchase_payload(buy, allowed=("DATAT",)).error_code == "PAYLOAD_INVALID"


@pytest.mark.parametrize("block", [{"type": ""}, {"type": 4}, {"payterm": -1}, {"payterm": 1000}, {"payterm": "30"},
                                   {"payterm": True}, {"payterm": 1.5}])
def test_bad_party_keys_refuse_the_document(acct, block):
    sale = _sale([_item("ICE01", 1, 30.0, "stock_sale")], **V21)
    sale["customer"].update(block)
    assert validate_sales_payload(sale, allowed=("DATAT",)).error_code == "PAYLOAD_INVALID"
    buy = _purchase([_item("ICE01", 1, 10.0, "stock_item")], **V21)
    buy["supplier"].update(block)
    assert validate_purchase_payload(buy, allowed=("DATAT",)).error_code == "PAYLOAD_INVALID"


# ---------------- 到期日 R2 ----------------


def test_the_due_date_of_a_credit_document_follows_the_payload(acct):
    """赊购 / 赊销的到期日写载荷给的（盖过对方档账期）；没给照旧 = 单据日 + 对方档账期（合成账套没有档 → 单据日）。"""
    sale = _sell(acct, _sale([_item("ICE01", 1, 30.0, "stock_sale")], due_date_be="690419", **V21))
    assert sale.ok, sale.error
    assert _rows(acct, "ARTRN", DOCNUM=sale.docnum)[0]["DUEDAT"] == dt.date(2026, 4, 19)
    plain = _sell(acct, _sale([_item("ICE01", 1, 30.0, "stock_sale")], ref_no="IV-2"))
    assert _rows(acct, "ARTRN", DOCNUM=plain.docnum)[0]["DUEDAT"] == dt.date(2026, 3, 20)
    buy = _buy(acct, _purchase([_item("ICE01", 1, 10.0, "stock_item")], due_date_be="690214", **V21))
    assert buy.ok, buy.error
    assert _rows(acct, "APTRN", DOCNUM=buy.docnum)[0]["DUEDAT"] == dt.date(2026, 2, 14)


def test_the_term_days_are_the_due_date_minus_the_document_date(acct):
    """账期天数 PAYTRM = 到期日 − 单据日（原生：到期日 = 单据日 + 账期）。合成表没有这一栏，补上再写。"""
    for table in ("ARTRN", "APTRN"):
        handle = dbf.Table(str(acct / f"{table}.DBF"), codepage=S.CODEPAGE)
        handle.open(dbf.READ_WRITE)
        handle.add_fields("PAYTRM N(3,0)")
        handle.close()
    sale = _sell(acct, _sale([_item("ICE01", 1, 30.0, "stock_sale")], due_date_be="690419", **V21))
    assert _rows(acct, "ARTRN", DOCNUM=sale.docnum)[0]["PAYTRM"] == 30
    buy = _buy(acct, _purchase([_item("ICE01", 1, 10.0, "stock_item")], due_date_be="690115", **V21))
    assert _rows(acct, "APTRN", DOCNUM=buy.docnum)[0]["PAYTRM"] == 0


# ---------------- 现金科目 A13 / A2 ----------------


def test_a_cash_sale_debits_the_chosen_account_and_a_cash_purchase_credits_it(acct):
    _cash_book(acct)
    sale = _sell(acct, _sale([_item("ICE01", 1, 30.0, "stock_sale")], doctype="HS", cash_account=BANK, **V21))
    assert sale.ok, sale.error
    assert (BANK, "0", 32.1) in _journal(acct, sale.docnum) and CASH not in {row[0] for row in _journal(acct, sale.docnum)}
    plain = _sell(acct, _sale([_item("ICE01", 1, 30.0, "stock_sale")], doctype="HS", ref_no="HS-2"))
    assert (CASH, "0", 32.1) in _journal(acct, plain.docnum)  # 没给：照旧借账套现金科目
    buy = _buy(acct, _purchase([_item("ICE01", 1, 10.0, "stock_item")], doctype="HP", cash_account=BANK, **V21))
    assert buy.ok, buy.error
    assert (BANK, "1", 10.7) in _journal(acct, buy.docnum)


def test_a_cash_account_that_cannot_be_posted_to_is_refused(acct):
    _cash_book(acct)
    before = _state(acct)
    for account in (HEADER_ACC, "99-99-99-99"):
        res = _sell(acct, _sale([_item("ICE01", 1, 30.0, "stock_sale")], doctype="HS", cash_account=account, **V21),
                    make_backup=True)
        assert not res.ok and res.error_code == "ACCOUNT_NOT_POSTABLE" and res.choice is None
        res = _buy(acct, _purchase([_item("ICE01", 1, 10.0, "stock_item")], doctype="HP", cash_account=account, **V21),
                   make_backup=True)
        assert not res.ok and res.error_code == "ACCOUNT_NOT_POSTABLE"
    assert _state(acct) == before


def test_guided_entry_asks_for_a_cash_account_when_the_books_own_cannot_be_posted_to(acct):
    """账套现金科目不能记账：以前静默保留载荷里的应收 / 应付科目（现销却记了应收）。引导模式整单不写、回 CASH_ACCOUNT_CHOICE；
    不带 guided 的照旧写；给了 cash_account 的照写。"""
    _cash_book(acct, cash=HEADER_ACC)
    before = _state(acct)
    for write, payload in ((_sell, _sale([_item("ICE01", 1, 30.0, "stock_sale")], doctype="HS", **GUIDED)),
                           (_buy, _purchase([_item("ICE01", 1, 10.0, "stock_item")], doctype="HP", **GUIDED))):
        res = write(acct, payload, make_backup=True)
        assert not res.ok and res.error_code == "CASH_ACCOUNT_CHOICE", (res.error_code, res.error)
        assert res.choice == {"code": "CASH_ACCOUNT_CHOICE", "cash_account": HEADER_ACC}
        assert worker._outcome_for(res.error_code) == "needs_review"
        assert worker._push_meta(res, "sales", str(acct))["choice"] == res.choice
    assert _state(acct) == before
    assert _sell(acct, _sale([_item("ICE01", 1, 30.0, "stock_sale")], doctype="HS", **V21)).ok  # 不带 guided：照旧
    answered = _sell(acct, _sale([_item("ICE01", 1, 30.0, "stock_sale")], doctype="HS", ref_no="HS-3", cash_account=BANK, **GUIDED))
    assert answered.ok and (BANK, "0", 32.1) in _journal(acct, answered.docnum)
    # 赊销不问现金科目
    assert _sell(acct, _sale([_item("ICE01", 1, 30.0, "stock_sale")], ref_no="IV-9", **GUIDED)).ok


# ---------------- 单位 A5 / A10 / B5 ----------------


def _pack(acct):
    """ICE01：库存单位 ถง，采购包装单位 กล = 12 ถง。"""
    _set(acct, "STMAS", "STKCOD", "ICE01", PQUCOD="กล", PFACTOR=12.0, SQUCOD="ถง", SFACTOR=1.0, CQUCOD="ถง", CFACTOR=1.0)


def test_a_line_is_written_in_the_unit_code_it_carries(acct):
    """票面单位文字解析不出（「ลัง」），人在引导卡上选了这件物料的包装单位 กล：照 Express 主档系数写（TQUCOD กล、TFACTOR 12、库存 +24）。"""
    _pack(acct)
    res = _buy(acct, _purchase([_item("ICE01", 2, 240.0, "stock_item", unit="ลัง", unit_code="กล")], **GUIDED))
    assert res.ok, res.error
    row = _rows(acct, "STCRD", DOCNUM=res.docnum)[0]
    assert (row["TQUCOD"].strip(), row["TFACTOR"], row["TRNQTY"], row["XTRNQTY"]) == ("กล", 12.0, 2.0, 24.0)
    assert _rows(acct, "STMAS", STKCOD="ICE01")[0]["TOTBAL"] == 29.0
    # 不是这件物料的单位，但给了换算率（1 ขวด = 0.5 库存单位）：换成库存单位写
    res = _sell(acct, _sale([_item("ICE01", 4, 120.0, "stock_sale", unit="ขวด", unit_code="ขด", factor="0.5")], **GUIDED))
    assert res.ok, res.error
    row = _rows(acct, "STCRD", DOCNUM=res.docnum)[0]
    assert (row["TQUCOD"].strip(), row["TFACTOR"], row["XTRNQTY"]) == ("ถง", 1.0, 2.0)


def test_a_unit_code_that_is_not_in_the_unit_table_is_refused(acct):
    before = _state(acct)
    res = _buy(acct, _purchase([_item("ICE01", 2, 20.0, "stock_item", unit_code="ZZ")], **V21), make_backup=True)
    assert not res.ok and res.error_code == "UNIT_CODE_NOT_FOUND" and "line 1" in res.error
    assert worker._outcome_for(res.error_code) == "needs_review"
    assert _state(acct) == before


def test_a_unit_that_is_not_the_items_asks_for_a_choice_in_guided_entry(acct):
    """已有物料、票面单位（กล่อง → กล）不是它的任何一个单位、又没有换算率：以前按库存单位、系数 1 写（「箱」按「袋」入库）。
    引导模式整单不写，带回这件物料的各单位与系数、账套单位表；不带 guided 的照旧写。"""
    before = _state(acct)
    line = _item("ICE01", 2, 20.0, "stock_item", unit="กล่อง")
    res = _buy(acct, _purchase([line, _item("ICE02", 1, 5.0, "stock_item")], **GUIDED), make_backup=True)
    assert not res.ok and res.error_code == "UNIT_CHOICE", (res.error_code, res.error)
    wanted = {"line": 1, "name": "ICE01", "stkcod": "ICE01", "units": [{"code": "ถง", "factor": "1", "role": "stock"}]}
    assert res.choice == {"code": "UNIT_CHOICE", **wanted, "lines": [wanted],
                          "unit_table": ["ลต", "ถง", "ชน", "กง", "กล", "คง", "ขด"]}
    assert _state(acct) == before
    assert worker._push_meta(res, "purchase", str(acct))["choice"]["lines"][0]["stkcod"] == "ICE01"
    plain = _buy(acct, _purchase([line], **V21))
    assert plain.ok and _rows(acct, "STCRD", DOCNUM=plain.docnum)[0]["TQUCOD"].strip() == "ถง"  # 现状：按库存单位
    # 票面没有单位的行不问（按库存单位写）；带换算率的不问
    assert _buy(acct, _purchase([_item("ICE01", 1, 10.0, "stock_item", unit="")], ref_no="PO-3", **GUIDED)).ok
    assert _buy(acct, _purchase([_item("ICE01", 1, 10.0, "stock_item", unit="กล่อง", factor="6")], ref_no="PO-4", **GUIDED)).ok


def test_an_explicit_unit_code_never_falls_back_even_without_guided(acct):
    res = _sell(acct, _sale([_item("ICE01", 1, 30.0, "stock_sale", unit_code="ขด")], **V21))
    assert not res.ok and res.error_code == "UNIT_CHOICE"
    assert res.choice["stkcod"] == "ICE01" and res.choice["units"][0] == {"code": "ถง", "factor": "1", "role": "stock"}


def test_a_new_stock_item_takes_the_unit_code_or_asks_for_one(acct):
    """新建库存品：给了单位码 → 四个单位列都写它、不算猜的；引导模式下单位解析不出（以前取账套众数、只在回执标 unit_guessed）→ UNIT_CHOICE；
    不带 guided 照旧取众数并在回执标 unit_guessed。"""
    before = _state(acct)
    res = _buy(acct, _purchase([_new("stock_item", unit="โหล")], **GUIDED), make_backup=True)
    assert not res.ok and res.error_code == "UNIT_CHOICE"
    assert res.choice["lines"] == [{"line": 1, "name": NEW, "stkcod": "", "units": []}] and "ขด" in res.choice["unit_table"]
    assert _state(acct) == before
    res = _buy(acct, _purchase([_new("stock_item", unit="โหล", unit_code="ขด")], **GUIDED))
    assert res.ok, res.error
    made = _rows(acct, "STMAS", STKDES=NEW)[0]
    assert {made[field].strip() for field in ("QUCOD", "PQUCOD", "SQUCOD", "CQUCOD")} == {"ขด"}
    assert res.line_modes[0]["created"] is True and res.line_modes[0]["unit_guessed"] is False
    assert res.line_modes[0]["name"] == NEW  # 回执带品名与新建标记，两产品显示「{品名}：新物料」
    guessed = _buy(acct, _purchase([_new("stock_item", unit="โหล", name="ของอีกอย่าง")], ref_no="PO-5", **V21))
    assert guessed.ok and guessed.line_modes[0]["unit_guessed"] is True and guessed.line_modes[0]["unit_code"] == "ถง"


# ---------------- 新建库存品的科目组、分类 A10 ----------------


def _groups(acct):
    seed(acct, "ISTAB", [{"TABTYP": "22", "TYPCOD": "FG", "TYPDES": "สินค้า"}, {"TABTYP": "22", "TYPCOD": "HW", "TYPDES": "HW"}])
    seed(acct, "ISACC", [{"ACCTYP": "S", "ACCCOD": "ST02", "METHOD": "A", "ACCNUM01": "11-04-04-00", "ACCNUM02": COGS_ACC},
                         {"ACCTYP": "S", "ACCCOD": "EXP", "METHOD": "A", "ACCNUM01": "52-08-00-00", "ACCNUM02": COGS_ACC}])
    _set(acct, "STMAS", "STKCOD", "ICE01", STKGRP="FG")


def test_a_new_stock_item_takes_the_group_and_class_of_its_line(acct):
    _groups(acct)
    res = _buy(acct, _purchase([_new("stock_item", acccod="ST02", stkgrp="HW")], **V21))
    assert res.ok, res.error
    made = _rows(acct, "STMAS", STKDES=NEW)[0]
    assert (made["ACCCOD"].strip(), made["STKGRP"].strip()) == ("ST02", "HW")
    plain = _buy(acct, _purchase([_new("stock_item", name="ของอีกอย่าง")], ref_no="PO-6"))
    made = _rows(acct, "STMAS", STKDES="ของอีกอย่าง")[0]
    assert plain.ok and (made["ACCCOD"].strip(), made["STKGRP"].strip()) == ("ST01", "FG")  # 没给：账套众数


def test_a_group_or_class_that_is_not_in_this_account_set_is_refused(acct):
    _groups(acct)
    before = _state(acct)
    res = _buy(acct, _purchase([_new("stock_item", stkgrp="ZZ")], **V21), make_backup=True)
    assert not res.ok and res.error_code == "ITEM_GROUP_NOT_FOUND"
    for group in ("EXP", "NOPE"):
        res = _sell(acct, _sale([_new("stock_sale", acccod=group, cost_choice="none")], **V21), make_backup=True)
        assert not res.ok and res.error_code == "STOCK_ACC_GROUP_UNFIT", (group, res.error_code, res.error)
    assert _state(acct) == before
    assert worker._outcome_for("ITEM_GROUP_NOT_FOUND") == "needs_review"


# ---------------- 出库成本 A11 ----------------


def test_guided_entry_asks_for_the_cost_of_a_line_without_a_cost_basis(acct):
    """销售出库没有成本基础（要新建的物料；结存 ≤ 0 且没有标准成本）：以前静默不结转。引导模式整单不写，带回行号、物料码与结存。
    有正结存、有标准成本的行不问。"""
    _set(acct, "STMAS", "STKCOD", "ICE02", STNPR=0.0)
    before = _state(acct)
    lines = [_item("ICE01", 1, 30.0, "stock_sale"), _item("ICE02", 1, 30.0, "stock_sale"), _new("stock_sale")]
    res = _sell(acct, _sale(lines, **GUIDED), make_backup=True)
    assert not res.ok and res.error_code == "COST_BASIS_CHOICE", (res.error_code, res.error)
    first = {"line": 2, "name": "ICE02", "stkcod": "ICE02", "balance": "0"}
    assert res.choice == {"code": "COST_BASIS_CHOICE", **first,
                          "lines": [first, {"line": 3, "name": NEW, "stkcod": "", "balance": "0"}]}
    assert _state(acct) == before
    assert worker._outcome_for(res.error_code) == "needs_review"
    _set(acct, "STMAS", "STKCOD", "ICE02", STNPR=7.0)
    assert _sell(acct, _sale(lines[:2], **GUIDED)).ok  # 有标准成本：不问
    assert _sell(acct, _sale([_new("stock_sale")], ref_no="IV-7", **V21)).ok  # 不带 guided：照旧不结转


def test_a_given_cost_is_carried_like_a_standard_cost(acct):
    """cost_choice = given：没有成本基础的行按人填的每库存单位成本结转（写法同标准成本那一支：XUNITPR、XTRNVAL、借成本 / 贷存货）。"""
    res = _sell(acct, _sale([_new("stock_sale", cost_choice="given", cost="4.5")], **GUIDED))
    assert res.ok, res.error
    row = _rows(acct, "STCRD", DOCNUM=res.docnum)[0]
    assert (row["XUNITPR"], row["XTRNVAL"]) == (4.5, 9.0)
    made = _rows(acct, "STMAS", STKDES=NEW)[0]
    assert (made["TOTBAL"], made["TOTVAL"]) == (-2.0, -9.0)
    assert (COGS_ACC, "0", 9.0) in _journal(acct, res.docnum) and (INVENTORY_ACC, "1", 9.0) in _journal(acct, res.docnum)
    mode = res.line_modes[0]
    assert (mode["reason"], mode["created"], mode["name"]) == (SS.REASON_COST_GIVEN, True, NEW) and "cost_ignored" not in mode


def test_no_cost_carried_is_the_old_way_of_writing(acct, tmp_path):
    """cost_choice = none 等于现在的写法：与不带这个键的版本 20 载荷写出的每一张表逐字节相同。"""
    import shutil

    twin = tmp_path / "twin"
    shutil.copytree(acct, twin)
    new, old = _sell(acct, _sale([_new("stock_sale", cost_choice="none")], **GUIDED)), WS_sell(twin, _sale([_new("stock_sale")]))
    assert new.ok and old.ok and new.line_modes == old.line_modes
    assert new.line_modes[0]["reason"] == SS.REASON_NEW_ITEM
    assert _state(acct) == _state(twin)


def test_a_given_cost_is_not_used_when_the_books_have_a_cost(acct):
    """有正结存照移动均价、有标准成本照标准成本：人填的不用，回执标 cost_ignored。"""
    res = _sell(acct, _sale([_item("ICE01", 1, 30.0, "stock_sale", cost_choice="given", cost="99")], **GUIDED))
    assert res.ok and res.line_modes[0]["cost_ignored"] is True
    assert _rows(acct, "STCRD", DOCNUM=res.docnum)[0]["XUNITPR"] == 10.0  # 50 / 5
    _set(acct, "STMAS", "STKCOD", "ICE02", STNPR=7.0)
    res = _sell(acct, _sale([_item("ICE02", 1, 30.0, "stock_sale", cost_choice="given", cost="99")], ref_no="IV-8", **GUIDED))
    assert res.ok and res.line_modes[0]["cost_ignored"] is True and res.line_modes[0]["reason"] == SS.REASON_STANDARD_COST
    assert _rows(acct, "STCRD", DOCNUM=res.docnum)[0]["XUNITPR"] == 7.0


def test_negative_stock_is_checked_before_the_cost_is_asked(acct):
    """账套不许负库存：卖新物料的单写不进（STOCK_INSUFFICIENT），不出成本题。"""
    isinfo(acct, MAINLOC="01", NEGALLOW="N")
    res = _sell(acct, _sale([_new("stock_sale")], **GUIDED))
    assert not res.ok and res.error_code == "STOCK_INSUFFICIENT" and res.choice is None
    assert res.shortages == [{"stkcod": "", "loccod": "01", "need": "2", "have": "0", "name": NEW, "new": True}]


# ---------------- 新建对方的类型、账期 A8 ----------------


def _parties(acct):
    for table in ("APMAS", "ARMAS"):
        handle = dbf.Table(str(acct / f"{table}.DBF"), codepage=S.CODEPAGE)
        handle.open(dbf.READ_WRITE)
        handle.add_fields("PAYTRM N(3,0)")
        handle.close()
    seed(acct, "APMAS", [{"SUPCOD": "ก001", "SUPNAM": "เก่า", "SUPTYP": "00"}, {"SUPCOD": "ก002", "SUPNAM": "เก่า2", "SUPTYP": "00"},
                         {"SUPCOD": "ข001", "SUPNAM": "ค้างจ่าย", "SUPTYP": "07"}])
    seed(acct, "ARMAS", [{"CUSCOD": "ก001", "CUSNAM": "เก่า", "CUSTYP": "00"}, {"CUSCOD": "ข001", "CUSNAM": "ส่ง", "CUSTYP": "02"}])


def _new_party(payload, block, **keys):
    payload[block] = {"code": "", "name": "บริษัท ใหม่จริง จำกัด", "tax_id": "0105555000011", "prename": "",
                      f"{block}_new": True, **keys}
    return payload


def test_a_new_party_takes_the_type_and_term_days_of_the_payload(acct):
    _parties(acct)
    buy = _buy(acct, _new_party(_purchase([_item("ICE01", 1, 10.0, "stock_item")], **V21), "supplier", type="07", payterm=30))
    assert buy.ok and buy.created_party, buy.error
    made = _rows(acct, "APMAS", SUPCOD=buy.party_code)[0]
    assert (made["SUPTYP"].strip(), made["PAYTRM"]) == ("07", 30)
    assert _rows(acct, "APTRN", DOCNUM=buy.docnum)[0]["DUEDAT"] == dt.date(2026, 2, 14)  # 单据日 + 新档账期 30 天
    sale = _sell(acct, _new_party(_sale([_item("ICE01", 1, 30.0, "stock_sale")], **V21), "customer", type="02", payterm=7))
    assert sale.ok and sale.created_party, sale.error
    made = _rows(acct, "ARMAS", CUSCOD=sale.party_code)[0]
    assert (made["CUSTYP"].strip(), made["PAYTRM"]) == ("02", 7)
    # 没给：类型取账套众数、账期不写（Express 默认 0），同以前
    plain = _buy(acct, _new_party(_purchase([_item("ICE01", 1, 10.0, "stock_item")], ref_no="PO-8"), "supplier",
                                  name="ร้าน อีกเจ้า", tax_id="0105555000022"))
    made = _rows(acct, "APMAS", SUPCOD=plain.party_code)[0]
    assert plain.ok and made["SUPTYP"].strip() == "00" and not made["PAYTRM"]


def test_a_party_type_no_party_of_this_account_set_uses_is_refused(acct):
    _parties(acct)
    before = _state(acct)
    buy = _buy(acct, _new_party(_purchase([_item("ICE01", 1, 10.0, "stock_item")], **V21), "supplier", type="02"),
               make_backup=True)  # 02 是客户用的类型，供应商没有
    assert not buy.ok and buy.error_code == "PARTY_TYPE_NOT_FOUND"
    sale = _sell(acct, _new_party(_sale([_item("ICE01", 1, 30.0, "stock_sale")], **V21), "customer", type="07"), make_backup=True)
    assert not sale.ok and sale.error_code == "PARTY_TYPE_NOT_FOUND"
    assert _state(acct) == before
    assert worker._outcome_for("PARTY_TYPE_NOT_FOUND") == "needs_review"
    assert guided.party_types(acct, "supplier") == ["00", "07"] and guided.party_types(acct, "customer") == ["00", "02"]


# ---------------- 缺陷 G-B1 ----------------


def test_a_new_stock_item_does_not_copy_the_pack_factors_of_the_template(acct):
    """模板物料（账套里挂众数科目组的那一件）的计数单位是「箱 = 20」：以前新品四个单位列同码、系数却抄来 20。新建一律写 1。"""
    _set(acct, "STMAS", "STKCOD", "ICE01", CQUCOD="กล", CFACTOR=20.0, PQUCOD="กล", PFACTOR=12.0, SQUCOD="ถง", SFACTOR=1.0)
    _set(acct, "STMAS", "STKCOD", "ICE02", CQUCOD="กล", CFACTOR=20.0, PQUCOD="กล", PFACTOR=12.0, SQUCOD="ถง", SFACTOR=1.0)
    made = PM.ensure_stock_item(acct, NEW, unit="ถัง")
    row = _rows(acct, "STMAS", STKCOD=made.stkcod)[0]
    assert {row[field].strip() for field in ("QUCOD", "PQUCOD", "SQUCOD", "CQUCOD")} == {"ถง"}
    assert (row["PFACTOR"], row["SFACTOR"], row["CFACTOR"]) == (1.0, 1.0, 1.0)


# ---------------- 代录不收新键 ----------------


def test_an_operator_task_with_guided_keys_is_refused(acct):
    """新键第一批只支持直写：代录的单带了它们不录、转人工（服务端本来不派，这里兜底）。"""
    import types

    class Queue:
        failures = []

        def ack_failure(self, task_id, error, **kw):
            self.failures.append((task_id, error, kw))

    task = types.SimpleNamespace(id="T1", payload={"entry_mode": "operator", "payload_version": 21, "guided": True,
                                                   "items": [{"name": "x", "unit_code": "ขด"}]})
    assert worker._handle_operator_task(None, Queue(), task, None) == "needs_review"
    assert Queue.failures[0][2]["error_code"] == "PAYLOAD_INVALID" and Queue.failures[0][2]["outcome"] == "needs_review"
    assert guided.uses({"supplier": {"payterm": 3}}) and guided.uses({"items": [{"negallow": ""}]}) and guided.uses({"guided": False})
    assert not guided.uses({"items": [{"unit": "ขวด", "factor": "2"}], "supplier": {"code": "S"}})


def test_an_operator_task_may_carry_only_the_four_keys_new_items_need():
    """代录单只带建新物料用的四个行键（单位码、科目组、分类、允许负库存）照录（11-12 晚）；带了表头、对方块或别的行键照旧拒收。"""
    four = {"payload_version": 21, "items": [{"name": "x", "unit_code": "ขด", "acccod": "ST01", "stkgrp": "FG", "negallow": "N"}]}
    assert guided.uses(four) and guided.operator_allowed(four)
    assert guided.operator_allowed({"items": [{"name": "x"}]})
    for extra in ({"guided": True}, {"due_date_be": "690130"}, {"supplier": {"type": "01"}}):
        assert not guided.operator_allowed({**four, **extra})
    assert not guided.operator_allowed({"items": [{"name": "x", "cost_choice": "none"}]})
