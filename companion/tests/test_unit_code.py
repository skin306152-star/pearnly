# -*- coding: utf-8 -*-
"""单位串 → 本账套合法单位码(ISTAB TABTYP=20)。

回归点:主档 QUCOD 曾直接写 `unit[:2]`,`ถัง` 落成 `ถั` —— 账套 39 个合法码里没有这个值。
现在一律走账套自己的码表,解析不出宁可返空(调用方保留模板克隆的单位),不硬塞前两字。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _synth_account  # noqa: E402

from companion import dbf_schema as S  # noqa: E402
from companion import unit_code  # noqa: E402


@pytest.fixture
def acct(tmp_path):
    return _synth_account.build(tmp_path / "70EXP" / "test")


@pytest.mark.parametrize(
    "unit,code",
    [
        ("ถัง", "ถง"),  # 前两字是 'ถั' —— 非法码,正解在 ISTAB
        ("ลิตร", "ลต"),
        ("ชิ้น", "ชน"),  # 内置映射给 'ชิ',账套码表说是 'ชน' → 账套赢
        ("กระป๋อง", "กง"),  # 内置映射给 'กป'(该账套里是"กระปุก"),账套赢
        ("ถง", "ถง"),  # 已经是码
        (" ลิตร ", "ลต"),
    ],
)
def test_resolves_against_account_unit_table(acct, unit, code):
    assert unit_code.resolve(acct, unit) == code


def test_prefix_of_unit_name_is_not_a_legal_code(acct):
    """把老写法的结果拿来对照:`ถัง`[:2] 不在账套合法码里。"""
    assert "ถัง"[:2] not in unit_code.legal_codes(acct)
    assert unit_code.resolve(acct, "ถัง") in unit_code.legal_codes(acct)


def test_falls_back_to_builtin_map_when_account_table_has_no_wording(acct):
    """英文单位账套码表里没有说法,内置映射的结果仍在合法码里 → 采用。"""
    assert S.unit_to_tqucod("bottle") == "ขด"
    assert unit_code.resolve(acct, "bottle") == "ขด"


def test_rejects_builtin_code_absent_from_this_account(acct):
    """内置映射给的码不在本账套码表里 → 返空,让调用方保留模板单位,不写无效码。"""
    assert S.unit_to_tqucod("pcs") == "ชิ"
    assert "ชิ" not in unit_code.legal_codes(acct)
    assert unit_code.resolve(acct, "pcs") == ""


def test_unknown_unit_returns_empty(acct):
    assert unit_code.resolve(acct, "กระสอบ") == ""
    assert unit_code.resolve(acct, "") == ""


def test_missing_unit_table_returns_empty(tmp_path):
    """账套连 ISTAB 都没有 → 不猜,返空(保留模板值)。"""
    acct = _synth_account.build(tmp_path / "70EXP" / "test")
    (acct / "ISTAB.DBF").unlink()
    assert unit_code.resolve(acct, "ถัง") == ""


def test_rejects_code_wider_than_the_target_column(tmp_path):
    """ISTAB.TYPCOD 是 C(4) 而 STMAS.QUCOD 是 C(2):装不下的码返空,绝不截半个码。"""
    acct = _synth_account.build(tmp_path / "70EXP" / "test")
    import dbf

    t = dbf.Table(str(acct / "ISTAB.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    t.append({"TABTYP": "20", "TYPCOD": "ABCD", "SHORTNAM": "wide", "TYPDES": "wide"})
    t.close()
    assert "ABCD" in unit_code.legal_codes(acct)
    assert unit_code.resolve(acct, "wide") == ""
