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
    b = tmp_path / "drive" / "70EXP"  # 另一条路径指向同一个文件（硬链接：同一个文件号）= 同一份
    (b / "secure").mkdir(parents=True)
    os.link(a / "secure" / "SCCOMP.DBF", b / "secure" / "SCCOMP.DBF")
    roots = gui_pairing.rank_roots([str(a), str(b)])
    assert len(roots) == 1  # 两条写法判同一套,只列一次
    # 整个年度文件夹复制出来的（大小、修改时间一样，文件号不同）是另一份（10-24 验收 72EXP）
    c = tmp_path / "copy" / "72EXP"
    (c / "secure").mkdir(parents=True)
    shutil.copy2(a / "secure" / "SCCOMP.DBF", c / "secure" / "SCCOMP.DBF")
    assert len(gui_pairing.rank_roots([str(a), str(c)])) == 2


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


def test_a_place_reads_only_the_years_below_it(tmp_path):
    # 「Express 位置」（用户 10-24）：添加的是年度文件夹就只读这一年；是上层文件夹就读它下面的全部年度；位置外的一律不读
    _registry(tmp_path / "68EXP", 12)
    _registry(tmp_path / "69EXP", 3)
    (tmp_path / "NOTEXP").mkdir()
    _registry(tmp_path.parent / (tmp_path.name + "-other") / "70EXP", 20)   # 位置外

    picked = str(tmp_path / "68EXP")
    assert gui_pairing.places_roots([picked]) == [picked]
    assert gui_pairing.places_roots([str(tmp_path)]) == [picked, str(tmp_path / "69EXP")]
    assert gui_pairing.places_roots([str(tmp_path / "NOTEXP")]) == []


def test_a_drive_or_parent_folder_place_finds_the_express_data_below_it(tmp_path):
    # 位置是上层文件夹（如映射的共享盘 S:）：往下找，名字不带 EXP、在第 2 层的也认；默认年度（公司够多的最新年）放第一。
    share = tmp_path / "share"
    _registry(share / "2569" / "ACC69", 12)
    _registry(share / "บัญชี" / "68EXP", 15)
    (share / "Admin").mkdir(parents=True)
    assert gui_pairing.places_roots([str(share)]) == [str(share / "2569" / "ACC69"), str(share / "บัญชี" / "68EXP")]
    assert gui_pairing.places_roots([str(share / "Admin")]) == []
    # 几个位置一起：合并去重（一个在另一个里面的只算外面那个）
    assert gui_pairing.places_roots([str(share / "2569"), str(share)]) == [str(share / "2569" / "ACC69"),
                                                                            str(share / "บัญชี" / "68EXP")]
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
    # 「重新查找」只在「Express 位置」下面找（用户 10-24），不再整台电脑、局域网去搜
    places = ["C:\\", "S:\\"]
    monkeypatch.setattr(gui_pairing, "places_roots", lambda found_in: [r"S:\71EXP", r"C:\70EXP", r"C:\69EXP"])
    monkeypatch.setattr(gui_pairing, "machine_roots", lambda store: [r"C:\70EXP"])
    gone = lambda root: True  # noqa: E731
    assert gui_pairing.rescan_roots(None, places, gone=gone) == ([r"C:\70EXP", r"S:\71EXP", r"C:\69EXP"], [])
    monkeypatch.setattr(gui_pairing, "machine_roots", lambda store: [r"D:\GONE"])
    assert gui_pairing.rescan_roots(None, places, gone=gone) == ([r"S:\71EXP", r"C:\70EXP", r"C:\69EXP"], [])


def test_find_again_keeps_authorized_roots_it_cannot_reach_right_now(monkeypatch):
    """10-20 测试机：夜里服务器关着时「重新查找」只找到本机的 70EXP。原来授权、这次没找到的：暂时访问不到就保留（主根仍排
    第一），确实没了（所在盘 / 共享访问得到、文件夹不在）才去掉。"""
    monkeypatch.setattr(gui_pairing, "places_roots", lambda places: [r"C:\70EXP"])
    previous = [r"\\accserver\d$\ACCOUNT\69EXP", r"C:\70EXP", r"\\accserver\d$\ACCOUNT\68EXP", r"C:\OLD", r"S:\ELSE\67EXP"]
    places = ["C:\\", r"\\accserver\d$\ACCOUNT"]   # S: 不在位置里：原来授权的也不留
    roots, kept = gui_pairing.rescan_roots(None, places, previous, gone=lambda root: root == r"C:\OLD")
    assert kept == [r"\\accserver\d$\ACCOUNT\69EXP", r"\\accserver\d$\ACCOUNT\68EXP"]
    assert roots == [r"\\accserver\d$\ACCOUNT\69EXP", r"C:\70EXP", r"\\accserver\d$\ACCOUNT\68EXP"]


def test_a_root_is_gone_only_when_its_drive_or_share_answers_and_the_folder_is_missing(tmp_path, monkeypatch):
    from src.companion import scan_pace

    asked = []
    monkeypatch.setattr(scan_pace.GATE, "reachable", lambda path, retry=False: asked.append((path, retry)) or True)
    assert gui_pairing.root_gone(str(tmp_path / "missing")) is True
    assert gui_pairing.root_gone(str(tmp_path)) is False
    assert asked[0][1] is True   # 有人在等：不等退避到点，马上问
    # 所在服务器这会儿不在：不算没了，也不再碰文件夹
    monkeypatch.setattr(scan_pace.GATE, "reachable", lambda path, retry=False: asked.append(path) or False)
    assert gui_pairing.root_gone(str(tmp_path / "missing")) is False
    assert gui_pairing.root_gone("") is False
    # 同一次查找里同一台服务器只问一次（夜里同一台上的十几个年度）
    monkeypatch.setattr(scan_pace.GATE, "host", lambda path: "\\\\accserver\\d$")
    asked.clear()
    kept = gui_pairing.offline_roots([], [r"\\accserver\d$\ACCOUNT\68EXP", r"\\accserver\d$\ACCOUNT\69EXP"])
    assert kept == [r"\\accserver\d$\ACCOUNT\68EXP", r"\\accserver\d$\ACCOUNT\69EXP"] and len(asked) == 1


def test_the_same_folder_written_two_ways_is_one_root():
    assert gui_pairing.same_root("C:/PearnlyExpress\\70EXP", "c:\\pearnlyexpress\\70exp\\")
    assert not gui_pairing.same_root("C:/PearnlyExpress/70EXP", "C:/PearnlyExpress/69EXP")
    assert not gui_pairing.same_root("", "")
