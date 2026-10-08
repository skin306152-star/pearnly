"""DBF 销项直写器测试(镜像采购)· 合成 VFP 表 · 不依赖客户数据。

覆盖:写 IV/HS 读回比字段 / 借贷平(Dr 资产=Cr 收入+Cr 销项税)/ ISRUN IV+1 / 当日序号 /
白名单拒 / 备份回滚 / 幂等 / 零税 / reindex 可见 / 方向路由不串表族 + gold 比对真 HL。
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path

import dbf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion import dbf_schema as S  # noqa: E402
from companion import dbf_sales as WS  # noqa: E402
from companion import dbf_writer as WP  # noqa: E402

from _synth_account import seed_fiscal_year  # noqa: E402


def _sale(**over):
    p = {
        "direction": "sales",
        "doctype": "IV",
        "account_set": "DATAT",
        "docdate_be": "581215",
        "vat_period_be": "581201",
        "ref_no": "SO-9001",
        "customer": {
            "code": "C001",
            "name": "ลูกค้า ทดสอบ",
            "tax_id": "0105551234567",
            "prename": "บริษัท",
            "customer_new": False,
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


@pytest.fixture
def datat(tmp_path):
    """空 DATAT 合成账套:采购 + 销项两套表族 + 共享表。"""
    d = tmp_path / "70EXP" / "test"
    d.mkdir(parents=True)
    for name, spec in S.TABLE_SPECS.items():
        t = dbf.Table(
            str(d / f"{name}.DBF"), spec, codepage=S.CODEPAGE, dbf_type=S.DBF_TYPE
        )
        t.open(dbf.READ_WRITE)
        t.close()
    # 账套会计期间(余额表月桶的列号读它)。本模块的票是佛历 58xx = 公历 2015。
    seed_fiscal_year(d, 2015)
    # 真账套必有单位表:明细行单位码只写这套账里有的码(2026-10-08)
    from _synth_account import ISTAB_UNITS, _seed

    _seed(d, "ISTAB", [{"TABTYP": "20", "TYPCOD": c, "SHORTNAM": sh, "TYPDES": de} for c, sh, de in ISTAB_UNITS])
    return d


def _rows(d, name, **match):
    t = dbf.Table(str(d / f"{name}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        return [
            r for r in t if all((r[k] or "").strip() == v for k, v in match.items())
        ]
    finally:
        t.close()


# ── 报税单号(ISVAT.REFNUM)── 2026-07-20 修:此前误填本单 docnum ────────────
def test_isvat_refnum_is_the_printed_invoice_no(datat):
    """ภ.พ.30 出自 ISVAT,单号必须是票面那张的号,否则税局按票号核时对不上。"""
    res = WS.write_sale(
        _sale(ref_no="IV69/00475"), datat,
        allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False,
    )
    assert res.ok, res.error
    # 凭证号即票面号(2026-07-20 事故:Express 里显示 IV590531-003,与客户手上的票对不上)
    assert res.docnum == "IV69/00475"
    ar = _rows(datat, "ARTRN", DOCNUM="IV69/00475")[0]
    assert (ar.YOUREF or "").strip() == "IV69/00475"
    iv = _rows(datat, "ISVAT", DOCNUM="IV69/00475")[0]
    assert (iv.REFNUM or "").strip() == "IV69/00475"  # ภ.พ.30 报税单号
    # 换号后 6 表仍串得起来:GL 凭证与明细都挂同一个号,且借贷平
    assert _rows(datat, "GLJNL", VOUCHER="IV69/00475")
    gi = _rows(datat, "GLJNLIT", VOUCHER="IV69/00475")
    assert len(gi) == 3
    dr = sum(Decimal(str(r.AMOUNT)) for r in gi if (r.TRNTYP or "").strip() == "0")
    cr = sum(Decimal(str(r.AMOUNT)) for r in gi if (r.TRNTYP or "").strip() == "1")
    assert dr == cr == Decimal("9630.00")


def test_ref_no_over_refnum_width_is_rejected(datat):
    """票号同时进 YOUREF(30) 与 REFNUM(15) → 按严的 15 拦,不静默截断进税表。"""
    res = WS.write_sale(
        _sale(ref_no="IV69/00475-EXTRA-LONG"), datat,
        allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False,
    )
    assert not res.ok
    assert "REFNUM" in (res.error or "")


# ── 主写入 + 读回 ───────────────────────────────────────────
def test_write_sale_creates_all_rows(datat):
    res = WS.write_sale(_sale(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert res.ok, res.error
    assert res.docnum == "SO-9001"  # 凭证号=票面发票号

    ar = _rows(datat, "ARTRN", DOCNUM="SO-9001")[0]
    assert (
        ar.RECTYP or ""
    ).strip() == "3"  # 赊销 IV=3(镜像采购 RR=3·报表按 RECTYP=3 筛)
    assert (ar.FLGVAT or "").strip() == "2"  # 赊单价不含税
    assert (ar.CUSCOD or "").strip() == "C001"
    assert (ar.YOUREF or "").strip() == "SO-9001"
    assert float(ar.AMOUNT) == 9000.00  # FLGVAT=2 → AMOUNT/TOTAL/NETVAL=税前
    assert float(ar.TOTAL) == 9000.00
    assert float(ar.NETVAL) == 9000.00  # 税前
    assert float(ar.NETAMT) == 9630.00  # 含税
    assert float(ar.VATAMT) == 630.00
    assert float(ar.RCVAMT) == 0.0  # IV 赊销未收
    assert float(ar.REMAMT) == 9630.00
    # Express GUI 自填、外部直写必补的可见性字段(缺则报表/放大镜看不到)
    assert (ar.CMPLAPP or "").strip() == "Y"
    assert (ar.BILNUM or "").strip() == "~"
    assert (ar.SRV_VATTYP or "").strip() == "1"
    assert int(ar.ORGNUM or 0) == 0

    iv = _rows(datat, "ISVAT", DOCNUM="SO-9001")[0]
    assert (iv.VATREC or "").strip() == "S"  # 销项税
    assert (iv.RECTYP or "").strip() == "3"  # 赊销 ISVAT.RECTYP=3
    # 报税单号=票面那张的号(ภ.พ.30 出自本表,必须与客户手上的实体票对得上);
    # 采购侧一直如此,销项此前误填本单 docnum。
    assert (iv.REFNUM or "").strip() == "SO-9001"
    assert float(iv.AMT01) == 9000.00 and float(iv.VAT01) == 630.00

    gj = _rows(datat, "GLJNL", VOUCHER="SO-9001")[0]
    assert (gj.JNLTYP or "").strip() == "03"

    gi = {
        (r.SEQIT or "").strip(): r
        for r in _rows(datat, "GLJNLIT", VOUCHER="SO-9001")
    }
    assert set(gi) == {"2", "5", "6"}
    assert (gi["2"].ACCNUM or "").strip() == "11-03-01-00" and (
        gi["2"].TRNTYP or ""
    ).strip() == "0"  # Dr AR
    assert (gi["5"].ACCNUM or "").strip() == "41-01-00-00" and (
        gi["5"].TRNTYP or ""
    ).strip() == "1"  # Cr 收入
    assert (gi["6"].ACCNUM or "").strip() == "11-05-04-02" and (
        gi["6"].TRNTYP or ""
    ).strip() == "1"  # Cr 销项税


def test_sale_gl_balanced(datat):
    WS.write_sale(_sale(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    gi = _rows(datat, "GLJNLIT", VOUCHER="SO-9001")
    dr = sum(Decimal(str(r.AMOUNT)) for r in gi if (r.TRNTYP or "").strip() == "0")
    cr = sum(Decimal(str(r.AMOUNT)) for r in gi if (r.TRNTYP or "").strip() == "1")
    assert dr == cr == Decimal("9630.00")


def test_cash_sale_hs_received(datat):
    p = _sale(
        doctype="HS",
        ref_no="HS-1",
        lines=[
            {"acc": "11-01-01-00", "side": "D", "amount": "9630.00", "desc": "เงินสด"},
            {"acc": "41-01-00-00", "side": "C", "amount": "9000.00", "desc": "รายได้"},
            {"acc": "11-05-04-02", "side": "C", "amount": "630.00", "desc": "ภาษีขาย"},
        ],
    )
    res = WS.write_sale(p, datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert res.ok and res.docnum == "HS-1"  # 现销同样用票面号
    ar = _rows(datat, "ARTRN", DOCNUM="HS-1")[0]
    assert (ar.RECTYP or "").strip() == "1"  # 现销 HS=1
    assert (ar.FLGVAT or "").strip() == "1"  # 现单价含税 → AMOUNT=含税
    assert float(ar.AMOUNT) == 9630.00 and float(ar.NETVAL) == 9000.00
    assert float(ar.RCVAMT) == 9630.00 and float(ar.REMAMT) == 0.0  # 现销已收
    assert float(ar.CSHRCV) == 9630.00


def test_arbal_iv_month_bucket(datat):
    WS.write_sale(_sale(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    ab = _rows(datat, "ARBAL", CUSCOD="C001")[0]
    assert float(ab.IV12) == 9630.00


def test_isrun_iv_incremented(datat):
    t = dbf.Table(str(datat / "ISRUN.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    t.append({"DOCTYP": "IV", "PREFIX": "IV", "DOCNUM": "0000002", "JNLTYP": "03"})
    t.close()
    WS.write_sale(_sale(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert (_rows(datat, "ISRUN", PREFIX="IV")[0].DOCNUM or "").strip() == "0000003"


def test_sale_docnum_daily_sequence_on_fallback(datat, monkeypatch):
    """回退路径(PEARNLY_SALES_DOCNUM=seq)仍按当日序号排号 —— 一键退回旧行为。"""
    monkeypatch.setenv("PEARNLY_SALES_DOCNUM", "seq")
    t = dbf.Table(str(datat / "ARTRN.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    t.append({"RECTYP": "1", "DOCNUM": "IV581215-001", "CUSCOD": "X", "YOUREF": "OLD"})
    t.close()
    res = WS.write_sale(_sale(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert res.docnum == "IV581215-002"


def test_docnum_falls_back_when_ref_no_too_wide(datat):
    """票号超 DOCNUM C(12) → 退回自编号,但 REFNUM 仍记真实票号(报表不受影响)。"""
    res = WS.write_sale(
        _sale(ref_no="IV69/00475-XY"), datat,
        allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False,
    )
    assert res.ok, res.error
    assert res.docnum == "IV581215-001"
    iv = _rows(datat, "ISVAT", DOCNUM=res.docnum)[0]
    assert (iv.REFNUM or "").strip() == "IV69/00475-XY"


def test_reject_write_to_dir_other_than_selected(datat, tmp_path):
    # 所选 = datat,写盘目标给了别的目录 → C2 闸拒
    bad = tmp_path / "70EXP" / "PDATAT"
    bad.mkdir(parents=True)
    res = WS.write_sale(_sale(), bad, allowed_sets=("DATAT",),
                        selected_dir=str(datat), make_backup=False)
    assert not res.ok and res.error_code == WP.ERR_ACCOUNT_DIR


def test_sale_backup_and_rollback(datat, monkeypatch):
    monkeypatch.setattr(
        WS,
        "_verify",
        lambda *a, **k: (_ for _ in ()).throw(WS._WriteError("boom", WP.ERR_VERIFY)),
    )
    res = WS.write_sale(_sale(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=True)
    assert not res.ok and res.error_code == WP.ERR_VERIFY
    assert res.backup_dir  # 回滚标记保留
    assert not Path(res.backup_dir).exists()  # 回滚后快照已删(磁盘不堆积)
    assert _rows(datat, "ARTRN", DOCNUM="SO-9001") == []
    assert _rows(datat, "GLJNLIT", VOUCHER="SO-9001") == []


def test_same_sale_pushed_again_is_written_again(datat):
    """不查重:同号销项再推照写一张(会计手录 Express 也不拦,用户 2026-09-30、2026-10-08)。"""
    r1 = WS.write_sale(_sale(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    r2 = WS.write_sale(_sale(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert r1.ok and not r1.skipped
    assert r2.ok and not r2.skipped, r2.error
    assert len(_rows(datat, "ARTRN", YOUREF="SO-9001")) == 2


def test_auto_create_new_customer(datat):
    p = _sale(
        customer={
            "code": "",
            "name": "บริษัท ลูกค้าใหม่ จำกัด",
            "tax_id": "0105559876543",
            "prename": "บริษัท",
            "customer_new": True,
        },
        ref_no="NC-1",
    )
    res = WS.write_sale(p, datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert res.ok, res.error
    assert res.created_party and res.party_code
    cus = _rows(datat, "ARMAS", CUSCOD=res.party_code)[0]
    assert (
        cus.CUSNAM or ""
    ).strip() == "ลูกค้าใหม่ จำกัด"  # 去前缀 บริษัท(尾 จำกัด 保留·gold 一致)
    assert (cus.ACCNUM or "").strip() == "11-03-01-00"  # 默认应收(从借方行推)
    assert (cus.STATUS or "").strip() == "A"
    assert (cus.TAXID or "").strip() == "0105559876543"
    assert (cus.CUSTYP or "").strip() == "00"
    assert (
        _rows(datat, "ARTRN", DOCNUM=res.docnum)[0].CUSCOD or ""
    ).strip() == res.party_code


def test_suspected_dup_customer_to_manual(datat):
    # 先建客户 ABC Trading(tax 111)→ 再来一张近似名(打字差一字母)+ 不同税号的票:
    # 不静默建重复 ARMAS 户,转人工(ERR_CUSTOMER_DUP),且不落新客户行。
    first = WS.write_sale(
        _sale(customer={"code": "", "name": "ABC Trading", "tax_id": "1111111111111",
                        "prename": "", "customer_new": True}, ref_no="D-1"),
        datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert first.ok and first.created_party
    before = len(_rows(datat, "ARMAS"))

    res = WS.write_sale(
        _sale(customer={"code": "", "name": "ABC Tradng", "tax_id": "2222222222222",
                        "prename": "", "customer_new": True}, ref_no="D-2"),
        datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert not res.ok and res.error_code == WS.ERR_CUSTOMER_DUP
    assert len(_rows(datat, "ARMAS")) == before  # 没静默建重复户


def test_new_customer_idempotent_reuse(datat):
    base = dict(
        customer={
            "code": "",
            "name": "บริษัท เดียวกัน จำกัด",
            "tax_id": "0105500011122",
            "prename": "บริษัท",
            "customer_new": True,
        }
    )
    r1 = WS.write_sale(_sale(ref_no="S-1", **base), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    r2 = WS.write_sale(_sale(ref_no="S-2", **base), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert r1.ok and r2.ok
    assert r1.created_party and not r2.created_party
    assert r1.party_code == r2.party_code
    assert len(_rows(datat, "ARMAS", CUSCOD=r1.party_code)) == 1


def test_reject_customer_no_code_no_name(datat):
    res = WS.write_sale(
        _sale(customer={"code": "", "name": ""}), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False
    )
    assert not res.ok and res.error_code in (WP.ERR_VALIDATION, WS.ERR_NEW_CUSTOMER)


def test_sale_zero_vat_no_isvat(datat):
    p = _sale(
        vat_rate=0.0,
        base_amount="1000.00",
        vat_amount="0.00",
        total_amount="1000.00",
        lines=[
            {"acc": "11-03-01-00", "side": "D", "amount": "1000.00", "desc": "AR"},
            {"acc": "41-01-00-00", "side": "C", "amount": "1000.00", "desc": "รายได้"},
        ],
    )
    res = WS.write_sale(p, datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert res.ok
    assert _rows(datat, "ISVAT", DOCNUM=res.docnum) == []
    assert {
        (r.SEQIT or "").strip() for r in _rows(datat, "GLJNLIT", VOUCHER=res.docnum)
    } == {"2", "5"}


def test_reindex_row_visible(datat):
    res = WS.write_sale(_sale(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert len(_rows(datat, "ARTRN", DOCNUM=res.docnum)) == 1


# ── V1 安全明细(STCRD 直接科目行)──────────────────────────
def _sale_items(**over):
    # 行合计 9000 = 税前额(对账闸过)→ items_status=ok。
    p = _sale(
        items=[
            {"name": "สินค้า A", "qty": "2", "unit": "ลิตร", "unit_price": "3000", "amount": "6000.00"},
            {"name": "สินค้า B", "qty": "1", "unit_price": "3000", "amount": "3000.00"},
        ],
        items_status="ok",
        items_account="41-01-00-00",
    )
    p.update(over)
    return p


def test_detail_writes_stcrd_and_nxtseq(datat):
    res = WS.write_sale(_sale_items(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert res.ok, res.error
    st = _rows(datat, "STCRD", DOCNUM=res.docnum)
    assert len(st) == 2
    ar = _rows(datat, "ARTRN", DOCNUM=res.docnum)[0]
    assert (ar.NXTSEQ or "").strip() == "2"  # 必须=明细行数
    # 行字段忠实落 + 挂收入科目作直接科目行
    line1 = next(r for r in st if (r.SEQNUM or "").strip() == "1")
    assert (line1.STKCOD or "").strip() == "41-01-00-00"
    assert (line1.STKDES or "").strip() == "สินค้า A"
    assert (line1.PEOPLE or "").strip() == "C001"  # 客户码
    assert (line1.POSOPR or "").strip() == "9"
    assert float(line1.TRNQTY) == 2.0 and float(line1.UNITPR) == 3000.0
    assert float(line1.NETVAL) == 6000.0
    assert (line1.TQUCOD or "").strip() == "ลต"  # 单位映射 ลิตร→ลต


def test_detail_no_cost_no_inventory_signature(datat):
    # 证明"没动成本/库存":每行 XUNITPR/XTRNVAL=0(无 COGS 签名)。
    res = WS.write_sale(_sale_items(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    for r in _rows(datat, "STCRD", DOCNUM=res.docnum):
        assert float(r.XUNITPR) == 0.0
        assert float(r.XTRNVAL) == 0.0


def test_detail_gl_unchanged_three_lines(datat):
    # 加明细不改 GL:仍是 借资产/贷收入/贷销项税 三行,无 COGS。
    res = WS.write_sale(_sale_items(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    gi = {(r.SEQIT or "").strip() for r in _rows(datat, "GLJNLIT", VOUCHER=res.docnum)}
    assert gi == {"2", "5", "6"}  # 无 COGS/存货行


def test_items_not_ok_falls_back_header_only(datat):
    # items_status=mismatch → 不落 STCRD,退回表头模式(NXTSEQ 默认),账照常。
    p = _sale_items(items_status="mismatch")
    res = WS.write_sale(p, datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert res.ok, res.error
    assert _rows(datat, "STCRD", DOCNUM=res.docnum) == []
    assert len(_rows(datat, "ARTRN", DOCNUM=res.docnum)) == 1


def test_detail_line_count_mismatch_rolls_back(datat, monkeypatch):
    # 读回校验:STCRD 行数与 NXTSEQ 不符 → 整单回滚(注入只写一行)。
    orig = WS._write_stcrd_detail

    def _extra(account_dir, docnum, docdat, entry, depcod="", userid="BIT9"):
        # 多写一行(STCRD=3),但 NXTSEQ/detail_n=2 → 读回行数不符 → 拒。
        orig(account_dir, docnum, docdat, entry, depcod, userid=userid)
        return orig(account_dir, docnum, docdat, entry, depcod=depcod, userid=userid)

    monkeypatch.setattr(WS, "_write_stcrd_detail", _extra)
    res = WS.write_sale(_sale_items(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=True)
    assert not res.ok and res.error_code == WP.ERR_VERIFY
    assert _rows(datat, "ARTRN", DOCNUM="SO-9001") == []  # 回滚


# ── V2 item_mode 分发(非库存主档 / 回落)──────────────────────
def _sale_nonstock(**over):
    p = _sale(
        items=[
            {
                "name": "ค่าบริการล้างรถ", "qty": "1", "unit_price": "9000",
                "amount": "9000.00", "item_mode": "non_stock_item", "account": "41-01-00-00",
            },
        ],
        items_status="ok",
        items_account="41-01-00-00",
    )
    p.update(over)
    return p


def test_nonstock_mode_creates_master_and_links(datat):
    from companion import product_master as PM

    res = WS.write_sale(
        _sale_nonstock(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False
    )
    assert res.ok, res.error
    code = PM.find_item_code(datat, "ค่าบริการล้างรถ")
    assert code and code.startswith("PN")  # 建了非库存主档
    row = _rows(datat, "STCRD", DOCNUM=res.docnum)[0]
    assert (row.STKCOD or "").strip() == code  # STCRD 引用主档码(非账码)
    assert float(row.XTRNVAL) == 9000.00  # 非库存:X=净额(非零)
    assert float(row.PHYBAL or 0) == 0  # 无库存影响
    assert res.line_modes[0]["mode"] == "non_stock_item"
    assert res.line_modes[0]["reason"] == ""


def test_nonstock_defers_master_index_to_batch(datat):
    # 传 index_batch → 非库存主档照常建(STCRD 引用其码),但 STMAS/STCRD/ISACC 索引延后累积
    from companion import product_master as PM
    from companion.cdx_reindex import IndexBatch

    b = IndexBatch()
    res = WS.write_sale(
        _sale_nonstock(), datat, allowed_sets=("DATAT",), selected_dir=str(datat),
        make_backup=False, index_batch=b,
    )
    assert res.ok, res.error
    assert PM.find_item_code(datat, "ค่าบริการล้างรถ")  # 主档照常落盘(数据不延后)
    assert b._pending.get(str(datat)) == {"STCRD", "STMAS", "ISACC"}  # 索引推到批末


def test_nonstock_fallback_to_direct_with_reason(datat, monkeypatch):
    from companion import product_master

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(product_master, "ensure_item", _boom)
    res = WS.write_sale(
        _sale_nonstock(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False
    )
    assert res.ok, res.error  # 主档建失败不阻断整单
    row = _rows(datat, "STCRD", DOCNUM=res.docnum)[0]
    assert (row.STKCOD or "").strip() == "41-01-00-00"  # 回落直接科目行(STKCOD=账码)
    assert float(row.XTRNVAL) == 0  # 直接科目行 X=0
    assert res.line_modes[0]["mode"] == "direct_account"
    assert "ensure_item_failed" in res.line_modes[0]["reason"]  # 诚实记因·不静默


# ── 方向路由不串表族 ────────────────────────────────────────
def _purchase(**over):
    p = {
        "direction": "purchase",
        "doctype": "RR",
        "account_set": "DATAT",
        "docdate_be": "581215",
        "vat_period_be": "581201",
        "ref_no": "PINV-1",
        "supplier": {
            "code": "S001",
            "name": "ผู้ขาย",
            "tax_id": "0107544000108",
            "prename": "บริษัท",
            "supplier_new": False,
        },
        "vat_rate": 7.0,
        "base_amount": "1000.00",
        "vat_amount": "70.00",
        "total_amount": "1070.00",
        "lines": [
            {"acc": "53-03-03-01", "side": "D", "amount": "1000.00", "desc": "ซื้อ"},
            {"acc": "11-05-04-04", "side": "D", "amount": "70.00", "desc": "ภาษีซื้อ"},
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


def test_direction_does_not_cross_table_families(datat):
    sres = WS.write_sale(_sale(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    pres = WP.write_purchase(_purchase(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert sres.ok and pres.ok
    # 销项进 ARTRN 不进 APTRN;采购进 APTRN 不进 ARTRN。
    assert len(_rows(datat, "ARTRN", DOCNUM=sres.docnum)) == 1
    assert _rows(datat, "APTRN", DOCNUM=sres.docnum) == []
    assert len(_rows(datat, "APTRN", DOCNUM=pres.docnum)) == 1
    assert _rows(datat, "ARTRN", DOCNUM=pres.docnum) == []


def test_dispatch_routes_by_direction(datat):
    from companion.config import CompanionConfig
    from companion.main import _handle_dbf_task

    class _QC:
        def __init__(self):
            self.ok = None
            self.docnum = None
            self.fail = None

        def ack_success(self, tid, extracted_invoice_no=None, line_modes=None, meta=None):
            self.ok = tid
            self.docnum = extracted_invoice_no

        def ack_failure(self, tid, err, error_code=None, outcome=None, meta=None):
            self.fail = (tid, error_code)

    class _Task:
        def __init__(self, payload):
            self.id = "t1"
            self.payload = payload

    # 所选账套锁定(配对后):账套名 DATAT + 目录 datat(account_company 留空 → 公司证跳过)
    cfg = CompanionConfig(method="dbf", account_dir=str(datat), account_set="DATAT")
    qc = _QC()
    _handle_dbf_task(cfg, qc, _Task(_sale()))
    assert qc.docnum == "SO-9001", qc.fail
    assert len(_rows(datat, "ARTRN", DOCNUM="SO-9001")) == 1


# ── gold 比对(真 DATAT 副本·无则 skip)──────────────────────
def _gold_dir():
    p = os.environ.get("PEARNLY_DATAT_COPY") or "D:/datat_dev_copy"
    d = Path(p)
    return d if (d / "ARTRN.DBF").exists() else None


@pytest.mark.skipif(
    _gold_dir() is None, reason="no DATAT copy (set PEARNLY_DATAT_COPY)"
)
def test_gold_sales_assumptions_hold():
    """验证销项字段语义假设与 Express 实写(现销 HL580301-001·3 行非库存)一致。"""
    d = _gold_dir()
    DOC = "HL580301-001"
    ar = _rows(d, "ARTRN", DOCNUM=DOC)[0]
    amount, netval, vat, net = (
        Decimal(str(ar.AMOUNT)),
        Decimal(str(ar.NETVAL)),
        Decimal(str(ar.VATAMT)),
        Decimal(str(ar.NETAMT)),
    )
    assert (ar.RECTYP or "").strip() == "1"
    assert amount == net  # AMOUNT = 含税
    assert abs(netval + vat - net) <= Decimal("0.02")  # NETVAL=税前, NETAMT=含税
    iv = [r for r in _rows(d, "ISVAT", DOCNUM=DOC) if (r.VATREC or "").strip() == "S"][
        0
    ]
    assert (iv.REFNUM or "").strip() == DOC  # ISVAT.REFNUM = 本单号
    assert abs(Decimal(str(iv.AMT01)) - netval) <= Decimal("0.02")  # AMT01 = 税前
    gi = {(r.SEQIT or "").strip(): r for r in _rows(d, "GLJNLIT", VOUCHER=DOC)}
    assert {"2", "5", "6"}.issubset(set(gi))
    assert (gi["2"].TRNTYP or "").strip() == "0"  # 借 资产
    assert (gi["5"].TRNTYP or "").strip() == "1"  # 贷 收入
    assert (gi["6"].TRNTYP or "").strip() == "1"  # 贷 销项税
    assert (_rows(d, "GLJNL", VOUCHER=DOC)[0].JNLTYP or "").strip() == "03"


def test_same_tax_multi_branch_to_manual_and_branch_resolves(datat):
    # 连锁同税号三档(总店在前):名字/分店号定不出唯一 → ERR_CUSTOMER_AMBIGUOUS 转人工,
    # 不写单也不建第 4 个档;带分店号则定位分店档正常落单(2026-08-06 Big C 现场)。
    for ref, name, org in (
        ("S-0", "บิ๊กซี สำนักงานใหญ่", "0"),
        ("S-1", "บิ๊กซี (สาขา-พลับพลาไชย)", "18"),
        ("S-2", "บิ๊กซี (สาขา-ซอยประชุม)", "54"),
    ):
        first = WS.write_sale(
            _sale(customer={"code": "", "name": name, "tax_id": "0107536000633",
                            "prename": "", "customer_new": True, "branch": org},
                  ref_no=ref),
            datat, allowed_sets=("DATAT",), selected_dir=str(datat),
            make_backup=False)
        assert first.ok
    before = len(_rows(datat, "ARMAS"))

    ambiguous = WS.write_sale(
        _sale(customer={"code": "", "name": "บิ๊กซี ชื่อไม่ตรงเลย",
                        "tax_id": "0107536000633", "prename": "",
                        "customer_new": True}, ref_no="S-3"),
        datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert not ambiguous.ok and ambiguous.error_code == WS.ERR_CUSTOMER_AMBIGUOUS
    assert len(_rows(datat, "ARMAS")) == before

    by_branch = WS.write_sale(
        _sale(customer={"code": "", "name": "บิ๊กซี ชื่อไม่ตรงเลย",
                        "tax_id": "0107536000633", "prename": "",
                        "customer_new": True, "branch": "18"}, ref_no="S-4"),
        datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert by_branch.ok and not by_branch.created_party
    hit = _rows(datat, "ARTRN", DOCNUM=by_branch.docnum)[0]
    branch_row = _rows(datat, "ARMAS", CUSCOD=(hit.CUSCOD or "").strip())[0]
    assert int(branch_row.ORGNUM) == 18


def test_branch_customer_number_lands_on_voucher_and_vat_record(datat):
    """连锁客户一个分店一条档:凭证表头与销项税记录的分店号跟着客户档(用户 2026-09-27)。"""
    t = dbf.Table(str(datat / "ARMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        t.append({"CUSCOD": "C018", "CUSNAM": "บิ๊กซี", "TAXID": "0107536000633", "ORGNUM": 18, "STATUS": "A"})
    finally:
        t.close()
    sale = _sale(ref_no="SO-BR18", customer={"code": "", "name": "บิ๊กซี", "tax_id": "0107536000633",
                                              "prename": "บริษัท", "customer_new": False, "branch": "00018"})
    res = WS.write_sale(sale, datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert res.ok, res.error
    assert int(_rows(datat, "ARTRN", DOCNUM=res.docnum)[0].ORGNUM) == 18
    assert int(_rows(datat, "ISVAT", DOCNUM=res.docnum)[0].ORGNUM) == 18


def _hand_entered(d, docnum, *, netamt=None):
    """把已写进去的那张单改成会计手录的样子:YOUREF 为空(幂等闸认不出),金额可改。"""
    t = dbf.Table(str(d / "ARTRN.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        for r in t:
            if (r.DOCNUM or "").strip() == docnum:
                with r:
                    r.YOUREF = ""
                    if netamt is not None:
                        r.NETAMT = netamt
    finally:
        t.close()


@pytest.mark.parametrize("old_total", [None, 1234.0])
def test_push_goes_through_when_express_already_has_the_same_invoice_no(datat, old_total):
    # erpcowork 2026-09-30:IV69/08-xxx 会计早已手录进 Express,推送时读回把旧单的明细/分录
    # 一起数进来 → 行数翻倍 → 整单回滚。用户定:要推就推,不查重、不拦,只核对本次写入的行。
    first = WS.write_sale(_sale_items(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert first.ok, first.error
    _hand_entered(datat, first.docnum, netamt=old_total)
    again = WS.write_sale(_sale_items(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert again.ok and not again.skipped, again.error
    assert again.docnum == first.docnum  # 凭证号仍是票面发票号
    assert len(_rows(datat, "ARTRN", DOCNUM=first.docnum)) == 2
    assert len(_rows(datat, "STCRD", DOCNUM=first.docnum)) == 4


def test_receivable_line_uses_the_customers_own_account(datat):
    """2026-10-08:赊销应收行记客户档上挂的科目(Express 自己过账就这样),不再一律用账套默认应收科目。"""
    from _synth_account import _seed

    _seed(datat, "GLACC", [{"ACCNUM": "11-03-02-00", "ACCNAM": "ลูกหนี้ พ.ก.ง.", "ACCTYP": "0", "GROUP": "1"}])
    _seed(datat, "ARMAS", [{"CUSCOD": "C001", "CUSNAM": "ลูกค้า ทดสอบ", "ACCNUM": "11-03-02-00"}])
    res = WS.write_sale(_sale(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert res.ok, res.error
    debit = [r for r in _rows(datat, "GLJNLIT", VOUCHER="SO-9001") if (r.TRNTYP or "").strip() == S.TRNTYP_DEBIT]
    assert [(r.ACCNUM or "").strip() for r in debit] == ["11-03-02-00"]


def test_cash_sale_debits_the_cash_account_not_receivables(datat):
    import account_rules_fixture as F

    F.isinfo(datat, ACCNUM00="11-01-01-00")
    F.glacc(datat, ("11-01-01-00", "เงินสด"))
    res = WS.write_sale(_sale(doctype="HS"), datat, allowed_sets=("DATAT",), selected_dir=str(datat),
                        make_backup=False)
    assert res.ok, res.error
    debit = [r for r in _rows(datat, "GLJNLIT", VOUCHER="SO-9001") if (r.TRNTYP or "").strip() == S.TRNTYP_DEBIT]
    assert [(r.ACCNUM or "").strip() for r in debit] == ["11-01-01-00"]


def test_creating_the_missing_cash_customer_counts_as_a_new_master(datat):
    """云端给现金客户码 เงินสด、账套里没有:建档后回执要标新建主档(才会重建 ARMAS 索引)。"""
    import account_rules_fixture as F
    from companion import master_create as MC

    F.isinfo(datat, ACCNUM00="11-01-01-00", ACCNUM02="11-03-01-00")
    F.glacc(datat, ("11-01-01-00", "เงินสด"), ("11-03-01-00", "ลูกหนี้การค้า"))
    sale = _sale(doctype="HS", customer={"code": MC.CASH_CUSTOMER, "name": MC.CASH_CUSTOMER, "tax_id": "",
                                         "prename": "", "customer_new": False})
    res = WS.write_sale(sale, datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert res.ok, res.error
    assert res.created_party is True
    assert [(r.CUSCOD or "").strip() for r in _rows(datat, "ARMAS")] == [MC.CASH_CUSTOMER]
