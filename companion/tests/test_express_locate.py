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


# ── 深层扫描（2026-10-08：换事务所 / 局域网布局不漏扫）──────────────────────────────


def test_deep_walk_finds_any_folder_name_up_to_three_levels(tmp_path):
    # 不叫 *EXP* 的（ACC68）、放得更深的（บัญชี\2568\Data 下）都要找到；第 4 层不找（有界）。
    _registry(tmp_path / "ACC68", 2)
    _registry(tmp_path / "บัญชี" / "2568" / "DATA69", 2)
    _registry(tmp_path / "a" / "b" / "c" / "TOO_DEEP", 2)
    hits = _norm(express_locate._walk_data_roots(str(tmp_path)))
    assert hits == _norm([tmp_path / "ACC68", tmp_path / "บัญชี" / "2568" / "DATA69"])


def test_deep_walk_does_not_descend_into_account_or_system_folders(tmp_path):
    # 账套文件夹（直接放着 .DBF）与系统目录里不会再套数据根：不进去，省时间。
    account = tmp_path / "BOOKS" / "ACME"
    account.mkdir(parents=True)
    (account / "ARTRN.DBF").write_bytes(b"")
    _registry(account / "nested", 2)
    _registry(tmp_path / "Windows" / "EXP70", 2)
    _registry(tmp_path / "BOOKS" / "EXP71", 2)
    hits = _norm(express_locate._walk_data_roots(str(tmp_path)))
    assert hits == _norm([tmp_path / "BOOKS" / "EXP71"])


def test_deep_walk_stops_at_its_directory_cap(tmp_path):
    for index in range(30):
        (tmp_path / f"d{index:02d}").mkdir()
    _registry(tmp_path / "d29" / "inner" / "EXP70", 2)
    assert express_locate._walk_data_roots(str(tmp_path), max_dirs=5) == []


def test_deep_scan_keeps_start_order_and_drops_a_start_that_hangs(tmp_path, monkeypatch):
    import threading
    import time

    _registry(tmp_path / "one" / "ACC68", 2)
    _registry(tmp_path / "two" / "ACC69", 2)
    real = express_locate._walk_data_roots
    release = threading.Event()

    def walk(start, **kwargs):
        if start.endswith("hang"):
            release.wait(5)  # 模拟够不到的网络盘
            return ["never"]
        return real(start, **kwargs)

    monkeypatch.setattr(express_locate, "_walk_data_roots", walk)
    began = time.monotonic()
    hits = express_locate._deep_scan([str(tmp_path / "two"), str(tmp_path / "hang"), str(tmp_path / "one")],
                                     total_seconds=0.5)
    release.set()
    assert time.monotonic() - began < 2
    assert [p.lower() for p in hits] == [str(tmp_path / "two" / "ACC69").lower(), str(tmp_path / "one" / "ACC68").lower()]


def test_network_shares_come_from_mapped_drives_and_map_history(monkeypatch):
    # 映射过的网络盘（断开的也算）与「映射网络驱动器」用过的地址都当起点；只要 \\服务器\共享 形式的。
    import sys
    import types

    tree = {
        "Network": {"S": {"RemotePath": r"\\accserver\ACCOUNT"}, "P": {"RemotePath": r"\\nas01\Books\\"}},
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\Map Network Drive MRU": {
            "a": r"\\nas01\books", "b": r"\\192.168.1.20\express", "c": "not-a-share", "MRUList": "abc"},
    }

    class Key:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def open_key(_hive, path):
        node = tree
        for part in [path] if path in tree else path.split("\\"):
            if part not in node:
                raise OSError(path)
            node = node[part]
        return Key(node)

    def enum_key(key, index):
        names = [name for name, value in key.path.items() if isinstance(value, dict)]
        if index >= len(names):
            raise OSError
        return names[index]

    def enum_value(key, index):
        values = [(name, value) for name, value in key.path.items() if not isinstance(value, dict)]
        if index >= len(values):
            raise OSError
        return (*values[index], 1)

    fake = types.SimpleNamespace(HKEY_CURRENT_USER=object(), OpenKey=open_key, EnumKey=enum_key, EnumValue=enum_value)
    monkeypatch.setitem(sys.modules, "winreg", fake)

    assert express_locate._network_share_roots() == [r"\\accserver\ACCOUNT", r"\\nas01\Books", r"\\192.168.1.20\express"]


def test_candidate_roots_adds_the_deep_scan_after_the_classic_one(tmp_path, monkeypatch):
    _registry(tmp_path / "EXP70", 2)
    _registry(tmp_path / "Office" / "Books" / "ACC68", 2)
    monkeypatch.delenv("PEARNLY_EXPRESS_ROOT", raising=False)
    monkeypatch.setattr(express_locate, "_registry_install_dirs", lambda: [])
    monkeypatch.setattr(express_locate, "_shortcut_candidate_roots", lambda: [])
    monkeypatch.setattr(express_locate, "_mounted_drive_roots", lambda: [str(tmp_path)])
    monkeypatch.setattr(express_locate, "_SHARE_PARENTS", ())
    monkeypatch.setattr(express_locate, "_network_share_roots", lambda: [])
    monkeypatch.setattr(express_locate, "_user_folders", lambda: [])

    assert [p.lower() for p in express_locate.candidate_roots()] == [
        str(tmp_path / "EXP70").lower(), str(tmp_path / "Office" / "Books" / "ACC68").lower()]


_WINDOWS = __import__("sys").platform == "win32"


@__import__("pytest").mark.skipif(not _WINDOWS, reason="真实 Windows：注册表、盘符、NTFS 联接点")
def test_on_real_windows_the_full_detection_finishes_and_finds_a_deep_unusual_root(tmp_path):
    import time

    _registry(tmp_path / "Office" / "บัญชี" / "ACC68", 2)  # 名字不带 EXP、在第 3 层
    assert isinstance(express_locate._network_share_roots(), list)  # 真注册表读得动、不抛
    began = time.monotonic()
    every = express_locate.candidate_roots()  # 整机检测：盘符、注册表、快捷方式、深层扫描
    took = time.monotonic() - began
    assert took < express_locate._DEEP_TOTAL_SECONDS + 15, took
    assert [p.lower() for p in express_locate._deep_scan([str(tmp_path)])] == [
        str(tmp_path / "Office" / "บัญชี" / "ACC68").lower()]
    assert isinstance(every, list)


@__import__("pytest").mark.skipif(not _WINDOWS, reason="NTFS 联接点只在 Windows 上有")
def test_on_real_windows_a_junction_loop_is_not_followed(tmp_path):
    import subprocess

    (tmp_path / "a").mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(tmp_path / "a" / "loop"), str(tmp_path)], check=True,
                   capture_output=True)
    _registry(tmp_path / "a" / "EXP70", 2)
    assert [p.lower() for p in express_locate._walk_data_roots(str(tmp_path))] == [str(tmp_path / "a" / "EXP70").lower()]
