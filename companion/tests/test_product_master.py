"""V2 非库存商品主档 find-or-create 测试 · 合成 VFP 表 · 不依赖真账。

覆盖:无匹配→克隆建档 / 归一匹配复用(不重复建)/ ISACC 按目标科目 find-or-create /
克隆取模板字段(禁硬编码)/ 防呆绝不建库存 STKTYP=0 / find_item_code 只认非库存。
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

import dbf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _synth_account  # noqa: E402

from companion import dbf_schema as S  # noqa: E402
from companion import product_master as PM  # noqa: E402
from companion import stock_lookup  # noqa: E402

_TODAY = _dt.date(2026, 6, 25)


@pytest.fixture
def datat(tmp_path):
    return _synth_account.build(tmp_path / "70EXP" / "test")


def _des_width(d):
    """STKDES 的真实列宽 —— 断言不写死数字,换个账套/换个 Express 版本仍然成立。"""
    return len(stock_lookup.storage_des(d, "x" * 200))


def _add_stmas(d, **fields):
    t = dbf.Table(str(d / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        t.append(fields)
    finally:
        t.close()


def _stmas_row(d, stkcod):
    t = dbf.Table(str(d / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        for r in t:
            if (r.STKCOD or "").strip() == stkcod:
                return {f: (r[f].strip() if isinstance(r[f], str) else r[f]) for f in t.field_names}
        return None
    finally:
        t.close()


def test_create_when_no_match(datat):
    code = PM.ensure_item(
        datat, "ค่าบริการล้างรถ", "41-01-00-00", created_date=_TODAY, unit="ครั้ง"
    )
    assert code == "PN00001"
    row = _stmas_row(datat, code)
    assert row["STKTYP"] in ("3", "4", "5")  # 非库存
    assert row["STKDES"] == "ค่าบริการล้างรถ"
    assert row["STATUS"] == ""  # 商品须空白才进 Express 商品库('A' 会被列表过滤掉)
    assert row["ACCCOD"]  # 已联动 ISACC
    assert row["QUCOD"] == "คง"  # 单位名 → ISTAB 单位码,不是名字前两字('คร')


def test_acccod_links_target_account(datat):
    code = PM.ensure_item(datat, "ค่าขนส่ง", "41-02-00-00", created_date=_TODAY)
    acccod = _stmas_row(datat, code)["ACCCOD"]
    # ISACC 该 ACCCOD 的 ACCNUM01 == 目标账码
    t = dbf.Table(str(datat / "ISACC.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        hit = [r for r in t if (r.ACCCOD or "").strip() == acccod]
        assert hit
        assert (hit[0].ACCNUM01 or "").strip() == "41-02-00-00"  # 成本科目
        assert (hit[0].ACCNUM03 or "").strip() == "41-02-00-00"  # 收入科目(บัญชีรายได้·否则主档列空)
    finally:
        t.close()


def test_reuse_existing_acccod(datat):
    # 预置 ISACC 映射 → ensure_item 应复用,不新建
    t = dbf.Table(str(datat / "ISACC.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    t.append({"ACCTYP": "S", "ACCCOD": "0099", "ACCNUM01": "41-01-00-00", "METHOD": "-"})
    t.close()
    code = PM.ensure_item(datat, "ค่าบริการ", "41-01-00-00", created_date=_TODAY)
    assert _stmas_row(datat, code)["ACCCOD"] == "0099"


def test_idempotent_match_normalized(datat):
    c1 = PM.ensure_item(datat, "ค่าบริการล้างรถ", "41-01-00-00", created_date=_TODAY)
    # 大小写/空格变体 → 命中同一主档,不重复建
    c2 = PM.ensure_item(datat, "  ค่าบริการล้างรถ ", "41-01-00-00", created_date=_TODAY)
    assert c1 == c2
    t = dbf.Table(str(datat / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        n = sum(1 for r in t if not dbf.is_deleted(r) and (r.STKCOD or "").strip())
    finally:
        t.close()
    assert n == 1


def test_clone_from_template_not_hardcoded(datat):
    # 模板非库存主档带独特结构字段 → 新建应克隆这些(STKGRP/NEGALLOW…),而非写死
    _add_stmas(
        datat, STKCOD="53-03-04-00", STKDES="ค่าโทรศัพท์", STKTYP="5",
        STKGRP="คช", QUCOD="คง", VATCOD="1", CFACTOR=1.0, NEGALLOW="Y",
    )
    code = PM.ensure_item(datat, "ค่าใหม่", "53-03-99-00", created_date=_TODAY)
    row = _stmas_row(datat, code)
    # STKTYP 不跟模板(模板是 5=隐身费用型)→ 强制 3(服务型·商品列表可见)
    assert row["STKTYP"] == "3"
    assert row["STKGRP"] == "คช"  # 其余结构仍克隆
    assert row["NEGALLOW"] == "Y"


def test_stktyp_forced_visible_3_even_if_template_is_5(datat):
    # 账套唯一可克隆模板是 5 型(隐身费用项)→ 新建商品仍须 3(否则"在库却查不到")
    _add_stmas(datat, STKCOD="53-03-02-00", STKDES="ค่าซ่อมแซม", STKTYP="5", STKGRP="คช")
    code = PM.ensure_item(datat, "ผ้าเบรกหน้า", "41-01-00-00", created_date=_TODAY)
    assert _stmas_row(datat, code)["STKTYP"] == "3"


def test_never_creates_stock_type(datat):
    # 即便账套里只有库存主档(STKTYP=0)可"克隆",也绝不建成库存
    _add_stmas(datat, STKCOD="0001", STKDES="สินค้า", STKTYP="0", STKGRP="A")
    code = PM.ensure_item(datat, "ค่าบริการ", "41-01-00-00", created_date=_TODAY)
    assert _stmas_row(datat, code)["STKTYP"] in ("3", "4", "5")


def test_find_item_code_only_nonstock(datat):
    _add_stmas(datat, STKCOD="0001", STKDES="สินค้าเหมือนกัน", STKTYP="0")
    # 同名但库存型 → find 不认(只匹配非库存)
    assert PM.find_item_code(datat, "สินค้าเหมือนกัน") is None


def _stmas_count(d):
    t = dbf.Table(str(d / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        return sum(1 for r in t if not dbf.is_deleted(r) and (r.STKCOD or "").strip())
    finally:
        t.close()


def test_long_name_over_column_width_dedupes(datat):
    # 超列宽的长名再次 ensure_item → 须复用同一主档,不因 STKDES 截断而重复建(回归 bug)
    long_name = "น้ำมันเครื่องสังเคราะห์แท้ PERFORMA NGV 10W-40 สูตรพิเศษ (10/1L)"
    assert len(long_name) > _des_width(datat)
    c1 = PM.ensure_item(datat, long_name, "41-01-00-00", created_date=_TODAY)
    c2 = PM.ensure_item(datat, long_name, "41-01-00-00", created_date=_TODAY)
    assert c1 == c2
    assert _stmas_count(datat) == 1


def test_long_names_sharing_stored_prefix_collapse(datat):
    # 两个长名仅在列宽之后不同 → STKDES 截断后无法区分 → 归一为同一主档(复用)
    prefix = "ชุดซ่อมระบบหัวฉีดคอมมอนเรลดีเซลพร้อมอะไหล่แท้เบอร์มาตรฐานโรงงาน"[
        : _des_width(datat)
    ]
    c1 = PM.ensure_item(datat, prefix + " รุ่น A-001", "41-01-00-00", created_date=_TODAY)
    c2 = PM.ensure_item(datat, prefix + " รุ่น B-002", "41-01-00-00", created_date=_TODAY)
    assert c1 == c2
    assert _stmas_count(datat) == 1


def test_storage_and_match_key_follow_declared_column_width(datat):
    """列宽是从表头现取的:把 STKDES 建成 C(30) 的账套,落档与比对键都必须跟着变成 30。

    宽度写死成常量时本用例必红 —— 这是 C6 真账套那条「一边截 40、一边比 50 字原值」的机器闸。
    """
    narrow = datat.parent / "narrow"
    narrow.mkdir()
    t = dbf.Table(
        str(narrow / "STMAS.DBF"),
        "STKCOD C(20); STKDES C(30); STKTYP C(1)",
        codepage=S.CODEPAGE,
        dbf_type=S.DBF_TYPE,
    )
    t.open(dbf.READ_WRITE)
    t.close()
    name = "ก" * 45
    assert _des_width(datat) == 50  # 常规合成表照真表建
    assert len(stock_lookup.storage_des(narrow, name)) == 30
    assert stock_lookup.match_key(narrow, name) == "ก" * 30
