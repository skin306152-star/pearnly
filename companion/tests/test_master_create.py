# -*- coding: utf-8 -*-
"""自动建档(APMAS/ARMAS)单测 · 合成 VFP 表 · 不依赖客户数据。

覆盖:编码段内自增 + 冲突跳过 / 去法人前缀 / 幂等(税号、名字)/ 默认科目从分录推 /
gold 字段(STATUS='A'/SUPTYP='00'/ACCNUM)。
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import dbf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion import dbf_schema as S  # noqa: E402
from companion import master_create as M  # noqa: E402
from companion.purchase_adapter import build_purchase_entry  # noqa: E402
from companion.sales_adapter import build_sales_entry  # noqa: E402

_TODAY = dt.date(2015, 12, 20)


@pytest.fixture
def datat(tmp_path):
    d = tmp_path / "70EXP" / "test"
    d.mkdir(parents=True)
    for name in ("APMAS", "ARMAS"):
        t = dbf.Table(
            str(d / f"{name}.DBF"),
            S.TABLE_SPECS[name],
            codepage=S.CODEPAGE,
            dbf_type=S.DBF_TYPE,
        )
        t.open(dbf.READ_WRITE)
        t.close()
    return d


def _seed(d, table, codefield, codes):
    t = dbf.Table(str(d / f"{table}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    for c in codes:
        t.append({codefield: c, "STATUS": "A"})
    t.close()


def _sup_entry(**over):
    p = {
        "doctype": "RR",
        "account_set": "DATAT",
        "docdate_be": "581220",
        "vat_period_be": "581201",
        "ref_no": "R-1",
        "vat_rate": 7.0,
        "base_amount": "1000.00",
        "vat_amount": "70.00",
        "total_amount": "1070.00",
        "supplier": {
            "code": "",
            "name": "บริษัท ปตท จำกัด",
            "tax_id": "0107544000108",
            "prename": "บริษัท",
            "supplier_new": True,
        },
        "lines": [
            {"acc": "53-01-00-00", "side": "D", "amount": "1000.00", "desc": "x"},
            {"acc": "11-05-04-01", "side": "D", "amount": "70.00", "desc": "vat"},
            {"acc": "21-02-01-00", "side": "C", "amount": "1070.00", "desc": "ap"},
        ],
    }
    p["supplier"].update(over.pop("supplier", {}))
    p.update(over)
    return build_purchase_entry(p)


# ── 纯函数 ──────────────────────────────────────────────────
def test_strip_prename():
    assert M._strip_prename("บริษัท ปตท จำกัด") == "ปตท จำกัด"
    assert M._strip_prename("หจก. กิจรุ่งเรือง") == "กิจรุ่งเรือง"
    assert M._strip_prename("ABC Trading") == "ABC Trading"


def test_next_code_increments_per_prefix():
    assert M._next_code(["ป001", "ป002", "ก001"], "ป") == "ป003"
    assert M._next_code(["ป001", "ก005"], "ก") == "ก006"
    assert M._next_code([], "อ") == "อ001"


def test_next_code_skips_collision():
    # 已有 ป002 占位(乱序)→ 下一个跳过它
    assert M._next_code(["ป001", "ป003"], "ป") == "ป004"
    assert M._next_code(["ป001", "ป002", "ป003"], "ป") == "ป004"


def test_default_acc_from_lines():
    lines = [{"acc": "53-01", "side": "D"}, {"acc": "21-02-01-00", "side": "C"}]
    assert M._ap_acc_from_lines(lines, "X") == "21-02-01-00"
    sl = [{"acc": "11-03-01-00", "side": "D"}, {"acc": "41-01", "side": "C"}]
    assert M._ar_acc_from_lines(sl, "X") == "11-03-01-00"


# ── 建档(对合成表) ────────────────────────────────────────
def test_create_supplier_gold_fields(datat):
    entry = _sup_entry()
    code = M.ensure_supplier(datat, entry, created_date=_TODAY)
    assert code == "ป001"  # 核心名 ปตท → 首字符 ป
    t = dbf.Table(str(datat / "APMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    r = next(x for x in t if (x.SUPCOD or "").strip() == code)
    assert (r.SUPNAM or "").strip() == "ปตท จำกัด"
    assert (r.PRENAM or "").strip() == "บริษัท"
    assert (r.ACCNUM or "").strip() == "21-02-01-00"  # 从贷方行推
    assert (r.STATUS or "").strip() == "A"
    assert (r.SUPTYP or "").strip() == "00"
    assert (r.TAXID or "").strip() == "0107544000108"
    assert float(r.VATRAT) == 7.0
    t.close()


def test_supplier_next_after_seed(datat):
    _seed(datat, "APMAS", "SUPCOD", ["ป001", "ป002", "ก001"])
    code = M.ensure_supplier(datat, _sup_entry(), created_date=_TODAY)
    assert code == "ป003"


def test_idempotent_by_taxid(datat):
    _seed(datat, "APMAS", "SUPCOD", [])
    e1 = _sup_entry()
    c1 = M.ensure_supplier(datat, e1, created_date=_TODAY)
    # 同税号、不同名 → 复用同码,不新建
    e2 = _sup_entry(supplier={"name": "ชื่ออื่น", "tax_id": "0107544000108"})
    c2 = M.ensure_supplier(datat, e2, created_date=_TODAY)
    assert c1 == c2
    t = dbf.Table(str(datat / "APMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    assert sum(1 for x in t if (x.SUPCOD or "").strip()) == 1
    t.close()


def test_idempotent_by_name_when_no_taxid(datat):
    e1 = _sup_entry(supplier={"name": "ร้านไม่มีภาษี", "tax_id": "", "prename": ""})
    c1 = M.ensure_supplier(datat, e1, created_date=_TODAY)
    e2 = _sup_entry(supplier={"name": "ร้านไม่มีภาษี", "tax_id": "", "prename": ""})
    c2 = M.ensure_supplier(datat, e2, created_date=_TODAY)
    assert c1 == c2


def test_find_returns_none_when_absent(datat):
    assert M.find_supplier_code(datat, "9999999999999", "ไม่มีใคร") is None


def test_norm_space_collapses_nbsp_and_runs():
    assert M._norm_space("เซ็นเตอร์ปิโตรเลียม\xa0จำกัด") == "เซ็นเตอร์ปิโตรเลียม จำกัด"
    assert M._norm_space("  a   b\xa0 c ") == "a b c"
    assert M._norm_space("") == ""


def test_match_existing_name_across_nbsp(datat):
    """账里存的名字带不换行空格、票面是普通空格 → 必须认成同一个,不能判疑似重复转人工。

    真账套(70EXP\\TEST)550 个客户里约 1/3 的名字带 \\xa0 —— 不归一空白就等于客户复用全废。
    """
    t = dbf.Table(str(datat / "ARMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    t.append(
        {
            "CUSCOD": "ซ001",
            "CUSTYP": "00",
            "PRENAM": "บริษัท",
            "CUSNAM": "เซ็นเตอร์ปิโตรเลียม\xa0จำกัด",  # 账里:不换行空格
            "STATUS": "A",
        }
    )
    t.close()
    # 票面:普通空格 + 带法人前缀 → 剥前缀后应命中同一条(修前:比不中 → 走疑似重复 → 整票转人工)
    assert M.find_customer_code(datat, "", "บริษัท เซ็นเตอร์ปิโตรเลียม จำกัด") == "ซ001"
    # 精确命中即复用:写入路径是「先 find_customer_code,没命中才查疑似重复」,
    # 故命中后根本走不到 ERR_CUSTOMER_DUP 那条 elif(见 dbf_sales.write_sale)。
    assert M.find_customer_code(datat, "", "เซ็นเตอร์ปิโตรเลียม\xa0จำกัด") == "ซ001"


def test_explicit_code_short_circuits(datat):
    entry = _sup_entry(supplier={"code": "X999"})
    assert M.ensure_supplier(datat, entry, created_date=_TODAY) == "X999"
    # 不应建档
    t = dbf.Table(str(datat / "APMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    assert sum(1 for x in t if (x.SUPCOD or "").strip()) == 0
    t.close()


# ── S3 做对:FLGVAT 按单别 / 疑似重复 / 读回校验 ────────────────
def _flgvat_of(datat, code):
    t = dbf.Table(str(datat / "APMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        r = next(x for x in t if (x.SUPCOD or "").strip() == code)
        return (r.FLGVAT or "").strip()
    finally:
        t.close()


def test_flgvat_credit_is_2(datat):
    # 赊购 RR + 有税 → 价不含税 '2'(真账主流·标准税票)
    code = M.ensure_supplier(datat, _sup_entry(doctype="RR"), created_date=_TODAY)
    assert _flgvat_of(datat, code) == "2"


def test_flgvat_cash_is_1(datat):
    code = M.ensure_supplier(
        datat,
        _sup_entry(doctype="HP", supplier={"tax_id": "0107544000109"}),
        created_date=_TODAY,
    )
    assert _flgvat_of(datat, code) == "1"


def test_flgvat_no_vat_is_0(datat):
    e = _sup_entry(
        vat_rate=0.0,
        vat_amount="0.00",
        total_amount="1000.00",
        supplier={"tax_id": "0107544000110"},
    )
    code = M.ensure_supplier(datat, e, created_date=_TODAY)
    assert _flgvat_of(datat, code) == "0"


def _seed_full(datat, table, codefield, namefield, code, name, tax, **extra):
    t = dbf.Table(str(datat / f"{table}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    t.append({codefield: code, namefield: name, "TAXID": tax, "STATUS": "A", **extra})
    t.close()


def test_suspected_duplicate_flagged(datat):
    _seed_full(
        datat, "APMAS", "SUPCOD", "SUPNAM", "A001", "ABC Trading", "1111111111111"
    )
    # 近似名(打字差一字母)+ 不同税号 → 疑似重复
    hit = M.find_suspected_supplier_dup(datat, "2222222222222", "ABC Tradng")
    assert hit is not None and hit[0] == "A001"


def test_suspected_duplicate_skips_same_taxid(datat):
    _seed_full(
        datat, "APMAS", "SUPCOD", "SUPNAM", "A001", "ABC Trading", "1111111111111"
    )
    # 同税号 = 真匹配,非疑似
    assert M.find_suspected_supplier_dup(datat, "1111111111111", "ABC Tradng") is None


def test_suspected_duplicate_none_for_distinct(datat):
    _seed_full(
        datat, "APMAS", "SUPCOD", "SUPNAM", "A001", "ABC Trading", "1111111111111"
    )
    assert (
        M.find_suspected_supplier_dup(datat, "2222222222222", "XYZ Logistics") is None
    )


def _cus_entry(**over):
    p = {
        "doctype": "IV",
        "account_set": "DATAT",
        "docdate_be": "581220",
        "vat_period_be": "581201",
        "ref_no": "IV-1",
        "vat_rate": 7.0,
        "base_amount": "1000.00",
        "vat_amount": "70.00",
        "total_amount": "1070.00",
        "customer": {
            "code": "",
            "name": "ABC Tradng",
            "tax_id": "2222222222222",
            "prename": "",
            "customer_new": True,
        },
        "lines": [
            {"acc": "11-02-01-00", "side": "D", "amount": "1070.00", "desc": "ar"},
            {"acc": "41-01-00-00", "side": "C", "amount": "1000.00", "desc": "rev"},
            {"acc": "11-05-04-02", "side": "C", "amount": "70.00", "desc": "vat"},
        ],
    }
    p["customer"].update(over.pop("customer", {}))
    p.update(over)
    return build_sales_entry(p)


def test_suspected_customer_dup_entry(datat):
    # 销项侧:核心名相似 + 不同税号 → 疑似重复客户,建前转人工(镜像供应商)。
    _seed_full(
        datat, "ARMAS", "CUSCOD", "CUSNAM", "C001", "ABC Trading", "1111111111111"
    )
    assert M.suspected_customer_dup(datat, _cus_entry()) is True
    # 已有客户码 → 跳过判定;不同名 → 非疑似。
    assert (
        M.suspected_customer_dup(datat, _cus_entry(customer={"code": "C001"})) is False
    )
    assert (
        M.suspected_customer_dup(datat, _cus_entry(customer={"name": "XYZ Logistics"}))
        is False
    )


# ── 地址落档(B2B 全额税票一致性) ────────────────────────────
def test_split_address():
    assert M._split_address("") == ("", "", "")
    assert M._split_address("   ") == ("", "", "")
    assert M._split_address("123 Main St") == ("123 Main St", "", "")
    # 跨段但未超总容量(130):按词断行不切词,各段不超列宽 50/50/30,词全保留
    spanning = " ".join(f"word{i}" for i in range(15))
    p = M._split_address(spanning)
    assert len(p[0]) <= 50 and len(p[1]) <= 50 and len(p[2]) <= 30
    assert " ".join(x for x in p if x).split() == spanning.split()


def _addr_of(datat, table, codefield, code):
    t = dbf.Table(str(datat / f"{table}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        r = next(x for x in t if (getattr(x, codefield) or "").strip() == code)
        parts = [
            (r.ADDR01 or "").strip(),
            (r.ADDR02 or "").strip(),
            (r.ADDR03 or "").strip(),
        ]
        return " ".join(p for p in parts if p), (r.TAXID or "").strip()
    finally:
        t.close()


def test_create_supplier_writes_address(datat):
    addr = "212 หมู่ 1 ต.แคราย อ.กระทุ่มแบน จ.สมุทรสาคร 74110"
    code = M.ensure_supplier(
        datat, _sup_entry(supplier={"address": addr}), created_date=_TODAY
    )
    got, _ = _addr_of(datat, "APMAS", "SUPCOD", code)
    assert got == " ".join(addr.split())  # 分行重组 == 归一原文(无词被切断)


def test_create_customer_writes_address_and_taxid(datat):
    addr = "104 อาคารปานะวงศ์ ชั้น 6 ถ.สุรวงศ์ เขตบางรัก กรุงเทพฯ 10500"
    e = _cus_entry(
        customer={"name": "ยามาโกย่า", "tax_id": "0105549062312", "address": addr}
    )
    code = M.ensure_customer(datat, e, created_date=_TODAY)
    got, tax = _addr_of(datat, "ARMAS", "CUSCOD", code)
    assert tax == "0105549062312"  # B2B 税号落档(读回校验已在 ensure_customer 内)
    assert got == " ".join(addr.split())


def test_suspected_dup_normalized_identical_diff_tax(datat):
    # 归一同名(仅差泰文声调符 ็)+ 不同税号:OCR 常见变体,最该转人工,绝不静默建重复。
    _seed_full(
        datat,
        "ARMAS",
        "CUSCOD",
        "CUSNAM",
        "C001",
        "เอ็มเพ็กซ์ เทรดดิ้ง จำกัด",
        "0105561033221",
    )
    near = _cus_entry(
        customer={"name": "เอ็มเพกซ์ เทรดดิ้ง จำกัด", "tax_id": "0105562044332"}
    )
    assert M.suspected_customer_dup(datat, near) is True
    # 同名同税号 = 真匹配(_find_code 复用),不算疑似。
    same = _cus_entry(
        customer={"name": "เอ็มเพกซ์ เทรดดิ้ง จำกัด", "tax_id": "0105561033221"}
    )
    assert M.suspected_customer_dup(datat, same) is False


def test_verify_supplier_row(datat):
    code = M.ensure_supplier(datat, _sup_entry(), created_date=_TODAY)
    assert M.verify_supplier_row(datat, code, tax_id="0107544000108") is None
    assert M.verify_supplier_row(datat, "NOPE", tax_id="") is not None
    assert M.verify_supplier_row(datat, code, tax_id="9999999999999") is not None


# ── 连锁同税号多分店档:级联匹配(2026-08-06 Big C 真账现场镜像) ────────────
_BIGC_TAX = "0107536000633"


def _seed_bigc(d):
    """三档共用税号:总店(ORGNUM 0)行序在前 + 两分店(18/54)。旧版取第一条必错配总店。"""
    rows = (
        ("BIGC", "บิ๊กซี ซุปเปอร์เซ็นเตอร์", 0),
        ("BIGC-001", "บิ๊กซี ซูเปอร์เซ็นเตอร์ (สาขา-พลับพลาไชย)", 18),
        ("BIGC-002", "บิ๊กซี ซูเปอร์เซ็นเตอร์ (สาขา-ซอยประชุม)", 54),
    )
    for code, name, org in rows:
        _seed_full(d, "ARMAS", "CUSCOD", "CUSNAM", code, name, _BIGC_TAX, ORGNUM=org)


def test_branch_no_normalization():
    assert M._branch_no("18") == "00018"
    assert M._branch_no("00018") == "00018"
    assert M._branch_no("สาขาที่ 18") == "00018"
    assert M._branch_no("") == ""
    assert M._branch_no(None) == ""
    assert M._branch_no("123456") == ""  # 超长=无效,宁缺勿硬配


def test_multi_branch_resolved_by_branch_no(datat):
    _seed_bigc(datat)
    got = M.find_customer_code(datat, _BIGC_TAX, "ชื่อไม่ตรงเลย", branch="18")
    assert got == "BIGC-001"


def test_multi_branch_resolved_by_exact_name(datat):
    # Korn 场景:Excel 只有分店名没有分店号,名字精确应定位分店而不是总店。
    _seed_bigc(datat)
    got = M.find_customer_code(
        datat, _BIGC_TAX, "บิ๊กซี ซูเปอร์เซ็นเตอร์ (สาขา-พลับพลาไชย)"
    )
    assert got == "BIGC-001"


def test_multi_branch_ambiguous_raises(datat):
    # 分店号没给、名字也配不上 → 转人工,绝不静默取第一条(总店)。
    _seed_bigc(datat)
    with pytest.raises(M.AmbiguousPartyMatch):
        M.find_customer_code(datat, _BIGC_TAX, "ชื่อที่ไม่ตรงสักอัน")


def test_multi_branch_branch_miss_falls_to_name(datat):
    # 给了分店号但 ORGNUM 没配上(会计没填)→ 落名字级,仍能定位。
    _seed_bigc(datat)
    got = M.find_customer_code(
        datat, _BIGC_TAX, "บิ๊กซี ซูเปอร์เซ็นเตอร์ (สาขา-ซอยประชุม)", branch="99"
    )
    assert got == "BIGC-002"


def test_single_tax_hit_keeps_old_behavior(datat):
    # 散户:税号唯一命中,名字对不上也复用(与旧版一致,不受级联影响)。
    _seed_full(
        datat, "ARMAS", "CUSCOD", "CUSNAM", "C009", "ร้านเดี่ยว", "3333333333333"
    )
    assert M.find_customer_code(datat, "3333333333333", "ชื่ออื่น") == "C009"


def test_ensure_customer_writes_orgnum(datat):
    e = _cus_entry(
        customer={"name": "สาขาใหม่ เทรดดิ้ง", "tax_id": "4444444444444", "branch": "18"}
    )
    code = M.ensure_customer(datat, e, created_date=_TODAY)
    t = dbf.Table(str(datat / "ARMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        row = next(
            r for r in S.live_rows(t) if (r.CUSCOD or "").strip() == code
        )
        assert int(row.ORGNUM) == 18
    finally:
        t.close()


def test_ensure_customer_ambiguous_propagates(datat):
    # ensure 阶段(备份内)撞歧义 → 异常上抛,由 writer 回滚;绝不建出第 4 个同税号档。
    _seed_bigc(datat)
    e = _cus_entry(
        customer={"name": "ชื่อที่ไม่ตรงสักอัน", "tax_id": _BIGC_TAX}
    )
    with pytest.raises(M.AmbiguousPartyMatch):
        M.ensure_customer(datat, e, created_date=_TODAY)


def test_single_hit_branch_mismatch_creates_new(datat):
    # 只有总店档(ORGNUM 0),载荷明确分店 18 且名字不符 → 当未命中(走建档),
    # 不复用总店;名字相等则仍复用(档只是没填分店号)。
    _seed_full(
        datat, "ARMAS", "CUSCOD", "CUSNAM", "B001", "บิ๊กซี สนญ", _BIGC_TAX
    )
    assert (
        M.find_customer_code(datat, _BIGC_TAX, "บิ๊กซี สาขาใหม่", branch="18") is None
    )
    assert (
        M.find_customer_code(datat, _BIGC_TAX, "บิ๊กซี สนญ", branch="18") == "B001"
    )
    assert M.find_customer_code(datat, _BIGC_TAX, "ชื่ออื่น") == "B001"
