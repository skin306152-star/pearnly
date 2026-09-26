# -*- coding: utf-8 -*-
"""软删行不算数 —— 会计在 Express 里删掉单据/档案之后,系统还得能重推、能查、能记账。

现场(2026-07-25):客户删掉发票 IV690531-001,之后同一张票**永远推不进去,而且每次都报
成功**。DBF 的删除是打标记不是抹数据,幂等闸在那条软删的旧行上命中,返回「已存在,跳过」,
一个字节都没写。

删单重推这条路上串着四道闸,任何一道把软删行当真都会把票挡在外面:
  ① 幂等查重(_find_existing)     → 认下幽灵行 = 假成功,票永远进不去
  ② 单头/GL 读回(_verify)        → 新旧同号并存,GL 合计翻倍 → 好单被判不平回滚
  ③ 明细读回(dbf_detail)         → 旧明细也数进去 → 行数对不上 → 回滚
  ④ 主档查码/读回、库存主档定位     → 挂到看不见的对手方 / 扣库存扣进幽灵行
反过来,发号(_next_docnum / _next_code / _next_stkcod)必须把软删行也算上,否则会把删掉
那张单的号再发一次。本模块正反两侧都咬。
"""

from __future__ import annotations

import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path

import dbf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion import dbf_sales as WS  # noqa: E402
from companion import dbf_schema as S  # noqa: E402
from companion import dbf_writer as WP  # noqa: E402
from companion import master_create as MC  # noqa: E402
from companion import product_master as PM  # noqa: E402
from companion import stock_sale as SS  # noqa: E402

from _synth_account import seed_fiscal_year  # noqa: E402

_REF = "IV690531-001"  # 现场那张票


@pytest.fixture
def datat(tmp_path):
    """空 DATAT 合成账套(采购 + 销项两套表族)。"""
    d = tmp_path / "70EXP" / "test"
    d.mkdir(parents=True)
    for name, spec in S.TABLE_SPECS.items():
        t = dbf.Table(
            str(d / f"{name}.DBF"), spec, codepage=S.CODEPAGE, dbf_type=S.DBF_TYPE
        )
        t.open(dbf.READ_WRITE)
        t.close()
    # 账套会计期间(余额表月桶的列号读它)。本模块的票是佛历 69xx = 公历 2026。
    seed_fiscal_year(d, 2026)
    return d


# ── 账套小工具 ────────────────────────────────────────────────────────────


