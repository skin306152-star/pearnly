# -*- coding: utf-8 -*-
"""自动建库存档填什么:单位(QUCOD)、类别(STKGRP)、建档日(CREDAT)· 合成账套。

真机 69EXP/TEST 推库存票成功、账务全对,但建出来的 STMAS 只有编码/品名/科目/余额/审计几列,
单位和类别全空,建档日还写成了发票日。这三件各自的判据:

- 单位:票面给了按票面的,没给退账套众数,**并且要标出来是猜的**(line_modes.unit_guessed);
- 类别:账套众数直接填(70EXP 05 占 144/162),不必惊动会计;
- 建档日:主档的 CREDAT/CHGDAT 记真实建档日,不是单据日期。

`line_modes` 那两个键是与主站的契约(推送日志据此标「单位是系统填的」),故本文件连着钉。
"""

from __future__ import annotations

import datetime as dt
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
from companion import stock_purchase as SP  # noqa: E402
from companion import stock_sale as SS  # noqa: E402

_DOCDAT = dt.date(2026, 5, 31)  # 发票日:真机上主档 CREDAT 被写成的就是它
_NEW = "PEARNLY UNIT PROBE"
_INV_ACC = "11-04-02-00"  # 存货科目(GLACC 里 GROUP=1 且名字带 สินค้า → 过守门)


