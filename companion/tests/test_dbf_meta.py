# -*- coding: utf-8 -*-
"""列宽自省 + 定长列裁切。宽度必须来自表头,不来自常量。"""

from __future__ import annotations

import sys
from pathlib import Path

import dbf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion import dbf_meta  # noqa: E402
from companion import dbf_schema as S  # noqa: E402


@pytest.fixture
def acct(tmp_path):
    t = dbf.Table(
        str(tmp_path / "STMAS.DBF"),
        "STKCOD C(20); STKDES C(37)",
        codepage=S.CODEPAGE,
        dbf_type=S.DBF_TYPE,
    )
    t.open(dbf.READ_WRITE)
    t.close()
    return tmp_path


def test_field_width_reads_the_declared_width(acct):
    assert dbf_meta.field_width(acct, "STMAS", "STKDES") == 37
    assert dbf_meta.field_width(acct, "STMAS", "STKCOD") == 20


def test_field_width_raises_on_unknown_field(acct):
    with pytest.raises(Exception):
        dbf_meta.field_width(acct, "STMAS", "NOSUCH")


def test_fit_strips_then_truncates():
    assert dbf_meta.fit("  กกก  ", 4) == "กกก"
    assert dbf_meta.fit("abcdefgh", 3) == "abc"
    assert dbf_meta.fit(None, 5) == ""


def test_fit_counts_bytes_not_characters():
    """非 cp874 字符编码后仍占位,按字符数裁会溢出列宽。"""
    assert len(dbf_meta.fit("日本語テキスト", 3).encode(S.CODEPAGE, "replace")) <= 3