def _seed(acct: Path, table: str, row: dict, *, deleted: bool = False) -> None:
    t = dbf.Table(str(acct / f"{table}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        t.append(row)
        if deleted:
            dbf.delete(t[-1])
    finally:
        t.close()


def _mark_deleted(acct: Path, table: str, field: str, value: str) -> int:
    """把 table 里 field==value 的行打上删除标记(模拟 Express 删除)。返回打了几行。"""
    n = 0
    t = dbf.Table(str(acct / f"{table}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        for r in t:
            if not dbf.is_deleted(r) and (r[field] or "").strip() == value:
                dbf.delete(r)
                n += 1
    finally:
        t.close()
    return n


def _express_delete_sale(acct: Path, docnum: str) -> None:
    """会计在 Express 里删掉一张销售单 —— 单头/销项税/GL 凭证/明细一起打删除标记。"""
    _mark_deleted(acct, "ARTRN", "DOCNUM", docnum)
    _mark_deleted(acct, "ISVAT", "DOCNUM", docnum)
    _mark_deleted(acct, "GLJNL", "VOUCHER", docnum)
    _mark_deleted(acct, "GLJNLIT", "VOUCHER", docnum)
    _mark_deleted(acct, "STCRD", "DOCNUM", docnum)


def _express_delete_purchase(acct: Path, docnum: str) -> None:
    _mark_deleted(acct, "APTRN", "DOCNUM", docnum)
    _mark_deleted(acct, "ISVAT", "DOCNUM", docnum)
    _mark_deleted(acct, "GLJNL", "VOUCHER", docnum)
    _mark_deleted(acct, "GLJNLIT", "VOUCHER", docnum)
    _mark_deleted(acct, "STCRD", "DOCNUM", docnum)


def _live(acct: Path, table: str, **match) -> list:
    t = dbf.Table(str(acct / f"{table}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        fields = t.field_names
        return [
            {f: (r[f].strip() if isinstance(r[f], str) else r[f]) for f in fields}
            for r in t
            if not dbf.is_deleted(r)
            and all((r[k] or "").strip() == v for k, v in match.items())
        ]
    finally:
        t.close()


def _count_all(acct: Path, table: str) -> int:
    t = dbf.Table(str(acct / f"{table}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        return len(t)
    finally:
        t.close()


def _count_deleted_rows(table):
    """变异用:把 S.live_rows 换成"软删行照算"的旧行为。"""
    return iter(list(table))


# ── 载荷 ──────────────────────────────────────────────────────────────────


def _sale(**over):
    p = {
        "direction": "sales",
        "doctype": "IV",
        "account_set": "DATAT",
        "docdate_be": "690531",
        "vat_period_be": "690501",
        "ref_no": _REF,
        "customer": {
            "code": "C001",
            "name": "ลูกค้า ทดสอบ",
            "tax_id": "0105551234567",
            "prename": "บริษัท",
        },
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


def _sale_items(**over):
    return _sale(
        items=[
            {"name": "สินค้า A", "qty": "2", "unit_price": "3000", "amount": "6000.00"},
            {"name": "สินค้า B", "qty": "1", "unit_price": "3000", "amount": "3000.00"},
        ],
        items_status="ok",
        items_account="41-01-00-00",
        **over,
    )


def _purchase(**over):
    p = {
        "doctype": "RR",
        "account_set": "DATAT",
        "docdate_be": "690531",
        "vat_period_be": "690501",
        "ref_no": "INV-9001",
        "supplier": {
            "code": "S001",
            "name": "บริษัท ปตท จำกัด",
            "tax_id": "0107544000108",
            "prename": "บริษัท",
        },
        "vat_rate": 7.0,
        "base_amount": "1000.00",
        "vat_amount": "70.00",
        "total_amount": "1070.00",
        "lines": [
            {"acc": "11-04-02-00", "side": "D", "amount": "1000.00", "desc": "ซื้อ"},
            {"acc": "11-05-04-01", "side": "D", "amount": "70.00", "desc": "ภาษีซื้อ"},
            {
                "acc": "21-02-01-00",
                "side": "C",
                "amount": "1070.00",
                "desc": "เจ้าหนี้",
            },
        ],
    }
    p.update(over)
    return p


def _push_sale(acct, payload=None):
    return WS.write_sale(
        payload or _sale(),
        acct,
        allowed_sets=("DATAT",),
        selected_dir=str(acct),
        make_backup=False,
    )


def _push_purchase(acct, payload=None):
    return WP.write_purchase(
        payload or _purchase(),
        acct,
        allowed_sets=("DATAT",),
        selected_dir=str(acct),
        make_backup=False,
    )


# ── ① 核心:删了还能重推 ───────────────────────────────────────────────────


def test_sale_lands_again_after_the_invoice_was_deleted_in_express(datat):
    """现场那条路:推 → 会计删 → 重推,必须真写进去。

    修前:ARTRN 里躺着那条软删的旧行,幂等闸命中它 → ok=True / skipped=True / 有效行 0。
    """
    first = _push_sale(datat)
    assert first.ok and not first.skipped and first.docnum == _REF

    _express_delete_sale(datat, _REF)
    assert _live(datat, "ARTRN") == []
    assert _count_all(datat, "ARTRN") == 1  # 行还躺在表里,只是打了删除标记

    again = _push_sale(datat)
    assert again.ok, again.error
    assert not again.skipped, "软删行被当成了「已存在」→ 报成功但一个字节没写"
    assert len(_live(datat, "ARTRN", DOCNUM=_REF)) == 1
    assert len(_live(datat, "ISVAT", DOCNUM=_REF)) == 1
    assert len(_live(datat, "GLJNLIT", VOUCHER=_REF)) == 3


def test_sale_with_detail_lands_again_after_delete(datat):
    """带明细的票同理 —— 旧 STCRD 行也软删着,明细读回不能把它数进来。"""
    first = _push_sale(datat, _sale_items())
    assert first.ok, first.error
    assert len(_live(datat, "STCRD", DOCNUM=_REF)) == 2

    _express_delete_sale(datat, _REF)
    again = _push_sale(datat, _sale_items())
    assert again.ok, again.error
    assert not again.skipped
    assert len(_live(datat, "STCRD", DOCNUM=_REF)) == 2
    assert _count_all(datat, "STCRD") == 4  # 两条软删旧行仍在表里


def test_purchase_lands_again_after_the_bill_was_deleted_in_express(datat):
    """采购侧同款闸(REFNUM+SUPCOD)。"""
    first = _push_purchase(datat)
    assert first.ok and not first.skipped

    _express_delete_purchase(datat, first.docnum)
    assert _live(datat, "APTRN") == []

    again = _push_purchase(datat)
    assert again.ok, again.error
    assert not again.skipped
    assert len(_live(datat, "APTRN", REFNUM="INV-9001")) == 1


# ── ② 反向:幂等不能被改坏 ─────────────────────────────────────────────────


def test_live_duplicate_sale_is_still_skipped(datat):
    """票还好端端在账里 → 仍旧幂等跳过,绝不建第二张。"""
    first = _push_sale(datat)
    again = _push_sale(datat)
    assert again.ok and again.skipped and again.docnum == first.docnum
    assert len(_live(datat, "ARTRN", DOCNUM=_REF)) == 1
    assert len(_live(datat, "GLJNLIT", VOUCHER=_REF)) == 3


def test_live_duplicate_purchase_is_still_skipped(datat):
    first = _push_purchase(datat)
    again = _push_purchase(datat)
    assert again.ok and again.skipped and again.docnum == first.docnum
    assert len(_live(datat, "APTRN", REFNUM="INV-9001")) == 1


# ── ③ 变异:把软删判断去掉,上面那条路必须立刻塌 ────────────────────────────


def test_counting_deleted_rows_brings_the_silent_no_op_back(datat, monkeypatch):
    """变异证据:live_rows 退回"软删行照算" → 重推立刻回到「报成功但没写」。"""
    _push_sale(datat)
    _express_delete_sale(datat, _REF)

    monkeypatch.setattr(S, "live_rows", _count_deleted_rows)
    again = _push_sale(datat)
    assert again.ok and again.skipped  # 假成功
    assert _live(datat, "ARTRN") == []  # 一个字节没写


def test_counting_deleted_rows_brings_the_purchase_no_op_back(datat, monkeypatch):
    first = _push_purchase(datat)
    _express_delete_purchase(datat, first.docnum)

    monkeypatch.setattr(S, "live_rows", _count_deleted_rows)
    again = _push_purchase(datat)
    assert again.ok and again.skipped
    assert _live(datat, "APTRN") == []


def test_readback_would_reject_the_good_invoice_if_ghosts_counted(datat, monkeypatch):
    """第二道闸的独立证据:删单重推后新旧同号并存,读回把软删旧行也算上 → GL 合计翻倍,
    好端端一张单被判「借贷不平」而回滚。只修幂等不修读回,票仍然进不去。"""
    _push_sale(datat)
    _express_delete_sale(datat, _REF)
    again = _push_sale(datat)
    assert again.ok, again.error

    total, base = Decimal("9630.00"), Decimal("9000.00")
    WS._verify(datat, _REF, total, True, base=base)  # 修后:同号幽灵在也照过

    monkeypatch.setattr(S, "live_rows", _count_deleted_rows)
    with pytest.raises(WS._WriteError, match="GL not balanced"):
        WS._verify(datat, _REF, total, True, base=base)


def test_detail_readback_would_double_count_the_ghost_lines(datat, monkeypatch):
    """第三道闸:明细读回把软删旧行数进来 → 行数 4≠2 → 回滚。"""
    _push_sale(datat, _sale_items())
    _express_delete_sale(datat, _REF)
    again = _push_sale(datat, _sale_items())
    assert again.ok, again.error

    WS._verify_detail(datat, _REF, Decimal("9000.00"), 2)

    monkeypatch.setattr(S, "live_rows", _count_deleted_rows)
    with pytest.raises(WS._WriteError, match="line count 4"):
        WS._verify_detail(datat, _REF, Decimal("9000.00"), 2)


# ── ④ 余额桶 / 计数器 / 主档 / 库存 ────────────────────────────────────────


def test_receivable_balance_goes_to_a_live_row_not_the_ghost(datat):
    """ARBAL 那条客户余额行被删掉后,新单的应收要落在新建的活行上。

    修前:金额加进 Express 根本列不出来的软删行,客户余额凭空少一截,还不报错。
    """
    _push_sale(datat)
    field = "IV5"  # 2026-05-31 落 1 月起账账套的财年第 5 期
    assert _live(datat, "ARBAL", CUSCOD="C001")[0][field] == 9630.0

    _mark_deleted(datat, "ARBAL", "CUSCOD", "C001")
    _push_sale(datat, _sale(ref_no="IV690531-002"))

    live = _live(datat, "ARBAL", CUSCOD="C001")
    assert len(live) == 1
    assert live[0][field] == 9630.0  # 新行只记新单,没有加到幽灵行上去


def test_payable_balance_goes_to_a_live_row_not_the_ghost(datat):
    _push_purchase(datat)
    field = "RR5"  # 同上:财年第 5 期,不是单号里那个日历月
    _mark_deleted(datat, "APBAL", "SUPCOD", "S001")
    _push_purchase(datat, _purchase(ref_no="INV-9002"))

    live = _live(datat, "APBAL", SUPCOD="S001")
    assert len(live) == 1
    assert live[0][field] == 1070.0


def test_document_counter_ignores_the_ghost_row(datat):
    """ISRUN:活的计数器行才 +1;软删那条一动不动(它不是 Express 在用的那条)。"""
    _seed(
        datat,
        "ISRUN",
        {"PREFIX": "RR", "JNLTYP": "04", "DOCNUM": "0000010"},
        deleted=True,
    )
    _seed(datat, "ISRUN", {"PREFIX": "RR", "JNLTYP": "04", "DOCNUM": "0000020"})

    _push_purchase(datat)
    assert [r["DOCNUM"] for r in _live(datat, "ISRUN", PREFIX="RR")] == ["0000021"]
    assert _count_all(datat, "ISRUN") == 2  # 幽灵行还在,值没被动过


def test_deleted_customer_is_not_reused_as_the_counterparty(datat):
    """会计删掉的客户档不许再被复用 —— 复用了单据就挂在 Express 里列不出来的对手方上。"""
    _seed(
        datat,
        "ARMAS",
        {"CUSCOD": "ล001", "CUSNAM": "ทดสอบ", "TAXID": "0105551234567"},
        deleted=True,
    )
    assert MC.find_customer_code(datat, "0105551234567", "ทดสอบ") is None
    assert MC.find_suspected_customer_dup(datat, "0999999999999", "ทดสอบ") is None


def test_master_readback_ignores_the_deleted_row_with_the_same_code(datat):
    """主档读回只认活行:同码的软删旧档会让校验拿旧税号判新档。"""
    _seed(datat, "ARMAS", {"CUSCOD": "ล001", "TAXID": "9999999999999"}, deleted=True)
    assert "not found" in (MC.verify_customer_row(datat, "ล001", "0105551234567") or "")


def test_item_readback_ignores_the_deleted_row_with_the_same_code(datat):
    _seed(datat, "STMAS", {"STKCOD": "PN00001", "STKTYP": "3"}, deleted=True)
    assert "not found" in (PM.verify_item_row(datat, "PN00001") or "")


def test_stock_master_write_lands_on_the_live_row(datat):
    """扣库存必须扣在活档上 —— 扣进幽灵行 = 账面有货实物没货,且不报任何错。"""
    _seed(datat, "STMAS", {"STKCOD": "PN00001", "MREMBAL": 999.0}, deleted=True)
    _seed(datat, "STMAS", {"STKCOD": "PN00001", "MREMBAL": 10.0})

    SS.write_stmas_fields(datat, "PN00001", {"MREMBAL": 5.0})

    assert [r["MREMBAL"] for r in _live(datat, "STMAS", STKCOD="PN00001")] == [5.0]
    t = dbf.Table(str(datat / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        assert [r.MREMBAL for r in t if dbf.is_deleted(r)] == [999.0]  # 幽灵行没被动
    finally:
        t.close()


# ── ⑤ 反向原则:发号必须把软删行也算上 ─────────────────────────────────────


def test_numbering_does_not_hand_out_a_deleted_document_number_again(datat):
    """删掉 RR690531-001 之后,下一张不许再叫 001 —— 那个号在会计手上/打印件上已经用过。"""
    first = _push_purchase(datat)
    assert first.docnum == "RR690531-001"

    _express_delete_purchase(datat, first.docnum)
    again = _push_purchase(datat, _purchase(ref_no="INV-9002"))
    assert again.docnum == "RR690531-002"


def test_numbering_does_not_hand_out_a_deleted_customer_code_again(datat):
    """客户码同理:ล001 删了,新客户不许再拿 ล001(旧单据仍指着这个码)。"""
    _seed(datat, "ARMAS", {"CUSCOD": "ล001", "CUSNAM": "เก่า"}, deleted=True)
    entry = _NewCustomer("ลูกค้า ใหม่")
    code = MC.ensure_customer(datat, entry, created_date=dt.date(2026, 5, 31))
    assert code != "ล001"


def test_numbering_does_not_hand_out_a_deleted_item_code_again(datat):
    _seed(datat, "STMAS", {"STKCOD": "PN00001", "STKTYP": "3"}, deleted=True)
    assert PM._next_stkcod(datat) != "PN00001"


class _NewCustomer:
    """ensure_customer 只读这几个字段(不引 sales_adapter,免得测试跟着载荷契约漂)。"""

    def __init__(self, name: str):
        self.customer_code = ""
        self.customer_name = name
        self.customer_tax_id = ""
        self.customer_prename = ""
        self.customer_address = ""
        self.lines = []
