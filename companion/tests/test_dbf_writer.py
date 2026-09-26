"""DBF 直写器测试。

主体对**合成 VFP 表**(据 dbf_schema 建)跑 → 不依赖客户数据,CI 可跑:写一张 RR →
读回比字段 / 借贷平衡 / ISRUN+1 / 当日序号 / 白名单拒 / 备份回滚 / 幂等 / reindex 可见。

末尾一个 gold 比对(对真 DATAT 只读副本)验证我们的字段语义假设与 Express 实写一致;
无副本(env PEARNLY_DATAT_COPY 未设且默认路径不存在)则 skip。
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
from companion import dbf_writer as W  # noqa: E402

from _synth_account import seed_fiscal_year  # noqa: E402


def _payload(**over):
    p = {
        "doctype": "RR",
        "account_set": "DATAT",
        "docdate_be": "581231",
        "vat_period_be": "581201",
        "ref_no": "INV-9001",
        "supplier": {
            "code": "S001",
            "name": "บริษัท ปตท จำกัด",
            "tax_id": "0107544000108",
            "prename": "บริษัท",
            "supplier_new": False,
        },
        "vat_rate": 7.0,
        "base_amount": "388804.80",
        "vat_amount": "27216.34",
        "total_amount": "416021.14",
        "lines": [
            {"acc": "11-04-02-00", "side": "D", "amount": "388804.80", "desc": "ซื้อ"},
            {
                "acc": "11-05-04-01",
                "side": "D",
                "amount": "27216.34",
                "desc": "ภาษีซื้อ",
            },
            {
                "acc": "21-02-01-00",
                "side": "C",
                "amount": "416021.14",
                "desc": "เจ้าหนี้",
            },
        ],
    }
    p.update(over)
    return p


@pytest.fixture
def datat(tmp_path):
    """建空 DATAT 合成账套(…/70EXP/test),6 表齐。"""
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
    return d


def _rows(d, name, **match):
    t = dbf.Table(str(d / f"{name}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        out = []
        for r in t:
            if all((r[k] or "").strip() == v for k, v in match.items()):
                out.append(r)
        return out
    finally:
        t.close()


# ── 所选账套目录闸(C2)──────────────────────────────────────
def test_account_dir_matches_selected(tmp_path):
    sel = tmp_path / "70EXP" / "test"
    assert W.account_dir_allowed(sel, str(sel))  # 目标 == 所选 → 放行
    assert not W.account_dir_allowed(tmp_path / "70EXP" / "PDATAT", str(sel))  # 目标 != 所选 → 拒
    assert not W.account_dir_allowed(sel, "")  # 缺所选 → fail-safe 拒
    # 归一:正斜杠/大小写差异仍匹配
    assert W.account_dir_allowed("//x/70EXP/test", r"\\x\70EXP\TEST")


def test_reject_write_to_dir_other_than_selected(datat, tmp_path):
    # 所选 = datat,写盘目标给了别的目录 → C2 闸拒(在落盘前返回,不碰 DBF)
    bad = tmp_path / "70EXP" / "PDATAT"
    bad.mkdir(parents=True)
    res = W.write_purchase(_payload(), bad, allowed_sets=("DATAT",),
                           selected_dir=str(datat), make_backup=False)
    assert not res.ok and res.error_code == W.ERR_ACCOUNT_DIR


def test_reject_write_when_no_selection(datat):
    # 缺所选(selected_dir 空)→ fail-safe 拒,绝不瞎写
    res = W.write_purchase(_payload(), datat, allowed_sets=("DATAT",), make_backup=False)
    assert not res.ok and res.error_code == W.ERR_ACCOUNT_DIR


# ── V1 安全明细(采购 STCRD 直接科目行)──────────────────────
def _payload_items(**over):
    # 行合计 388804.80 = 税前额 → items_status=ok。
    p = _payload(
        items=[
            {"name": "น้ำมันดีเซล", "qty": "16000", "unit": "ลิตร",
             "unit_price": "24.3003", "amount": "388804.80"},
        ],
        items_status="ok",
        items_account="11-04-02-00",
    )
    p.update(over)
    return p


def test_purchase_detail_writes_stcrd_and_nxtseq(datat):
    res = W.write_purchase(_payload_items(), datat, allowed_sets=("DATAT",),
                           selected_dir=str(datat), make_backup=False)
    assert res.ok, res.error
    st = _rows(datat, "STCRD", DOCNUM=res.docnum)
    assert len(st) == 1
    ap = _rows(datat, "APTRN", DOCNUM=res.docnum)[0]
    assert (ap.NXTSEQ or "").strip() == "1"
    assert (st[0].STKCOD or "").strip() == "11-04-02-00"  # 挂采购科目
    assert (st[0].PEOPLE or "").strip() == "S001"  # 供应商码
    assert (st[0].STKDES or "").strip() == "น้ำมันดีเซล"
    assert float(st[0].XUNITPR) == 0.0 and float(st[0].XTRNVAL) == 0.0  # 无成本签名


def test_purchase_defers_master_index_to_batch(datat):
    # 传 index_batch → 主档(STCRD/STMAS/ISACC)索引延后累积,逐票不再随写重建;单据照样写成并回查通过
    from companion.cdx_reindex import IndexBatch

    b = IndexBatch()
    res = W.write_purchase(_payload_items(), datat, allowed_sets=("DATAT",),
                           selected_dir=str(datat), make_backup=False, index_batch=b)
    assert res.ok, res.error
    assert _rows(datat, "STCRD", DOCNUM=res.docnum)  # 明细照常落盘
    assert b._pending.get(str(datat)) == {"STCRD", "STMAS", "ISACC"}  # 主档索引被推到批末


def test_purchase_no_batch_keeps_per_doc_reindex(datat):
    # 不传 batch → 旧行为:逐票随写重建主档,仍成功(向后兼容 headless/测试)
    res = W.write_purchase(_payload_items(), datat, allowed_sets=("DATAT",),
                           selected_dir=str(datat), make_backup=False)
    assert res.ok, res.error


def test_purchase_items_not_ok_header_only(datat):
    res = W.write_purchase(_payload_items(items_status="incomplete"), datat,
                           allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert res.ok and _rows(datat, "STCRD", DOCNUM=res.docnum) == []


def _write_isinfo(d, company):
    t = dbf.Table(str(d / "ISINFO.DBF"), "THINAM C(40); TAXID C(15)",
                  codepage=S.CODEPAGE, dbf_type=S.DBF_TYPE)
    t.open(dbf.READ_WRITE)
    t.append({"THINAM": company})
    t.close()


def test_company_leg_rejects_mismatch(datat):
    # 三重一致性闸第三证:目录 ISINFO 公司 != 期望公司 → 拒(防同尾目录串账套)
    _write_isinfo(datat, "บริษัท ก จำกัด")
    res = W.write_purchase(_payload(), datat, allowed_sets=("DATAT",),
                           selected_dir=str(datat), expected_company="บริษัท ข จำกัด",
                           make_backup=False)
    assert not res.ok and res.error_code == W.ERR_COMPANY


def test_company_leg_passes_when_match(datat):
    _write_isinfo(datat, "บริษัท\xa0มานะชัยบริการ\xa0จำกัด")
    res = W.write_purchase(_payload(), datat, allowed_sets=("DATAT",),
                           selected_dir=str(datat),
                           expected_company="บริษัท มานะชัยบริการ จำกัด", make_backup=False)
    assert res.ok, res.error


# ── 主写入 + 读回 ───────────────────────────────────────────
def test_write_creates_all_rows(datat):
    res = W.write_purchase(_payload(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert res.ok, res.error
    assert res.docnum == "RR581231-001"

    ap = _rows(datat, "APTRN", DOCNUM="RR581231-001")[0]
    assert (ap.RECTYP or "").strip() == "3"
    assert (ap.SUPCOD or "").strip() == "S001"
    assert (ap.REFNUM or "").strip() == "INV-9001"
    assert float(ap.AMOUNT) == 388804.80  # 税前
    assert float(ap.TOTAL) == 388804.80  # TOTAL=税前(Express 口径)
    assert float(ap.VATAMT) == 27216.34
    assert float(ap.NETAMT) == 416021.14  # 含税
    assert float(ap.REMAMT) == 416021.14  # RR 未付
    assert float(ap.PAYAMT) == 0.0
    assert ap.DOCDAT.year == 2015 and ap.DOCDAT.month == 12 and ap.DOCDAT.day == 31
    # 赊购价不含税 + Express GUI 自填、外部直写必补的可见性字段
    assert (ap.FLGVAT or "").strip() == "2"
    assert (ap.CMPLAPP or "").strip() == "Y"  # 审核标志(报表可见性关键)
    assert (ap.BILNUM or "").strip() == "~"
    assert (ap.SRV_VATTYP or "").strip() == "1"
    assert (ap.PVATPRORAT or "").strip() == "-"
    assert int(ap.ORGNUM or 0) == 0

    iv = _rows(datat, "ISVAT", DOCNUM="RR581231-001")[0]
    assert (iv.VATREC or "").strip() == "P"
    assert (iv.TAXID or "").strip() == "0107544000108"
    assert float(iv.AMT01) == 388804.80 and float(iv.VAT01) == 27216.34

    gj = _rows(datat, "GLJNL", VOUCHER="RR581231-001")[0]
    assert (gj.JNLTYP or "").strip() == "04"

    gi = _rows(datat, "GLJNLIT", VOUCHER="RR581231-001")
    by_seq = {(r.SEQIT or "").strip(): r for r in gi}
    assert set(by_seq) == {"0", "3", "6"}
    assert (by_seq["0"].ACCNUM or "").strip() == "11-04-02-00" and (
        by_seq["0"].TRNTYP or ""
    ).strip() == "0"
    assert (by_seq["3"].ACCNUM or "").strip() == "11-05-04-01" and (
        by_seq["3"].TRNTYP or ""
    ).strip() == "0"
    assert (by_seq["6"].ACCNUM or "").strip() == "21-02-01-00" and (
        by_seq["6"].TRNTYP or ""
    ).strip() == "1"
    assert float(by_seq["6"].AMOUNT) == 416021.14


def test_cash_purchase_hp(datat):
    res = W.write_purchase(
        _payload(doctype="HP", ref_no="HP-1"), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False
    )
    assert res.ok and res.docnum == "HP581231-001"
    ap = _rows(datat, "APTRN", DOCNUM="HP581231-001")[0]
    assert (ap.RECTYP or "").strip() == "1"  # 现购 HP=1
    assert (ap.FLGVAT or "").strip() == "1"  # 现单价含税 → AMOUNT=含税
    assert float(ap.AMOUNT) == 416021.14 and float(ap.NETVAL) == 388804.80
    assert float(ap.PAYAMT) == 416021.14 and float(ap.REMAMT) == 0.0  # 现购已付
    assert float(ap.CSHPAY) == 416021.14
    # APBAL 按单别分桶:现购落 HP 月桶(BANKREC 真账反证),错挂 RR 桶=赊购月报虚高。
    ab = _rows(datat, "APBAL", SUPCOD="S001")[0]
    assert float(ab.HP12) == 416021.14
    assert float(ab.RR12 or 0) == 0.0


def test_gl_balanced(datat):
    W.write_purchase(_payload(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    gi = _rows(datat, "GLJNLIT", VOUCHER="RR581231-001")
    dr = sum(Decimal(str(r.AMOUNT)) for r in gi if (r.TRNTYP or "").strip() == "0")
    cr = sum(Decimal(str(r.AMOUNT)) for r in gi if (r.TRNTYP or "").strip() == "1")
    assert dr == cr == Decimal("416021.14")


def test_apbal_accumulates_month_bucket(datat):
    W.write_purchase(_payload(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    ab = _rows(datat, "APBAL", SUPCOD="S001")[0]
    assert float(ab.RR12) == 416021.14  # 赊购 12 月桶
    assert float(ab.HP12 or 0) == 0.0  # 现购桶不沾赊购单


def test_isrun_incremented(datat):
    t = dbf.Table(str(datat / "ISRUN.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    t.append({"DOCTYP": "RR", "PREFIX": "RR", "DOCNUM": "0000024", "JNLTYP": "04"})
    t.close()
    W.write_purchase(_payload(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    r = _rows(datat, "ISRUN", PREFIX="RR")[0]
    assert (r.DOCNUM or "").strip() == "0000025"


def test_docnum_daily_sequence(datat):
    t = dbf.Table(str(datat / "APTRN.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    t.append({"RECTYP": "3", "DOCNUM": "RR581231-001", "SUPCOD": "X", "REFNUM": "OLD"})
    t.close()
    res = W.write_purchase(_payload(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert res.docnum == "RR581231-002"


# ── 幂等 ────────────────────────────────────────────────────
def test_idempotent_no_double_write(datat):
    r1 = W.write_purchase(_payload(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert r1.ok and not r1.skipped
    r2 = W.write_purchase(_payload(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert r2.ok and r2.skipped and r2.docnum == r1.docnum
    assert len(_rows(datat, "APTRN", REFNUM="INV-9001")) == 1


# ── 备份 + 回滚 ─────────────────────────────────────────────
def test_backup_and_rollback_on_failure(datat, monkeypatch):
    monkeypatch.setattr(
        W,
        "_verify",
        lambda *a, **k: (_ for _ in ()).throw(W._WriteError("boom", W.ERR_VERIFY)),
    )
    res = W.write_purchase(_payload(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=True)
    assert not res.ok and res.error_code == W.ERR_VERIFY
    assert res.backup_dir  # 回滚标记保留(供云端 rolled_back 元数据)
    assert not Path(res.backup_dir).exists()  # 回滚后快照已删(磁盘不堆积)
    # 回滚后账套无残留行
    assert _rows(datat, "APTRN", DOCNUM="RR581231-001") == []
    assert _rows(datat, "GLJNLIT", VOUCHER="RR581231-001") == []


def test_backup_discarded_on_success(datat):
    """写盘成功 → per-write 快照即删,账套盘不堆积(磁盘恒定)。"""
    before = {p.name for p in datat.parent.iterdir()}
    res = W.write_purchase(_payload(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=True)
    assert res.ok, res.error
    assert res.backup_dir is None  # 成功不返快照路径(已删)
    leftover = [n for n in ({p.name for p in datat.parent.iterdir()} - before) if "pearnly_bak" in n]
    assert leftover == []  # 无遗留快照目录


# ── 选择性备份(只备会写的表 · 治网络盘整目录 copytree 慢)──────
def test_selective_backup_skips_unrelated_files(datat):
    # 账套里塞一个不在 allowlist 的大文件 + 一个 allowlist 内但合成账套没建的表文件
    (datat / "STCRD.DBF").write_bytes(b"stock")  # allowlist 含且存在 → 应备
    (datat / "UNRELATED.DBF").write_bytes(b"x" * 4096)  # 不在 allowlist → 不应备
    dest = W._backup(datat, S.BACKUP_TABLES)
    names = {p.name for p in dest.iterdir()}
    assert "APTRN.DBF" in names  # 采购写表
    assert "APMAS.DBF" in names  # 自建主档表(漏备则建档回滚不全)
    assert "STCRD.DBF" in names  # allowlist 内且存在
    assert "UNRELATED.DBF" not in names  # 整目录 copytree 才会带上它
    assert len(names) < len(list(datat.iterdir()))  # 严格少于整目录


def test_selective_backup_default_full_copy(datat):
    # tables=None 维持整目录复制(向后兼容)
    (datat / "UNRELATED.DBF").write_bytes(b"x")
    dest = W._backup(datat)
    assert (dest / "UNRELATED.DBF").exists()


# ── 新供应商自动建档(原拒写 → 现 APMAS 建档)─────────────────
def test_auto_create_new_supplier(datat):
    p = _payload(
        supplier={
            "code": "",
            "name": "ร้านทดสอบใหม่",
            "tax_id": "0999888777666",
            "prename": "ร้าน",
            "supplier_new": True,
        },
        ref_no="NEW-1",
    )
    res = W.write_purchase(p, datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert res.ok, res.error
    assert res.created_party and res.party_code
    sup = _rows(datat, "APMAS", SUPCOD=res.party_code)[0]
    assert (sup.SUPNAM or "").strip() == "ทดสอบใหม่"  # 去法人前缀 ร้าน
    assert (sup.ACCNUM or "").strip() == "21-02-01-00"  # 默认应付(从贷方行推)
    assert (sup.STATUS or "").strip() == "A"  # 可见性关键
    assert (sup.TAXID or "").strip() == "0999888777666"
    assert (sup.SUPTYP or "").strip() == "00"
    ap = _rows(datat, "APTRN", DOCNUM=res.docnum)[0]
    assert (ap.SUPCOD or "").strip() == res.party_code  # 单据用新建码过账


def test_new_supplier_idempotent_reuse(datat):
    """同一新供应商两张票 → 只建一次档,第二张复用同码。"""
    base = dict(
        supplier={
            "code": "",
            "name": "ร้านเดียวกัน",
            "tax_id": "0111222333444",
            "prename": "ร้าน",
            "supplier_new": True,
        }
    )
    r1 = W.write_purchase(_payload(ref_no="A-1", **base), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    r2 = W.write_purchase(_payload(ref_no="A-2", **base), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert r1.ok and r2.ok
    assert r1.created_party and not r2.created_party  # 第二次不再建
    assert r1.party_code == r2.party_code
    assert len(_rows(datat, "APMAS", SUPCOD=r1.party_code)) == 1


def test_reject_supplier_no_code_no_name(datat):
    res = W.write_purchase(
        _payload(supplier={"code": "", "name": ""}), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False
    )
    assert not res.ok and res.error_code in (W.ERR_VALIDATION, W.ERR_NEW_SUPPLIER)


def test_reject_overlong_ref_no(datat):
    # REFNUM 是 C(15);16 字节须写前拒,不进 append 才不需回滚。
    res = W.write_purchase(
        _payload(ref_no="PEARNLY-TEST-001"), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False
    )
    assert not res.ok and res.error_code == W.ERR_FIELD_WIDTH
    assert _rows(datat, "APTRN", REFNUM="PEARNLY-TEST-001") == []


# ── 零税 ────────────────────────────────────────────────────
def test_zero_vat_no_isvat(datat):
    p = _payload(
        vat_rate=0.0,
        base_amount="1000.00",
        vat_amount="0.00",
        total_amount="1000.00",
        lines=[
            {"acc": "11-04-02-00", "side": "D", "amount": "1000.00", "desc": "ซื้อ"},
            {
                "acc": "21-02-01-00",
                "side": "C",
                "amount": "1000.00",
                "desc": "เจ้าหนี้",
            },
        ],
    )
    res = W.write_purchase(p, datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert res.ok
    assert _rows(datat, "ISVAT", DOCNUM=res.docnum) == []
    gi = _rows(datat, "GLJNLIT", VOUCHER=res.docnum)
    assert {(r.SEQIT or "").strip() for r in gi} == {"0", "6"}


# ── reindex 后新行可见(重开表) ────────────────────────────
def test_reindex_row_visible(datat):
    res = W.write_purchase(_payload(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert len(_rows(datat, "APTRN", DOCNUM=res.docnum)) == 1


# ── gold 比对(真 DATAT 副本·无则 skip)────────────────────
def _gold_dir():
    p = os.environ.get("PEARNLY_DATAT_COPY") or "D:/datat_dev_copy"
    d = Path(p)
    return d if (d / "APTRN.DBF").exists() else None


@pytest.mark.skipif(
    _gold_dir() is None, reason="no DATAT copy (set PEARNLY_DATAT_COPY)"
)
def test_gold_field_assumptions_hold():
    """验证我们的字段语义假设与 Express 实写(RR580101-001/PTT)一致。"""
    d = _gold_dir()
    DOC = "RR580101-001"
    ap = next(r for r in _rows(d, "APTRN", DOCNUM=DOC))
    base, vat, net = (
        Decimal(str(ap.AMOUNT)),
        Decimal(str(ap.VATAMT)),
        Decimal(str(ap.NETAMT)),
    )
    assert (ap.RECTYP or "").strip() == "3"
    assert Decimal(str(ap.TOTAL)) == base  # TOTAL = 税前
    assert abs(base + vat - net) <= Decimal("0.02")  # NETAMT = 含税
    iv = next(r for r in _rows(d, "ISVAT", DOCNUM=DOC))
    assert (iv.VATREC or "").strip() == "P"
    gi = {(r.SEQIT or "").strip(): r for r in _rows(d, "GLJNLIT", VOUCHER=DOC)}
    assert set(gi) == {"0", "3", "6"}
    assert (gi["0"].TRNTYP or "").strip() == "0"  # 采购借
    assert (gi["3"].TRNTYP or "").strip() == "0"  # 进项税借
    assert (gi["6"].TRNTYP or "").strip() == "1"  # 应付贷
