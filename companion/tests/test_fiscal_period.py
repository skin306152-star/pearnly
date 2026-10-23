# -*- coding: utf-8 -*-
"""余额表月桶按 ISPRD 财年期号落列 —— 期号判定件本身 + 两条写路的落位。

数字照真账套形状取(镜像 · 2026-07-27):11 月起账用 69ASIAR(CY 2025-11-01~2026-10-31,
4 本这样的账套 = ASIAMI/ASIAR × EXP68/EXP69);CY 不等于当前公历年用 69SINCER/6902ASC
(CY 2025 全年、NY 2026 全年 —— 今年的单必须落 NY 组)。

改坏了才会红的地方:把 fiscal_period.resolve_month_field 换回「单号里取日历月 + 恒写 CY」
(即 2026-07-27 之前的 dbf_schema.arbal_month_field/apbal_month_field),本模块 6 条断言
桶名的用例全红 —— 见 test_the_old_calendar_month_rule_lands_in_a_different_column。
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import dbf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion import dbf_sales as WS  # noqa: E402
from companion import dbf_schema as S  # noqa: E402
from companion import dbf_writer as W  # noqa: E402
from companion import fiscal_period as F  # noqa: E402
from companion.worker import _push_meta  # noqa: E402

from _synth_account import build, seed_fiscal_year  # noqa: E402


@pytest.fixture
def acct(tmp_path):
    """默认合成账套:1 月起账,CY = 2026 全年、NY = 2027 全年。"""
    return build(tmp_path / "70EXP" / "test")


def _november_book(acct):
    """财年 11 月起账(真账 69ASIAR 的形状:CY 2025-11-01 ~ 2026-10-31)。"""
    return seed_fiscal_year(acct, 2025, start_month=11)


# ── 期号判定:桶列号不是日历月 ─────────────────────────────────────────


def test_calendar_year_book_period_equals_calendar_month(acct):
    """1 月起账时财年期号 == 日历月 —— 多数账套如此,所以旧的错法一直没被发现。"""
    cal = F.load(acct)
    assert cal.start_month == 1
    for month in (1, 5, 12):
        assert cal.locate(dt.date(2026, month, 15)).suffix == str(month)


def test_november_book_bucket_is_the_fiscal_period_not_the_calendar_month(acct):
    """11 月起账:2025-11-07 的单落第 1 期不是第 11 期,期号整体偏两个月。"""
    _november_book(acct)
    cal = F.load(acct)
    assert cal.start_month == 11
    cases = {
        dt.date(2025, 11, 7): "1",  # 财年第 1 期 = 11 月
        dt.date(2026, 1, 7): "3",  # 跨自然年,仍在同一财年第 3 期
        dt.date(2026, 3, 15): "5",
        dt.date(2026, 10, 31): "12",  # 财年最后一天
        dt.date(2026, 11, 1): "1NY",  # 下一财年第 1 期
        dt.date(2027, 3, 15): "5NY",
    }
    for day, suffix in cases.items():
        assert F.month_field("HP", cal.locate(day)) == f"HP{suffix}", day


def test_year_slot_is_a_fiscal_range_not_a_calendar_year(acct):
    """CY/NY 是两段财年区间:11 月起账的账套里 2026 年的单落 CY 不落 NY。"""
    _november_book(acct)
    cal = F.load(acct)
    assert cal.locate(dt.date(2026, 6, 1)).slot == F.SLOT_CY
    assert cal.locate(dt.date(2026, 12, 1)).slot == F.SLOT_NY


def test_month_field_appends_ny_suffix_only_for_the_next_year_slot(acct):
    cal = F.load(acct)
    assert F.month_field("IV", cal.locate(dt.date(2026, 4, 1))) == "IV4"
    assert F.month_field("RR", cal.locate(dt.date(2027, 4, 1))) == "RR4NY"


# ── 判不出:不写桶,也不许猜 ───────────────────────────────────────────


def test_missing_isprd_table_yields_no_column(tmp_path):
    acct = build(tmp_path / "70EXP" / "test")
    (acct / "ISPRD.DBF").unlink()
    assert F.load(acct) is None
    field, why = F.resolve_month_field(acct, dt.date(2026, 5, 1), "IV")
    assert field is None and "判不出来" in why


def test_empty_isprd_table_yields_no_column(acct):
    """空表(靶场账套的 GLBAL 就是这样)也算判不出 —— 不许拿 CY 当默认。"""
    _clear_isprd(acct)
    assert F.load(acct) is None
    assert F.resolve_month_field(acct, dt.date(2026, 5, 1), "IV")[0] is None


def test_date_outside_the_24_period_window_yields_no_column(acct):
    for day in (dt.date(2025, 12, 31), dt.date(2028, 1, 1)):  # 窗口前一天 / 窗口后
        field, why = F.resolve_month_field(acct, day, "RR")
        assert field is None and "会计期间窗内" in why, day


def test_period_with_broken_dates_is_skipped_not_guessed(acct):
    """某一期的 BEG/END 读不出来 → 该期不参与匹配,落在那里的单据按判不出处理。"""
    _poke(acct, BEG5=None)
    assert len(F.load(acct).periods) == 23
    assert F.resolve_month_field(acct, dt.date(2026, 5, 10), "IV")[0] is None
    assert F.resolve_month_field(acct, dt.date(2026, 6, 10), "IV")[0] == "IV6"


# ── 两条写路的落位 ─────────────────────────────────────────────────────


def test_purchase_lands_in_the_fiscal_period_column_of_a_november_book(acct):
    """69ASIAR 形状:2026-03-15 的赊购落 RR5,不是单号 690315 看着像的 RR3。"""
    _november_book(acct)
    res = W.write_purchase(_purchase(docdate_be="690315"), acct, **_gate(acct))
    assert res.ok and not res.notes
    row = _bal(acct, "APBAL")
    assert row["RR5"] == 416021.14
    assert row["RR3"] == 0.0 and row["RR5NY"] == 0.0


def test_sale_of_this_year_lands_in_the_ny_slot_when_cy_is_last_year(acct):
    """1 月起账但 CY 是去年(69SINCER/6902ASC 的形状):今年的单必须落 NY 组。

    这是正在发生的那条 —— 旧算法恒写 CY,2026 年的销项全挤进 2025 年那 12 列。
    """
    seed_fiscal_year(acct, 2025)  # CY = 2025 全年,NY = 2026 全年
    res = WS.write_sale(_sale(docdate_be="690531"), acct, **_gate(acct))
    assert res.ok and not res.notes
    row = _bal(acct, "ARBAL")
    assert row["IV5NY"] == 9630.00
    assert row["IV5"] == 0.0


def test_document_number_still_carries_the_buddhist_calendar_month(acct):
    """发号是佛历年 + 日历月,与财年无关 —— 月桶改了,单号一个字都不许跟着改。"""
    _november_book(acct)
    res = W.write_purchase(_purchase(docdate_be="690315"), acct, **_gate(acct))
    assert res.docnum == "RR690315-001"


def test_vat_period_stays_on_the_first_of_the_calendar_month(acct):
    """VATPRD 恒为自然月首日(泰国 VAT 按自然月申报),同样与财年无关。"""
    _november_book(acct)
    W.write_purchase(_purchase(docdate_be="690315"), acct, **_gate(acct))
    t = dbf.Table(str(acct / "APTRN.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        assert next(iter(S.live_rows(t))).VATPRD == dt.date(2026, 3, 1)
    finally:
        t.close()


# ── 判不出时:不写桶 + 上报(绝不静默跳过)────────────────────────────


def test_out_of_window_document_writes_no_bucket_and_says_so(acct):
    """单据日期落在 24 期窗口外:余额桶一格不动,原因回到结果里。

    静默跳过是最坏的形态 —— 试算表照样平、推送日志照样绿,只有供应商对账单少一笔,
    要等几个月后会计对账才发现。
    """
    res = W.write_purchase(_out_of_window_purchase(), acct, **_gate(acct))
    assert res.ok  # 单据本身进去了(APTRN/GLJNL 齐),缺的只有月桶
    assert "会计期间窗内" in res.notes["apbal_not_written"]
    assert not _live_rows(acct, "APBAL")  # 一格没写 = 连余额行都没建


def test_sale_without_isprd_writes_no_bucket_and_says_so(acct):
    (acct / "ISPRD.DBF").unlink()
    res = WS.write_sale(_sale(), acct, **_gate(acct))
    assert res.ok
    assert "判不出来" in res.notes["arbal_not_written"]
    assert not _live_rows(acct, "ARBAL")


def test_the_note_reaches_the_cloud_meta(acct):
    """上报得真到云端:会计只看得到推送记录,notes 掉在本地日志里等于没说。"""
    res = W.write_purchase(_out_of_window_purchase(), acct, **_gate(acct))
    assert "apbal_not_written" in _push_meta(res, "purchase", str(acct))["notes"]


def test_a_normal_push_carries_no_notes(acct):
    """反过来:正常落桶时不许报 —— 每张单都挂个标注,标注就没人看了。"""
    res = W.write_purchase(_purchase(), acct, **_gate(acct))
    assert res.ok and not res.notes
    assert "notes" not in _push_meta(res, "purchase", str(acct))


# ── 反证:旧算法在这些形状上就是落错列 ─────────────────────────────────


def test_the_old_calendar_month_rule_lands_in_a_different_column(acct):
    """旧算法 = 单号取日历月 + 恒写 CY。本模块的两个账套形状上它与正解各错一处。

    这条钉的是"新用例不是白写的":旧算法若与新判定等价,上面那些桶名断言就测不出东西。
    """
    def old(docdate_be: str, doctype: str) -> str:
        return f"{doctype}{int(str(docdate_be)[2:4])}"

    _november_book(acct)  # 期号错:2026-03-15 是财年第 5 期,旧算法给 3
    assert old("690315", "RR") == "RR3"
    assert F.resolve_month_field(acct, dt.date(2026, 3, 15), "RR")[0] == "RR5"

    seed_fiscal_year(acct, 2025)  # 年槽错:2026 年的单该进 NY,旧算法恒给 CY
    assert old("690531", "IV") == "IV5"
    assert F.resolve_month_field(acct, dt.date(2026, 5, 31), "IV")[0] == "IV5NY"


# ── 夹具 ───────────────────────────────────────────────────────────────


def _gate(acct):
    """三重一致性闸的入参(合成账套里公司名为空,只校目录 + 账套名)。"""
    return {
        "allowed_sets": ("DATAT",),
        "selected_dir": str(acct),
        "make_backup": False,
    }


def _purchase(**over):
    p = {
        "doctype": "RR",
        "account_set": "DATAT",
        "docdate_be": "690315",
        "vat_period_be": "690301",
        "ref_no": "INV-9001",
        "supplier": {"code": "S001", "name": "ผู้ขาย", "supplier_new": False},
        "vat_rate": 7.0,
        "base_amount": "388804.80",
        "vat_amount": "27216.34",
        "total_amount": "416021.14",
        "lines": [
            {"acc": "11-04-02-00", "side": "D", "amount": "388804.80", "desc": "ซื้อ"},
            {"acc": "11-05-04-01", "side": "D", "amount": "27216.34", "desc": "ภาษีซื้อ"},
            {"acc": "21-02-01-00", "side": "C", "amount": "416021.14", "desc": "เจ้าหนี้"},
        ],
    }
    p.update(over)
    return p


def _out_of_window_purchase():
    """佛历 72xx = 公历 2029:落在默认账套 CY 2026 / NY 2027 这 24 期之外。"""
    return _purchase(docdate_be="720315", vat_period_be="720301")


def _sale(**over):
    p = {
        "direction": "sales",
        "doctype": "IV",
        "account_set": "DATAT",
        "docdate_be": "690531",
        "vat_period_be": "690501",
        "ref_no": "SO-9001",
        "customer": {"code": "C001", "name": "ลูกค้า", "customer_new": False},
        "vat_rate": 7.0,
        "base_amount": "9000.00",
        "vat_amount": "630.00",
        "total_amount": "9630.00",
        "lines": [
            {"acc": "11-03-01-00", "side": "D", "amount": "9630.00", "desc": "AR"},
            {"acc": "41-01-00-00", "side": "C", "amount": "9000.00", "desc": "รายได้"},
            {"acc": "11-05-04-02", "side": "C", "amount": "630.00", "desc": "ภาษีขาย"},
        ],
    }
    p.update(over)
    return p


def _live_rows(acct, table):
    t = dbf.Table(str(acct / f"{table}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        return [{f: r[f] for f in t.field_names} for r in S.live_rows(t)]
    finally:
        t.close()


def _bal(acct, table):
    """余额表唯一那条活行,数值列一律读成 float(空格 → 0.0)。"""
    (row,) = _live_rows(acct, table)
    return {k: float(v or 0) if not isinstance(v, str) else v for k, v in row.items()}


def _poke(acct, **fields):
    t = dbf.Table(str(acct / "ISPRD.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        with t[0] as rec:
            for name, value in fields.items():
                rec[name] = value
    finally:
        t.close()


def _clear_isprd(acct):
    t = dbf.Table(str(acct / "ISPRD.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        for r in t:
            dbf.delete(r)
    finally:
        t.close()



# ── 关账期间（1.12.0：只有 ERP 的单带 respect_period_lock）──────────────────


def test_a_locked_period_refuses_an_erp_document_and_leaves_the_book_untouched(acct):
    seed_fiscal_year(acct, 2026, locked_cy=(3,))
    assert F.locked(acct, dt.date(2026, 3, 15)) and not F.locked(acct, dt.date(2026, 4, 1))
    before = {p.name: p.read_bytes() for p in acct.iterdir() if p.is_file()}
    res = W.write_purchase(_purchase(respect_period_lock=True), acct, **_gate(acct))
    assert not res.ok and res.error_code == F.ERR_PERIOD_LOCKED
    sale = WS.write_sale(_sale(docdate_be="690315", vat_period_be="690301", respect_period_lock=True), acct, **_gate(acct))
    assert not sale.ok and sale.error_code == F.ERR_PERIOD_LOCKED
    assert {p.name: p.read_bytes() for p in acct.iterdir() if p.is_file()} == before


def test_cowork_documents_are_written_into_a_locked_period_as_before(acct):
    seed_fiscal_year(acct, 2026, locked_cy=(3,))
    assert W.write_purchase(_purchase(), acct, **_gate(acct)).ok
