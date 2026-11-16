# -*- coding: utf-8 -*-
"""代录认「是不是同一本账套」（11-16 验收首跑：CC 登记 \\\\bookserver\\d$\\ACCOUNT\\69EXP\\TEST2，Express 公司登记表在 P:（映射
\\\\bookserver\\ACCOUNT）那份里，两个共享名对不上，代录停在 company_pick）。底线「只写对话选中的账套」：认不准一律当不是。"""
from __future__ import annotations

from types import SimpleNamespace

from companion.express_operator import host as host_module
from companion.express_operator import session as session_module
from companion.express_operator.session import same_book

D_SHARE = r"\\bookserver\d$\ACCOUNT\69EXP\TEST2"
ACCOUNT_SHARE = r"\\bookserver\ACCOUNT\69EXP\TEST2"
MAPPED = r"P:\69EXP\TEST2"


def _share(path):
    return path.replace("P:", r"\\bookserver\ACCOUNT", 1) if path.upper().startswith("P:") else (path if path.startswith("\\\\") else "")


def _uids(table):
    calls = []

    def uid(path):
        calls.append(path)
        return table.get(path, "")

    return uid, calls


def test_literal_and_mapped_drive_are_the_same_without_reading_the_file_number():
    uid, calls = _uids({})
    assert same_book(ACCOUNT_SHARE, ACCOUNT_SHARE.lower(), share=_share, uid=uid)
    assert same_book(MAPPED, ACCOUNT_SHARE, share=_share, uid=uid)
    assert calls == []  # 前两层认得出就不取文件号


def test_two_share_names_of_one_folder_are_the_same_when_both_file_numbers_match():
    uid, _calls = _uids({D_SHARE: "vsn:1/99", MAPPED: "vsn:1/99"})
    assert same_book(D_SHARE, MAPPED, share=_share, uid=uid)


def test_a_cloned_disk_with_the_same_file_number_but_another_folder_name_is_not_the_same():
    other = r"\\backup\d$\ACCOUNT\69EXP\TEST"
    uid, calls = _uids({D_SHARE: "vsn:1/99", other: "vsn:1/99"})
    assert not same_book(D_SHARE, other, share=_share, uid=uid)
    assert calls == []  # 文件夹名不同：连文件号都不取


def test_a_copied_book_is_another_folder_and_not_the_same():
    copy = r"\\otherserver\ACCOUNT\69EXP\TEST2"
    uid, _calls = _uids({D_SHARE: "vsn:1/99", copy: "vsn:7/12"})
    assert not same_book(D_SHARE, copy, share=_share, uid=uid)


def test_without_a_file_number_on_either_side_it_is_not_the_same():
    for table in ({}, {D_SHARE: "vsn:1/99"}, {MAPPED: "vsn:1/99"}):
        uid, _calls = _uids(table)
        assert not same_book(D_SHARE, MAPPED, share=_share, uid=uid), table


def test_the_session_reads_each_file_number_once():
    uid, calls = _uids({D_SHARE: "vsn:1/99", MAPPED: "vsn:1/99"})
    s = session_module.Session(SimpleNamespace(), SimpleNamespace(data={}), express_exe="", account_dir=D_SHARE,
                               company_name="x", credential_target="t", share=_share, uid=uid)
    assert s.same_place(MAPPED, D_SHARE) and s.same_place(MAPPED, D_SHARE)
    assert sorted(calls) == sorted([MAPPED, D_SHARE])


def test_a_batch_held_for_a_person_counts_for_the_same_book_under_another_share_name(monkeypatch, tmp_path):
    """同一本账套别的批次正留着等人答框：换一个共享名写的同一本也算（host._held_elsewhere 改用 same_book）。"""
    monkeypatch.setattr(session_module, "_file_uid", lambda path: {D_SHARE: "vsn:1/99", ACCOUNT_SHARE: "vsn:1/99"}.get(path, ""))
    monkeypatch.setattr(session_module, "_share_path", _share)
    host = host_module.Host(tmp_path)
    held = SimpleNamespace(state=SimpleNamespace(account_dir=ACCOUNT_SHARE, held="doc-1"), still_held=lambda: True)
    host.runners = {"b1": held}
    assert host._held_elsewhere("b2", D_SHARE)
    monkeypatch.setattr(session_module, "_file_uid", lambda path: "")
    assert not host._held_elsewhere("b2", D_SHARE)  # 取不到文件号：不认作同一本（照旧只比字面 / 网络路径）


def test_the_company_list_row_is_found_for_a_book_registered_under_another_share_name(monkeypatch):
    """公司登记表（P: 那份）里的路径是映射盘写法、CC 登记的是 d$ 写法：同一个目录（文件号相同、文件夹名相同）就按它的行号选；
    文件号取不到照旧找不到、停下请人选（company_pick）。"""
    from companion import account_probe

    rows = [SimpleNamespace(path=r"P:\69EXP\TEST", row=4, name="company test"),
            SimpleNamespace(path=MAPPED, row=5, name="company test 2")]
    monkeypatch.setattr(account_probe, "probe_account_sets", lambda root, include_details=False: rows)
    for table, expected in (({D_SHARE: "vsn:1/99", MAPPED: "vsn:1/99"}, (5, "company test 2")), ({}, (0, ""))):
        uid, _calls = _uids(table)
        s = session_module.Session(SimpleNamespace(), SimpleNamespace(data={}), express_exe=r"P:\Express.exe",
                                   account_dir=D_SHARE, company_name="x", credential_target="t", share=_share, uid=uid)
        assert s.registry() == expected, table
