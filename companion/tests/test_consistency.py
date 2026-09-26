# -*- coding: utf-8 -*-
"""三重一致性闸的公司名一证(ISINFO.THINAM == 所选账套公司)· 合成 ISINFO · 只读。"""
import dbf

from src.companion import consistency

_SPEC = "THINAM C(40); ENGNAM C(40); TAXID C(15)"


def _isinfo(d, name):
    d.mkdir(parents=True, exist_ok=True)
    t = dbf.Table(str(d / "ISINFO.DBF"), _SPEC, codepage="cp874", dbf_type="vfp")
    t.open(dbf.READ_WRITE)
    t.append({"THINAM": name})
    t.close()


def test_read_company(tmp_path):
    _isinfo(tmp_path, "บริษัท\xa0มานะชัยบริการ\xa0จำกัด")
    assert "มานะชัยบริการ" in consistency.read_company(tmp_path)


def test_matches_when_same(tmp_path):
    _isinfo(tmp_path, "บริษัท\xa0มานะชัยบริการ\xa0จำกัด")
    ok, _ = consistency.account_company_matches(tmp_path, "บริษัท มานะชัยบริการ จำกัด")
    assert ok  # \xa0 / 空格差异归一后相等


def test_mismatch_rejected(tmp_path):
    _isinfo(tmp_path, "บริษัท ก")
    ok, reason = consistency.account_company_matches(tmp_path, "บริษัท ข")
    assert not ok and "公司不符" in reason


def test_expected_empty_skips(tmp_path):
    ok, _ = consistency.account_company_matches(tmp_path, "")
    assert ok  # 无期望公司 → 跳过此证(放行)


def test_missing_isinfo_is_failsafe(tmp_path):
    ok, _ = consistency.account_company_matches(tmp_path, "บริษัท ก")
    assert not ok  # 有期望但读不到 ISINFO → fail-safe 拒
