# -*- coding: utf-8 -*-
r"""这台电脑的 Express 账套(express_machine):多套年度根按年新→旧列出,默认跳过近空次年/测试根;
「更换…」时同步到全部成员。

治真机实证坑:事务所 \\accserver\ACCOUNT 下有 59EXP…70EXP 十余套年度根,旧码写死认 70EXP
(仅 3 家测试)→ 客户只看到测试账套。改为列全部 + 默认选最新且非近空的年度根。
"""

from pathlib import Path
from types import SimpleNamespace

import base64

import dbf

from src.companion import express_machine as gui_pairing
from src.companion.account_probe import AccountSet
from src.companion.profile_store import ProfileStore, ProfileStoreError

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
    assert gui_pairing.year_key("68EXP") == 68
    assert gui_pairing.year_key("EXP68") == 68
    assert gui_pairing.year_key("70exp") == 70
    assert gui_pairing.year_key("ExpressI") == -1  # 无年份 → 排最后


def test_rank_newest_year_first(tmp_path):
    _registry(tmp_path / "68EXP", 59)
    _registry(tmp_path / "69EXP", 8)
    _registry(tmp_path / "70EXP", 3)
    roots = gui_pairing.rank_roots(
        [str(tmp_path / "70EXP"), str(tmp_path / "68EXP"), str(tmp_path / "69EXP")]
    )
    assert [Path(r).name for r, _ in roots] == ["70EXP", "69EXP", "68EXP"]


def test_default_skips_near_empty_next_year(tmp_path):
    _registry(tmp_path / "68EXP", 59)  # 上一/当前会计年,真客户在此
    _registry(tmp_path / "69EXP", 8)  # 次年,刚起还很空
    _registry(tmp_path / "70EXP", 3)  # 更次年/测试,近空
    roots = gui_pairing.rank_roots(
        [str(tmp_path / "68EXP"), str(tmp_path / "69EXP"), str(tmp_path / "70EXP")]
    )
    assert (
        Path(gui_pairing.pick_default(roots)).name == "68EXP"
    )  # 不默认选 70/69 近空根


def test_default_falls_back_when_all_small(tmp_path):
    _registry(tmp_path / "67EXP", 2)
    _registry(tmp_path / "68EXP", 3)
    roots = gui_pairing.rank_roots([str(tmp_path / "67EXP"), str(tmp_path / "68EXP")])
    assert Path(gui_pairing.pick_default(roots)).name == "68EXP"  # 都很空 → 退最新


def test_dedup_and_drops_empty_roots(tmp_path):
    _registry(tmp_path / "68EXP", 5)
    (tmp_path / "EMPTY").mkdir()  # 无 SCCOMP → 丢弃
    roots = gui_pairing.rank_roots(
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
    roots = gui_pairing.rank_roots([str(a), str(b)])
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

    roots = gui_pairing.profile_root_candidates(store, "A")

    assert roots == [r"P:\\EXP69"]


class _Protector:
    def protect(self, value):
        return "t:" + base64.b64encode(value.encode()).decode()

    def unprotect(self, value):
        if not value.startswith("t:"):
            raise ProfileStoreError("bad")
        return base64.b64decode(value[2:]).decode()


def _member(store, token, root, account, *, paused=False, revenue="4100"):
    cfg = store.new_runtime_config()
    cfg.update(backend_url="https://testcowork.pearnly.com", bearer_token=token, account_dir=account,
               account_set=account, account_company="Old Co.", express_root=root, authorized_roots=[root],
               revenue_acc=revenue, paired=True)
    store.save_profile(cfg, paused=paused)


def test_machine_roots_is_the_union_of_every_members_roots(tmp_path):
    store = ProfileStore.load(tmp_path / "c.json", protector=_Protector())
    _member(store, "exp_a_1", r"S:\\EXP68", r"S:\\EXP68\\ACME")
    _member(store, "exp_b_2", r"S:\\EXP69", r"S:\\EXP69\\BETA")

    assert gui_pairing.machine_roots(store) == [r"S:\\EXP68", r"S:\\EXP69"]


def test_a_picked_root_comes_first_with_the_other_years_beside_it(tmp_path):
    _registry(tmp_path / "68EXP", 12)
    _registry(tmp_path / "69EXP", 3)
    (tmp_path / "NOTEXP").mkdir()

    picked = str(tmp_path / "68EXP")
    assert gui_pairing.roots_for_pick(picked) == [picked, str(tmp_path / "69EXP")]
    assert gui_pairing.roots_for_pick(str(tmp_path / "NOTEXP")) == []


def test_changing_the_machine_root_updates_every_member_and_keeps_what_still_fits(tmp_path, monkeypatch):
    # 2026-10-08：Express 账套是这台电脑的全局设置，「更换…」同步到全部成员（暂停的也同步、仍保持暂停）。
    new_root = tmp_path / "70EXP"
    kept = new_root / "ACME"
    fresh = new_root / "NEWCO"
    for folder in (kept, fresh):
        folder.mkdir(parents=True)
    store = ProfileStore.load(tmp_path / "c.json", protector=_Protector())
    _member(store, "exp_a_1", str(new_root), str(kept), revenue="4999")  # 账套还在新根下：原样保留
    _member(store, "exp_b_2", r"S:\\GONE", r"S:\\GONE\\OLD", paused=True)  # 不在了：换成默认账套
    monkeypatch.setattr(gui_pairing, "probe_account_sets", lambda root: [
        AccountSet(code="N", name="NEWCO", company="New Co.", path=str(fresh), tax_id="", writable=True, row=3)])
    monkeypatch.setattr(gui_pairing, "probe_default_accounts", lambda path: {"revenue_acc": "4100"})

    roots = [str(new_root), str(tmp_path / "69EXP")]
    assert gui_pairing.apply_roots(store, roots) == 2

    reloaded = ProfileStore.load(tmp_path / "c.json", protector=_Protector())
    first, second = (reloaded.runtime_config(record.profile_id) for record in reloaded.profiles)
    for cfg in (first, second):
        assert cfg.express_root == str(new_root)
        assert list(cfg.authorized_roots) == roots
    assert (first.account_dir, first.revenue_acc) == (str(kept), "4999")
    assert (second.account_dir, second.account_company, second.account_set_row, second.revenue_acc) == (
        str(fresh), "New Co.", 3, "4100")
    assert [record.paused for record in reloaded.profiles] == [False, True]
    assert first.bearer_token == "exp_a_1" and second.bearer_token == "exp_b_2"