def _stmas_append(d, **fields):
    t = dbf.Table(str(d / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        t.append(fields)
    finally:
        t.close()


def _stock_item(stkcod, qucod, stkgrp, **extra):
    """一条真库存品(建档模板 + 众数选票的来源)。"""
    row = {
        "STKCOD": stkcod,
        "STKDES": f"สินค้า {stkcod}",
        "STKTYP": "0",
        "ACCCOD": "ST01",
        "QUCOD": qucod,
        "CQUCOD": qucod,
        "PQUCOD": qucod,
        "SQUCOD": qucod,
        "CFACTOR": 1.0,
        "PFACTOR": 1.0,
        "SFACTOR": 1.0,
        "STKGRP": stkgrp,
    }
    row.update(extra)
    return row


@pytest.fixture
def acct(tmp_path):
    """账套里既有库存品:单位 ลต×3 / ถง×1,类别 05×3 / OIL×1 → 众数 ลต + 05。"""
    d = _synth_account.build(tmp_path / "70EXP" / "test")
    for i, (qucod, grp) in enumerate(
        [("ลต", "05"), ("ลต", "05"), ("ลต", "OIL"), ("ถง", "05")], start=1
    ):
        _stmas_append(d, **_stock_item(f"OIL{i:02d}", qucod, grp))
    t = dbf.Table(str(d / "ISACC.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        t.append(
            {
                "ACCTYP": "S",
                "ACCCOD": "ST01",
                "METHOD": "A",
                "ACCNUM01": _INV_ACC,
                "ACCNUM02": "51-01-00-00",
            }
        )
    finally:
        t.close()
    return d


@pytest.fixture
def bare_acct(tmp_path):
    """一件库存品都没有的账套(69EXP/TEST 那种零起点):只有服务品档 + 存货科目组。"""
    d = _synth_account.build(tmp_path / "69EXP" / "test")
    _stmas_append(
        d,
        STKCOD="SV01",
        STKDES="ค่าบริการ",
        STKTYP="3",
        QUCOD="คง",
        STKGRP="คช",
        ACCCOD="ST01",
    )
    t = dbf.Table(str(d / "ISACC.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        t.append({"ACCTYP": "S", "ACCCOD": "ST01", "METHOD": "A", "ACCNUM01": _INV_ACC})
    finally:
        t.close()
    return d


def _row(d, stkcod):
    t = dbf.Table(str(d / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        for r in t:
            if not dbf.is_deleted(r) and (r["STKCOD"] or "").strip() == stkcod:
                return {
                    f: (r[f].strip() if isinstance(r[f], str) else r[f])
                    for f in t.field_names
                }
        return None
    finally:
        t.close()


# ── 单位 ──────────────────────────────────────────────────────────────────


def test_invoice_unit_wins_and_is_not_flagged_as_a_guess(acct):
    made = PM.ensure_stock_item(acct, _NEW, unit="ถัง")
    assert _row(acct, made.stkcod)["QUCOD"] == "ถง"  # 码 ≠ 名字前两字
    assert made.unit_guessed is False
    assert made.unit_code == ""


def test_missing_invoice_unit_falls_back_to_the_account_mode(acct):
    """票面没给单位 → 拿这套账最常用的单位,而不是留空、也不是克隆模板那一行的单位。"""
    made = PM.ensure_stock_item(acct, _NEW, unit="")
    assert _row(acct, made.stkcod)["QUCOD"] == "ลต"
    assert (made.unit_guessed, made.unit_code) == (True, "ลต")


def test_unit_the_account_does_not_know_also_falls_back(acct):
    # 票面写了个这套账 ISTAB 里没有的说法 → 不硬塞,同样退众数并标成猜的。
    made = PM.ensure_stock_item(acct, _NEW, unit="กระสอบ")
    assert (made.unit_guessed, made.unit_code) == (True, "ลต")


def test_all_four_unit_columns_move_together(acct):
    """真账套 162/162 个库存品 QUCOD==CQUCOD==PQUCOD==SQUCOD;只改一列就造出真账里没有的形状。"""
    made = PM.ensure_stock_item(acct, _NEW, unit="ถัง")
    row = _row(acct, made.stkcod)
    assert row["QUCOD"] == row["CQUCOD"] == row["PQUCOD"] == row["SQUCOD"] == "ถง"
    assert (
        row["CFACTOR"] == row["PFACTOR"] == row["SFACTOR"] == 1.0
    )  # 换算系数仍克隆模板


def test_bare_account_leaves_the_unit_empty_instead_of_inventing_one(bare_acct):
    """账里一件库存品都没有 → 没有众数可取:留空,绝不抄服务品档的单位,也不谎报猜过。"""
    made = PM.ensure_stock_item(bare_acct, _NEW, unit="", acccod="ST01")
    row = _row(bare_acct, made.stkcod)
    assert row["QUCOD"] == "" and row["CQUCOD"] == ""
    assert (made.unit_guessed, made.unit_code) == (False, "")


def test_reused_item_reports_no_guess(acct):
    # 复用既有档没建任何东西 → 不许标「猜了单位」,否则推送日志上全是狼来了。
    first = PM.ensure_stock_item(acct, _NEW, unit="")
    again = PM.ensure_stock_item(acct, _NEW, unit="")
    assert again.stkcod == first.stkcod
    assert (again.created, again.unit_guessed) == (False, False)


# ── 负库存开关 ────────────────────────────────────────────────────────────


def test_new_item_allows_negative_stock(acct):
    """我们建的库存品按设计就是要被卖成负的(没档就建 / 没货卖负 / 没成本不结转)。

    档上写着"不许负"就与我们对它做的事自相矛盾:会计事后在 Express 界面动这个品时,
    GUI 会拦住 —— 拦的正是我们自己写进去的负库存。
    """
    made = PM.ensure_stock_item(acct, _NEW, unit="ถัง")
    assert _row(acct, made.stkcod)["NEGALLOW"] == "Y"


def test_bare_account_new_item_also_allows_negative(bare_acct):
    """零库存主档的账套走的是"抄服务品档"那条路,同样要允许负 —— 别只在一条路上生效。"""
    made = PM.ensure_stock_item(bare_acct, _NEW, unit="", acccod="ST01")
    assert _row(bare_acct, made.stkcod)["NEGALLOW"] == "Y"


def test_existing_item_policy_is_left_alone(acct):
    """客户既有商品的负库存策略是人家的账,复用时一个字节都不许改。"""
    first = PM.ensure_stock_item(acct, _NEW, unit="ถัง")
    before = _row(acct, first.stkcod)["NEGALLOW"]
    t = dbf.Table(str(acct / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        for rec in t:
            if (
                not dbf.is_deleted(rec)
                and (rec["STKCOD"] or "").strip() == first.stkcod
            ):
                with rec as r:
                    r["NEGALLOW"] = "N"  # 会计后来自己改成「不许负」
                break
    finally:
        t.close()
    again = PM.ensure_stock_item(acct, _NEW, unit="ถัง")
    assert again.stkcod == first.stkcod and again.created is False
    assert _row(acct, first.stkcod)["NEGALLOW"] == "N", "复用既有档时改了人家的策略"
    assert before == "Y"


# ── 类别 ──────────────────────────────────────────────────────────────────


def test_group_takes_the_account_mode_without_flagging(acct):
    """类别有明显众数(真账 05 占 144/162),自动填即可 —— 不必像单位那样惊动会计。"""
    made = PM.ensure_stock_item(acct, _NEW, unit="ถัง")
    assert _row(acct, made.stkcod)["STKGRP"] == "05"


def test_bare_account_leaves_the_group_empty(bare_acct):
    made = PM.ensure_stock_item(bare_acct, _NEW, unit="", acccod="ST01")
    assert _row(bare_acct, made.stkcod)["STKGRP"] == ""  # 不抄服务品的 คช


# ── 建档日 ────────────────────────────────────────────────────────────────


def test_master_records_the_real_creation_date_not_the_invoice_date(acct):
    """主档 CREDAT/CHGDAT 是审计列:这条档今天建的就写今天,写成发票日等于伪造建档时间。"""
    made = PM.ensure_stock_item(acct, _NEW, unit="ถัง")
    row = _row(acct, made.stkcod)
    today = dt.date.today()
    assert row["CREDAT"] == today and row["CHGDAT"] == today
    assert row["CREDAT"] != _DOCDAT


def test_opening_stock_keeps_the_business_date_on_the_stock_row(acct):
    """期初行的日期是会计填的业务日期,不许被"建档日改今天"顺手改掉 —— 两个日期各管各的。"""
    opened = dt.date(2026, 1, 1)
    SS.set_opening_stock(
        acct, [{"name": _NEW, "qty": "10", "unit_cost": "5", "date": opened}], _DOCDAT
    )
    t = dbf.Table(str(acct / "STCRD.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        rows = [r for r in t if (r["DOCNUM"] or "").strip().startswith("OB260101")]
    finally:
        t.close()
    assert rows and rows[0]["DOCDAT"] == opened  # 期初行:业务日期
    master = _row(acct, PM.find_stock_item_code(acct, _NEW))
    assert master["CREDAT"] == dt.date.today()  # 主档:真实建档日


# ── line_modes 契约(主站按这两个键在推送日志上标注)───────────────────────


def test_purchase_line_modes_carry_the_guess(acct):
    modes, _ = SP.write_stock_in_lines(
        acct,
        "PV-001",
        _DOCDAT,
        [{"name": _NEW, "qty": "3", "amount": "300", "unit": ""}],
        "SUP",
    )
    assert modes[0]["created"] is True
    assert modes[0]["unit_guessed"] is True
    assert modes[0]["unit_code"] == "ลต"


def test_sale_line_modes_carry_the_guess(acct):
    modes, _ = SS.write_stock_lines(
        acct,
        "IV-001",
        _DOCDAT,
        [{"name": _NEW, "qty": "2", "unit_price": "50", "amount": "100", "unit": ""}],
        "CUS",
    )
    assert modes[0]["reason"] == SS.REASON_NEW_ITEM
    assert modes[0]["unit_guessed"] is True
    assert modes[0]["unit_code"] == "ลต"


def test_every_line_mode_declares_the_two_keys(acct):
    """键要恒在(值可以是 False/''):主站少一个键就少一条标注,而且不报错 —— 最难查的那种。"""
    modes, _ = SP.write_stock_in_lines(
        acct,
        "PV-002",
        _DOCDAT,
        [{"name": "สินค้า OIL01", "qty": "1", "amount": "10", "unit": "ลิตร"}],
        "SUP",
    )
    assert modes[0]["created"] is False  # 复用既有档
    assert modes[0]["unit_guessed"] is False
    assert modes[0]["unit_code"] == ""
