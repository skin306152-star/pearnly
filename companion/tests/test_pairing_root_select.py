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


def test_picking_a_drive_or_parent_folder_finds_the_express_data_below_it(tmp_path):
    # 选的是上层文件夹（如映射的共享盘 S:）：往下找，名字不带 EXP、在第 2 层的也认；默认年度（公司够多的最新年）放第一。
    share = tmp_path / "share"
    _registry(share / "2569" / "ACC69", 12)
    _registry(share / "บัญชี" / "68EXP", 15)
    (share / "Admin").mkdir(parents=True)
    assert gui_pairing.roots_for_pick(str(share)) == [str(share / "2569" / "ACC69"), str(share / "บัญชี" / "68EXP")]
    assert gui_pairing.roots_for_pick(str(share / "Admin")) == []


def test_changing_the_machine_root_updates_every_member_and_keeps_what_still_fits(tmp_path, monkeypatch):
    # 2026-10-08：Express 账套是这台电脑的全局设置，「更换…」同步到全部成员（暂停的也同步、仍保持暂停）。
    root70, root69 = tmp_path / "70EXP", tmp_path / "69EXP"
    acme, newco = root70 / "ACME", root70 / "NEWCO"
    other_drive = tmp_path / "D-drive" / "BETA"  # 登记在 69 年度、放在别的盘（登记表写绝对路径）
    for folder in (acme, newco, other_drive):
        folder.mkdir(parents=True)
    store = ProfileStore.load(tmp_path / "c.json", protector=_Protector())
    _member(store, "exp_a_1", str(root70), str(acme), revenue="4999")         # 仍登记在主根：原样保留
    _member(store, "exp_b_2", r"S:\\OLD", str(other_drive), revenue="4888")  # 登记在另一年度：保留，主根改成那一年
    _member(store, "exp_c_3", r"S:\\GONE", r"S:\\GONE\\OLD", paused=True)  # 新数据根里没有：换成默认账套
    registry = {
        str(root70): [AccountSet(code="A", name="ACME", company="", path=str(acme), tax_id="", writable=False, row=1),
                      AccountSet(code="N", name="NEWCO", company="", path=str(newco), tax_id="", writable=False, row=2)],
        str(root69): [AccountSet(code="B", name="BETA", company="", path=str(other_drive), tax_id="", writable=False,
                                 row=1)],
    }
    detailed = [AccountSet(code="N", name="NEWCO", company="New Co.", path=str(newco), tax_id="", writable=True, row=2)]
    monkeypatch.setattr(gui_pairing, "probe_account_sets",
                        lambda root, include_details=True: registry[root] if not include_details else detailed)
    monkeypatch.setattr(gui_pairing, "probe_default_accounts", lambda path: {"revenue_acc": "4100"})

    roots = [str(root70), str(root69)]
    assert gui_pairing.apply_roots(store, roots) == 3

    reloaded = ProfileStore.load(tmp_path / "c.json", protector=_Protector())
    first, second, third = (reloaded.runtime_config(record.profile_id) for record in reloaded.profiles)
    assert (first.express_root, list(first.authorized_roots)) == (str(root70), roots)  # 新年度在前
    assert (first.account_dir, first.account_company, first.revenue_acc) == (str(acme), "Old Co.", "4999")
    # 推送按「数据根 + 账套」找账套：主根必须是真正登记它的那一年
    assert (second.express_root, list(second.authorized_roots)) == (str(root69), roots)
    assert (second.account_dir, second.revenue_acc) == (str(other_drive), "4888")
    assert (third.express_root, third.account_dir, third.account_company, third.account_set_row,
            third.revenue_acc) == (str(root70), str(newco), "New Co.", 2, "4100")
    assert [record.paused for record in reloaded.profiles] == [False, False, True]
    assert [cfg.bearer_token for cfg in (first, second, third)] == ["exp_a_1", "exp_b_2", "exp_c_3"]


def test_nothing_is_written_when_a_member_has_no_usable_account_set(tmp_path, monkeypatch):
    store = ProfileStore.load(tmp_path / "c.json", protector=_Protector())
    _member(store, "exp_a_1", r"S:\\EXP68", r"S:\\EXP68\\ACME")
    before = (tmp_path / "c.json").read_bytes()
    monkeypatch.setattr(gui_pairing, "probe_account_sets", lambda root, include_details=True: [])

    import pytest

    with pytest.raises(ValueError):
        gui_pairing.apply_roots(store, [str(tmp_path / "70EXP")])
    assert (tmp_path / "c.json").read_bytes() == before


def test_find_again_keeps_the_root_in_use_first(monkeypatch):
    monkeypatch.setattr(gui_pairing, "detect_roots", lambda store: [r"S:\71EXP", r"C:\70EXP", r"C:\69EXP"])
    monkeypatch.setattr(gui_pairing, "machine_roots", lambda store: [r"C:\70EXP"])
    assert gui_pairing.rescan_roots(None) == [r"C:\70EXP", r"S:\71EXP", r"C:\69EXP"]
    monkeypatch.setattr(gui_pairing, "machine_roots", lambda store: [r"D:\GONE"])
    assert gui_pairing.rescan_roots(None) == [r"S:\71EXP", r"C:\70EXP", r"C:\69EXP"]
