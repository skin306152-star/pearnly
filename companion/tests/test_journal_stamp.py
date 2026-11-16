# -*- coding: utf-8 -*-
"""凭证稽核栏照原生补（``dbf_schema.journal_stamp``，11-16 原生取样 NLTTEST / 69EXP\\TEST）。"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion import dbf_schema as S  # noqa: E402

NOW = dt.datetime(2026, 11, 16, 9, 5)
HEAD = SimpleNamespace(field_names=["JNLTYP", "VOUCHER", "CREBY", "CREDAT", "USERID", "CHGDAT"])
LINE = SimpleNamespace(field_names=["VOUCHER", "SEQIT", "CHGDAT", "CHGTIM"])


def test_a_head_gets_the_day_and_the_entering_user_and_keeps_the_machine_mark():
    row = S.journal_stamp(HEAD, {"VOUCHER": "RR1", "CREBY": S.PEARNLY_USERID}, userid="KIKKIKKIKK", now=NOW)
    assert row == {"VOUCHER": "RR1", "CREBY": S.PEARNLY_USERID, "CREDAT": NOW.date(), "CHGDAT": NOW.date(), "USERID": "KIKKIKKI"}
    assert S.journal_stamp(HEAD, {}, userid="", now=NOW)["USERID"] == S.PEARNLY_USERID  # 定不了录入人：同单头记 PEARNLY


def test_a_line_gets_the_day_and_a_four_digit_time():
    assert S.journal_stamp(LINE, {"VOUCHER": "RR1"}, now=NOW) == {"VOUCHER": "RR1", "CHGDAT": NOW.date(), "CHGTIM": "0905"}


def test_an_old_table_without_the_columns_or_a_value_already_given_is_left_alone():
    old = SimpleNamespace(field_names=["VOUCHER", "SEQIT"])
    assert S.journal_stamp(old, {"VOUCHER": "RR1"}, now=NOW) == {"VOUCHER": "RR1"}
    given = {"VOUCHER": "RR1", "chgdat": dt.date(2020, 1, 1)}
    assert S.journal_stamp(LINE, given, now=NOW) == {**given, "CHGTIM": "0905"}
    assert "CHGDAT" not in given  # 不改传进来的
