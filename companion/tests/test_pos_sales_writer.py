"""POS 导出的日汇总票(载荷 3)写 Express:现销 HS + 销项税一行 + 每种非现金付款方式一张日记账凭证,同一次写入。

合成 VFP 账套,不依赖客户数据。覆盖:凭证与销售单同写、借收款科目 / 贷现金、单号自编、票号起止进销项税摘要、
没有可用的普通日记账单据簿 / 收款科目不能记账时不动账套、读回不符整体回滚、全现金的天不要单据簿、现金净额为负的天、
`pos` 块不合格回报 POS_INVALID、旧载荷照旧。
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import dbf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion import dbf_schema as S  # noqa: E402
from companion import dbf_sales as WS  # noqa: E402
from companion import pos_journal as PJ  # noqa: E402

import account_rules_fixture as F  # noqa: E402
from _synth_account import seed_fiscal_year  # noqa: E402

CASH, QR_BANK, CARD_BANK, REVENUE, OUTPUT_VAT = "11-01-01-00", "11-02-01-01", "11-02-01-02", "41-01-00-00", "21-05-01-00"
RECEIVABLE = "11-03-01-00"


def _pos_sale(transfers=None, cash=None, **over):
    transfers = transfers if transfers is not None else [
        {"acc": QR_BANK, "amount": "535.00", "label": "พร้อมเพย์"},
        {"acc": CARD_BANK, "amount": "214.00", "label": "บัตรเครดิต"},
    ]
    total = Decimal(over.get("total_amount") or "1605.00")
    if cash is None:  # 当天现金净额 = 本单含税额 − 各转账(云端映射器同一算法)
        cash = format(total - sum((Decimal(row["amount"]) for row in transfers), Decimal("0")), "f")
    p = {
        "payload_version": 3,
        "direction": "sales",
        "doctype": "HS",
        "account_set": "DATAT",
        "docdate_be": "691013",
        "vat_period_be": "691001",
        "ref_no": "B2610130001-9",
        "customer": {"code": "เงินสด", "name": "เงินสด", "tax_id": "", "prename": "", "customer_new": False},
        "vat_rate": 7.0,
        "base_amount": "1500.00",
        "vat_amount": "105.00",
        "total_amount": "1605.00",
        "lines": [
            {"acc": RECEIVABLE, "side": "D", "amount": "1605.00", "desc": "เงินสด"},
            {"acc": REVENUE, "side": "C", "amount": "1500.00", "desc": "รายได้จากการขาย"},
            {"acc": OUTPUT_VAT, "side": "C", "amount": "105.00", "desc": "ภาษีขาย"},
        ],
        "items": [],
        "items_status": "empty",
        "pos": {"first": "B2610130001", "last": "B2610130009", "count": 9, "docnum": "auto", "cash": cash,
                "transfers": transfers},
    }
    p.update(over)
    return p


def _book(d, *rows):
    F.seed(d, "ISRUN", [{"DOCTYP": prefix, "PREFIX": prefix, "DOCNUM": number, "JNLTYP": jnltyp}
                        for prefix, number, jnltyp in rows])


def _drop_book(d, prefix=None):
    """软删 ISRUN 里的单据簿(给了单别只删那一本)。"""
    t = dbf.Table(str(d / "ISRUN.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        for r in t:
            if prefix is None or (r.PREFIX or "").strip() == prefix:
                dbf.delete(r)
    finally:
        t.close()


@pytest.fixture
def datat(tmp_path):
    d = tmp_path / "70EXP" / "test"
    d.mkdir(parents=True)
    for name, spec in S.TABLE_SPECS.items():
        t = dbf.Table(str(d / f"{name}.DBF"), spec, codepage=S.CODEPAGE, dbf_type=S.DBF_TYPE)
        t.open(dbf.READ_WRITE)
        t.close()
    seed_fiscal_year(d, 2026)
    F.isinfo(d, ACCNUM00=CASH, ACCNUM02=RECEIVABLE)
    F.glacc(d, (CASH, "เงินสด"), (QR_BANK, "ธนาคาร QR"), (CARD_BANK, "ธนาคาร บัตร"), (REVENUE, "รายได้"),
            (OUTPUT_VAT, "ภาษีขาย"), (RECEIVABLE, "ลูกหนี้การค้า"))
    # 零售账套都有现金客户档(เงินสด)
    F.seed(d, "ARMAS", [{"CUSCOD": "เงินสด", "CUSNAM": "เงินสด", "ACCNUM": RECEIVABLE}])
    _book(d, ("HS", "0000001", "03"), ("JV", "0000010", "00"))
    return d


def _rows(d, name, **match):
    t = dbf.Table(str(d / f"{name}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        return [r for r in t if not dbf.is_deleted(r) and all((r[k] or "").strip() == v for k, v in match.items())]
    finally:
        t.close()


def _write(d, payload, **kw):
    return WS.write_sale(payload, d, allowed_sets=("DATAT",), selected_dir=str(d), **{"make_backup": False, **kw})


def _lines(d, voucher):
    return sorted(((r.TRNTYP or "").strip(), (r.ACCNUM or "").strip(), Decimal(str(r.AMOUNT)))
                  for r in _rows(d, "GLJNLIT", VOUCHER=voucher))


def test_cash_sale_and_one_voucher_per_non_cash_payment_in_one_write(datat):
    res = _write(datat, _pos_sale())
    assert res.ok, res.error
    # 单号自编(HS + 佛历年月日 + 序号),不拿票号区间当单号
    assert res.docnum == "HS691013-001"
    ar = _rows(datat, "ARTRN", DOCNUM="HS691013-001")[0]
    assert (ar.YOUREF or "").strip() == "B2610130001-9"
    # 销项税每天一行:参考号是区间,摘要写全票号起止(报税草稿据「อย่างย่อ」认简易税票)
    iv = _rows(datat, "ISVAT", DOCNUM="HS691013-001")
    assert len(iv) == 1 and (iv[0].VATREC or "").strip() == "S"
    assert (iv[0].REFNUM or "").strip() == "B2610130001-9"
    assert (iv[0].DESCRP or "").strip() == "ใบกำกับภาษีอย่างย่อ B2610130001-B2610130009"
    # 现销借现金(账套现金科目),贷收入 + 销项税
    assert _lines(datat, "HS691013-001") == sorted([("0", CASH, Decimal("1605.00")), ("1", REVENUE, Decimal("1500.00")),
                                                    ("1", OUTPUT_VAT, Decimal("105.00"))])
    # 每种非现金付款方式一张普通日记账凭证:借收款科目 / 贷现金
    assert res.notes["pos_vouchers"] == ["JV691013-001", "JV691013-002"]
    heads = {(r.VOUCHER or "").strip(): (r.JNLTYP or "").strip() for r in _rows(datat, "GLJNL")}
    assert heads == {"HS691013-001": "03", "JV691013-001": "00", "JV691013-002": "00"}
    assert _lines(datat, "JV691013-001") == sorted([("0", QR_BANK, Decimal("535.00")), ("1", CASH, Decimal("535.00"))])
    assert _lines(datat, "JV691013-002") == sorted([("0", CARD_BANK, Decimal("214.00")), ("1", CASH, Decimal("214.00"))])
    # 普通日记账计数器随凭证张数增加
    assert (_rows(datat, "ISRUN", PREFIX="JV")[0].DOCNUM or "").strip() == "0000012"


def test_voucher_numbers_continue_after_existing_ones_of_the_day(datat):
    F.seed(datat, "GLJNL", [{"JNLTYP": "00", "VOUCHER": "JV691013-004", "DESCRP": "manual"}])
    res = _write(datat, _pos_sale())
    assert res.ok, res.error
    assert res.notes["pos_vouchers"] == ["JV691013-005", "JV691013-006"]


def test_an_all_cash_day_needs_no_journal_book(datat):
    _drop_book(datat, "JV")
    res = _write(datat, _pos_sale(transfers=[]))
    assert res.ok, res.error
    assert not (res.notes or {}).get("pos_vouchers")
    assert [(r.VOUCHER or "").strip() for r in _rows(datat, "GLJNL")] == ["HS691013-001"]


def test_no_usable_journal_book_stops_before_touching_the_account_set(datat):
    _drop_book(datat, "JV")
    res = _write(datat, _pos_sale())
    assert not res.ok
    assert res.error_code == PJ.ERR_JOURNAL_BOOK == "JOURNAL_BOOK_NOT_FOUND"
    assert _rows(datat, "ARTRN") == [] and _rows(datat, "GLJNL") == [] and _rows(datat, "ISVAT") == []


def test_several_general_journal_books_use_jv_or_stop(datat):
    _book(datat, ("GJ", "0000001", "00"))
    assert PJ.journal_prefix(datat) == "JV"
    _drop_book(datat, "JV")
    _book(datat, ("AJ", "0000001", "00"))
    assert PJ.journal_prefix(datat) is None  # GJ 与 AJ 都是普通日记账、没有 JV:不猜
    res = _write(datat, _pos_sale())
    assert res.error_code == "JOURNAL_BOOK_NOT_FOUND"


def test_a_single_general_journal_book_is_used_whatever_its_prefix(datat):
    _drop_book(datat, "JV")
    _book(datat, ("GJ", "0000001", "00"))
    res = _write(datat, _pos_sale())
    assert res.ok, res.error
    assert res.notes["pos_vouchers"] == ["GJ691013-001", "GJ691013-002"]


def test_a_receiving_account_that_cannot_be_posted_stops_before_writing(datat):
    res = _write(datat, _pos_sale(transfers=[{"acc": "11-09-99-99", "amount": "535.00", "label": "QR"}]))
    assert not res.ok
    assert res.error_code == PJ.ERR_ACCOUNT == "ACCOUNT_NOT_POSTABLE"
    assert _rows(datat, "ARTRN") == []


def test_a_voucher_that_reads_back_wrong_rolls_back_the_whole_day(datat, monkeypatch):
    real = PJ._gl_line

    def wrong_credit(voucher, seqit, docdat, accnum, descrp, trntyp, amt, depcod, departmental):
        if voucher.startswith("JV") and trntyp == S.TRNTYP_CREDIT:
            accnum = REVENUE  # 贷方写错科目:读回必须发现
        return real(voucher, seqit, docdat, accnum, descrp, trntyp, amt, depcod, departmental)

    monkeypatch.setattr(PJ, "_gl_line", wrong_credit)
    before = {name: len(_rows(datat, name)) for name in ("ARTRN", "ISVAT", "GLJNL", "GLJNLIT", "ARBAL")}
    isrun = [(r.PREFIX or "").strip() + (r.DOCNUM or "").strip() for r in _rows(datat, "ISRUN")]
    res = _write(datat, _pos_sale(), make_backup=True)
    assert not res.ok
    assert res.error_code == "READBACK_VERIFY_FAILED"
    # 同一份写前备份整体还原:销售单、销项税、两张凭证、计数器都不留
    assert {name: len(_rows(datat, name)) for name in before} == before
    assert [(r.PREFIX or "").strip() + (r.DOCNUM or "").strip() for r in _rows(datat, "ISRUN")] == isrun


def test_a_failure_while_writing_vouchers_rolls_back_the_sale(datat, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("share dropped")

    monkeypatch.setattr(PJ, "_bump_isrun", broken)
    res = _write(datat, _pos_sale(), make_backup=True)
    assert not res.ok
    assert _rows(datat, "ARTRN") == [] and _rows(datat, "GLJNL") == [] and _rows(datat, "ISVAT") == []


def test_a_long_ticket_range_is_cut_to_the_vat_description_width(datat):
    first, last = "POS01-BR0042-2026101300001", "POS01-BR0042-2026101300987"
    res = _write(datat, _pos_sale(pos={"first": first, "last": last, "count": 987, "docnum": "auto", "cash": "1605.00",
                                       "transfers": []}, ref_no="ABB691013"))
    assert res.ok, res.error
    text = (_rows(datat, "ISVAT")[0].DESCRP or "").strip()
    assert text.startswith("ใบกำกับภาษีอย่างย่อ POS01-BR0042-2026101300001-")
    assert len(text.encode("cp874")) <= 60


def test_a_day_with_more_cash_refunded_than_taken(datat):
    """现金退款多过现金收款(卡 1000、现金 50、现金退款 150):本单 900 借现金,凭证借银行 1000 / 贷现金 1000,
    现金净流出 100。与云端 tests/test_cowork_pos_import.py 同一组数。"""
    pay = _pos_sale(transfers=[{"acc": CARD_BANK, "amount": "1000.00", "label": "Card"}], cash="-100.00",
                    base_amount="841.12", vat_amount="58.88", total_amount="900.00",
                    lines=[{"acc": RECEIVABLE, "side": "D", "amount": "900.00", "desc": "เงินสด"},
                           {"acc": REVENUE, "side": "C", "amount": "841.12", "desc": "รายได้จากการขาย"},
                           {"acc": OUTPUT_VAT, "side": "C", "amount": "58.88", "desc": "ภาษีขาย"}])
    res = _write(datat, pay)
    assert res.ok, res.error
    assert _lines(datat, res.docnum) == sorted([("0", CASH, Decimal("900.00")), ("1", REVENUE, Decimal("841.12")),
                                                ("1", OUTPUT_VAT, Decimal("58.88"))])
    assert res.notes["pos_vouchers"] == ["JV691013-001"]
    assert _lines(datat, "JV691013-001") == sorted([("0", CARD_BANK, Decimal("1000.00")),
                                                    ("1", CASH, Decimal("1000.00"))])


@pytest.mark.parametrize("pos, reason", [
    ({"first": "1", "last": "2", "count": 2, "docnum": "printed", "cash": "1605.00", "transfers": []}, "docnum"),
    ({"first": "", "last": "2", "count": 2, "docnum": "auto", "cash": "1605.00", "transfers": []}, "first"),
    ({"first": "1", "last": "2", "count": 0, "docnum": "auto", "cash": "1605.00", "transfers": []}, "count"),
    ({"first": "1", "last": "2", "count": 2, "docnum": "auto", "cash": "0.00",
      "transfers": [{"acc": QR_BANK, "amount": "9999.00", "label": "QR"}]}, "add up"),
    ({"first": "1", "last": "2", "count": 2, "docnum": "auto", "transfers": []}, "add up"),
    ({"first": "1", "last": "2", "count": 2, "docnum": "auto", "cash": "Infinity", "transfers": []}, "add up"),
    ({"first": "1", "last": "2", "count": 2, "docnum": "auto", "cash": "1600.00",
      "transfers": [{"acc": "", "amount": "5.00", "label": "QR"}]}, "account"),
    ({"first": "1", "last": "2", "count": 2, "docnum": "auto", "cash": "1610.00",
      "transfers": [{"acc": QR_BANK, "amount": "-5.00", "label": "QR"}]}, "amount"),
])
def test_a_malformed_pos_block_is_refused_before_writing(datat, pos, reason):
    res = _write(datat, _pos_sale(pos=pos))
    # 单独回报 POS_INVALID(云端认作要人处理、有译文),不混进通用的 PAYLOAD_INVALID
    assert not res.ok and res.error_code == "POS_INVALID"
    assert reason in res.error
    assert _rows(datat, "ARTRN") == []


def test_pos_days_are_cash_sales_only(datat):
    res = _write(datat, _pos_sale(doctype="IV"))
    assert not res.ok and res.error_code == "POS_INVALID"


def test_other_payload_problems_keep_the_general_code(datat):
    res = _write(datat, _pos_sale(total_amount="1700.00"))
    assert not res.ok and res.error_code == "PAYLOAD_INVALID"


def test_a_sale_without_pos_is_written_as_before_even_without_a_journal_book(datat):
    _drop_book(datat)
    legacy = _pos_sale(ref_no="IV69/00475", payload_version=1)
    legacy.pop("pos")
    res = _write(datat, legacy)
    assert res.ok, res.error
    assert res.docnum == "IV69/00475"  # 旧载荷:单号仍是票面号
    assert (_rows(datat, "ISVAT")[0].DESCRP or "").strip() == "เงินสด"
    assert not (res.notes or {}).get("pos_vouchers")
    assert [(r.VOUCHER or "").strip() for r in _rows(datat, "GLJNL")] == ["IV69/00475"]
