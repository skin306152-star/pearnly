# -*- coding: utf-8 -*-
"""Express 根定位:判据 = secure/SCCOMP.DBF 含公司;多候选选登记最多的那套;全无返 None。"""
import dbf

from src.companion import express_locate

_SCCOMP = "COMPNAM C(40); COMPCOD C(10); PATH C(40); CANDEL C(1)"


def _registry(root, n):
    sec = root / "secure"
    sec.mkdir(parents=True, exist_ok=True)
    t = dbf.Table(str(sec / "SCCOMP.DBF"), _SCCOMP, codepage="cp874", dbf_type="vfp")
    t.open(dbf.READ_WRITE)
    for i in range(n):
        t.append({"COMPNAM": f"C{i}", "COMPCOD": f"COD{i}", "PATH": "DAT", "CANDEL": "Y"})
    t.close()


def test_sccomp_path_is_secure_subdir():
    assert str(express_locate.sccomp_path(r"X")).replace("/", "\\").endswith(r"X\secure\SCCOMP.DBF")


def test_company_count(tmp_path):
    _registry(tmp_path / "r", 3)
    assert express_locate.sccomp_company_count(tmp_path / "r") == 3
    assert express_locate.sccomp_company_count(tmp_path / "none") == 0  # 缺 → 0(不抛)


def test_detect_picks_richest_registry(tmp_path):
    a = tmp_path / "a"
    _registry(a, 1)  # 旧/空模板
    b = tmp_path / "b"
    _registry(b, 3)  # 活跃数据根
    assert express_locate.detect_express_root([str(a), str(b)]) == str(b)


def test_detect_none_when_no_sccomp(tmp_path):
    (tmp_path / "x").mkdir()
    assert express_locate.detect_express_root([str(tmp_path / "x")]) is None


def _norm(paths):
    return {str(p).lower().rstrip("\\/") for p in paths}


def test_scan_matches_any_exp_folder_name(tmp_path):
    # 任意年度/写法(NNexp · expNN · 小写)均命中,唯一判据是 secure/SCCOMP.DBF。
    _registry(tmp_path / "68EXP", 2)
    _registry(tmp_path / "EXP69", 2)
    _registry(tmp_path / "70exp", 2)
    _registry(tmp_path / "DATA", 2)  # 不含 EXP → 不该被扫到
    (tmp_path / "junk").mkdir()  # 无 SCCOMP → 不该被扫到
    hits = _norm(express_locate._scan_exp_roots([str(tmp_path)]))
    assert _norm([tmp_path / "68EXP", tmp_path / "EXP69", tmp_path / "70exp"]) <= hits
    assert (str(tmp_path / "DATA").lower()) not in hits


def test_scan_reaches_year_subdir(tmp_path):
    # 压在年份子目录下的年度根(如 S:\2568\EXP68)也要够得着(深度≤2)。
    _registry(tmp_path / "2568" / "EXP68", 3)
    hits = _norm(express_locate._scan_exp_roots([str(tmp_path)]))
    assert (str(tmp_path / "2568" / "EXP68").lower()) in hits


def test_env_var_wins_priority(tmp_path, monkeypatch):
    monkeypatch.setenv("PEARNLY_EXPRESS_ROOT", str(tmp_path / "chosen"))
    assert express_locate.candidate_roots()[0] == str(tmp_path / "chosen")
