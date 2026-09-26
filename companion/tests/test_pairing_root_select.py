# -*- coding: utf-8 -*-
r"""「年度/数据根」选择逻辑:多套年度根按年新→旧列出,默认跳过近空次年/测试根。

治真机实证坑:事务所 \\accserver\ACCOUNT 下有 59EXP…70EXP 十余套年度根,旧码写死认 70EXP
(仅 3 家测试)→ 客户只看到测试账套。改为列全部 + 默认选最新且非近空的年度根。
"""

from pathlib import Path
from types import SimpleNamespace

import dbf
import pytest

pytest.importorskip("PySide6")

from src.companion import gui_pairing  # noqa: E402

_SCCOMP = "COMPNAM C(40); COMPCOD C(10); PATH C(40); CANDEL C(1)"


def _registry(root, n):
    sec = root / "secure"
    sec.mkdir(parents=True, exist_ok=True)
    t = dbf.Table(str(sec / "SCCOMP.DBF"), _SCCOMP, codepage="cp874", dbf_type="vfp")
    t.open(dbf.READ_WRITE)
    for i in range(n):
        t.append(
            {"COMPNAM": f"C{i}", "COMPCOD": f"COD{i}", "PATH": "DAT", "CANDEL": "Y"}
        )
    t.close()


def test_year_key_parses_any_naming():
    assert gui_pairing._year_key("68EXP") == 68
    assert gui_pairing._year_key("EXP68") == 68
    assert gui_pairing._year_key("70exp") == 70
    assert gui_pairing._year_key("ExpressI") == -1  # 无年份 → 排最后


def test_rank_newest_year_first(tmp_path):
    _registry(tmp_path / "68EXP", 59)
    _registry(tmp_path / "69EXP", 8)
    _registry(tmp_path / "70EXP", 3)
    roots = gui_pairing._rank_roots(
        [str(tmp_path / "70EXP"), str(tmp_path / "68EXP"), str(tmp_path / "69EXP")]
    )
    assert [Path(r).name for r, _ in roots] == ["70EXP", "69EXP", "68EXP"]


def test_default_skips_near_empty_next_year(tmp_path):
    _registry(tmp_path / "68EXP", 59)  # 上一/当前会计年,真客户在此
    _registry(tmp_path / "69EXP", 8)  # 次年,刚起还很空
    _registry(tmp_path / "70EXP", 3)  # 更次年/测试,近空
    roots = gui_pairing._rank_roots(
        [str(tmp_path / "68EXP"), str(tmp_path / "69EXP"), str(tmp_path / "70EXP")]
    )
    assert (
        Path(gui_pairing._pick_default(roots)).name == "68EXP"
    )  # 不默认选 70/69 近空根


def test_default_falls_back_when_all_small(tmp_path):
    _registry(tmp_path / "67EXP", 2)
    _registry(tmp_path / "68EXP", 3)
    roots = gui_pairing._rank_roots([str(tmp_path / "67EXP"), str(tmp_path / "68EXP")])
    assert Path(gui_pairing._pick_default(roots)).name == "68EXP"  # 都很空 → 退最新


def test_dedup_and_drops_empty_roots(tmp_path):
    _registry(tmp_path / "68EXP", 5)
    (tmp_path / "EMPTY").mkdir()  # 无 SCCOMP → 丢弃
    roots = gui_pairing._rank_roots(
        [str(tmp_path / "68EXP"), str(tmp_path / "68EXP"), str(tmp_path / "EMPTY")]
    )
    assert [Path(r).name for r, _ in roots] == ["68EXP"]  # 去重 + 空根不入列


def test_dedup_same_root_via_two_path_spellings(tmp_path):
    # 同一物理数据根经两种写法(模拟 UNC vs 映射盘)进来 → 只列一次(按 SCCOMP 文件身份去重)。
    import os
    import shutil

    a = tmp_path / "unc" / "70EXP"
    _registry(a, 3)
    b = tmp_path / "drive" / "70EXP"  # 另一条路径,内容与 mtime 完全一致 = 同一份的镜像
    (b / "secure").mkdir(parents=True)
    shutil.copy2(a / "secure" / "SCCOMP.DBF", b / "secure" / "SCCOMP.DBF")
    st = (a / "secure" / "SCCOMP.DBF").stat()
    os.utime(b / "secure" / "SCCOMP.DBF", (st.st_atime, st.st_mtime))  # 对齐 mtime
    roots = gui_pairing._rank_roots([str(a), str(b)])
    assert len(roots) == 1  # 两条写法判同一套,只列一次


def test_new_profile_reuses_other_profiles_effective_root_as_candidate():
    store = SimpleNamespace(
        profiles=(
            SimpleNamespace(
                profile_id="A",
                express_root=r"S:\\EXP68",
                account_dir=r"S:\\EXP68\\ACME",
            ),
            SimpleNamespace(
                profile_id="B",
                express_root="",
                account_dir=r"P:\\EXP69\\BETA",
            ),
        )
    )

    roots = gui_pairing._profile_root_candidates(store, "A")

    assert roots == [r"P:\\EXP69"]
