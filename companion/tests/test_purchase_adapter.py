"""采购载荷适配器测试 · headless(不依赖 GUI / OCR / 真 Express)。

覆盖:佛历日期转换、票头映射、RR/HP、所选账套名匹配 + fail-safe、必填守门、金额自洽、分录平衡、
新供应商旗标、录入序列(field_bindings)、零税。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion.purchase_adapter import (  # noqa: E402
    ERR_ACCOUNT_SET_NOT_ALLOWED,
    ERR_AMOUNTS,
    ERR_BAD_DOCTYPE,
    ERR_ENTRY_NOT_BALANCED,
    ERR_INVALID_DATE,
    ERR_MISSING_REF_NO,
    ERR_MISSING_SUPPLIER,
    PURCHASE_FIELD_ORDER,
    be_date_to_express,
    be_period_to_express,
    build_purchase_entry,
    field_bindings,
    validate_purchase_payload,
)


def _payload(**over):
    p = {
        "doctype": "RR",
        "account_set": "DATAT",
        "docdate_be": "581231",
        "vat_period_be": "581201",
        "ref_no": "INV-2015-001",
        "supplier": {
            "code": "S001",
            "name": "บริษัท ปตท จำกัด",
            "tax_id": "0107544000108",
            "prename": "บริษัท",
            "supplier_new": False,
        },
        "vat_rate": 7.0,
        "base_amount": "375347.20",
        "vat_amount": "26274.30",
        "total_amount": "401621.50",
        "lines": [
            {"acc": "11-04-02-00", "side": "D", "amount": "375347.20", "desc": "ซื้อ"},
            {"acc": "11-05-04-01", "side": "D", "amount": "26274.30", "desc": "ภาษีซื้อ"},
            {"acc": "21-02-01-00", "side": "C", "amount": "401621.50", "desc": "เจ้าหนี้"},
        ],
    }
    p.update(over)
    return p


def _vp(payload):
    """所选账套 = DATAT 的视角校验(账套名闸是第一道·测其余守门须先放行账套名)。"""
    return validate_purchase_payload(payload, allowed=("DATAT",))


# ── 佛历日期 ────────────────────────────────────────────────
def test_be_date_to_express():
    assert be_date_to_express("581231") == "31/12/58"
    assert be_date_to_express("690519") == "19/05/69"


def test_be_date_rejects_bad():
    for bad in ("", "5812", "581399", "581200", "abcdef"):
        with pytest.raises(ValueError):
            be_date_to_express(bad)


def test_be_period_to_express():
    assert be_period_to_express("581201") == "12/58"
    assert be_period_to_express("") == ""
    assert be_period_to_express("bad") == ""


# ── happy path ──────────────────────────────────────────────
def test_validate_and_build_ok():
    p = _payload()
    assert _vp(p).ok
    e = build_purchase_entry(p)
    assert e.doctype == "RR"
    assert e.account_set == "DATAT"
    assert e.supplier_code == "S001"
    assert e.supplier_new is False
    assert e.ref_no == "INV-2015-001"
    assert e.doc_date == "31/12/58"
    assert e.vat_period == "12/58"
    assert e.vat_rate == "7"
    assert e.base_amount == "375347.20"
    assert e.total_amount == "401621.50"
    assert len(e.lines) == 3


def test_doctype_hp():
    e = build_purchase_entry(_payload(doctype="HP"))
    assert e.doctype == "HP"


# ── 守门 ────────────────────────────────────────────────────
def test_reject_account_set_other_than_selected():
    # 所选 DATAT,载荷给别的账套 → 拒(防串账套)
    r = _vp(_payload(account_set="PDATAT"))
    assert not r.ok and r.error_code == ERR_ACCOUNT_SET_NOT_ALLOWED


def test_failsafe_reject_when_no_selection():
    # 缺所选(allowed 空·默认)→ 任何账套都拒(没选不能瞎写)
    r = validate_purchase_payload(_payload(account_set="DATAT"))
    assert not r.ok and r.error_code == ERR_ACCOUNT_SET_NOT_ALLOWED


def test_reject_bad_doctype():
    r = _vp(_payload(doctype="ZZ"))
    assert not r.ok and r.error_code == ERR_BAD_DOCTYPE


def test_reject_bad_date():
    r = _vp(_payload(docdate_be="58123"))
    assert not r.ok and r.error_code == ERR_INVALID_DATE


def test_reject_missing_ref_no():
    r = _vp(_payload(ref_no="  "))
    assert not r.ok and r.error_code == ERR_MISSING_REF_NO


def test_reject_missing_supplier():
    r = _vp(
        _payload(supplier={"code": "", "name": "", "supplier_new": True})
    )
    assert not r.ok and r.error_code == ERR_MISSING_SUPPLIER


def test_reject_amounts_inconsistent():
    r = _vp(_payload(total_amount="999999.00"))
    assert not r.ok and r.error_code == ERR_AMOUNTS


def test_reject_unbalanced_lines():
    bad = _payload()
    bad["lines"][-1]["amount"] = "1.00"  # 贷方塌掉
    r = _vp(bad)
    assert not r.ok and r.error_code == ERR_ENTRY_NOT_BALANCED


def test_reject_empty_lines():
    r = _vp(_payload(lines=[]))
    assert not r.ok and r.error_code == ERR_ENTRY_NOT_BALANCED


# ── 新供应商 ────────────────────────────────────────────────
def test_supplier_new_flag_and_bindings_skip_code():
    p = _payload(supplier={"code": "", "name": "ร้านใหม่", "supplier_new": True})
    assert _vp(p).ok  # 有名即可,缺代码不阻塞
    e = build_purchase_entry(p)
    assert e.supplier_new is True
    assert e.supplier_code == ""
    names = [n for n, _ in field_bindings(e)]
    assert "supplier_code" not in names  # 新供应商不录空代码


def test_supplier_new_inferred_when_code_empty():
    p = _payload(supplier={"code": "", "name": "ร้าน", "supplier_new": False})
    e = build_purchase_entry(p)
    assert e.supplier_new is True  # 无代码 → 推断为新供应商


# ── 录入序列 ────────────────────────────────────────────────
def test_field_bindings_order_and_values():
    e = build_purchase_entry(_payload())
    bindings = field_bindings(e)
    assert [n for n, _ in bindings] == list(PURCHASE_FIELD_ORDER)
    d = dict(bindings)
    assert d["supplier_code"] == "S001"
    assert d["ref_no"] == "INV-2015-001"
    assert d["doc_date"] == "31/12/58"
    assert d["base_amount"] == "375347.20"
    assert d["vat_rate"] == "7"


# ── 零税(免税/零税票)──────────────────────────────────────
def test_zero_vat():
    p = _payload(
        vat_rate=0.0,
        base_amount="1000.00",
        vat_amount="0.00",
        total_amount="1000.00",
        lines=[
            {"acc": "11-04-02-00", "side": "D", "amount": "1000.00", "desc": "ซื้อ"},
            {"acc": "21-02-01-00", "side": "C", "amount": "1000.00", "desc": "เจ้าหนี้"},
        ],
    )
    assert _vp(p).ok
    e = build_purchase_entry(p)
    assert e.vat_rate == "0"
    assert e.vat_amount == "0.00"
