from __future__ import annotations

import threading
import time

from src.companion import account_probe, express_locate, master_refresh


def setup_function():
    master_refresh._reset_for_tests()


def test_slow_master_scan_is_non_blocking_and_shared(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow_sets(root, **_kwargs):
        calls.append(root)
        started.set()
        release.wait(2)
        return [{"path": r"\\server\books\A", "writable": True}]

    monkeypatch.setattr(account_probe, "account_sets_payload", slow_sets)
    monkeypatch.setattr(
        account_probe, "chart_accounts_payload", lambda _path: [{"code": "1100"}]
    )
    monkeypatch.setattr(express_locate, "candidate_roots", lambda: [])

    before = time.monotonic()
    assert master_refresh.request_master(r"\\server\books", r"\\server\books\A") == (
        None,
        0.0,
    )
    assert time.monotonic() - before < 0.2
    assert started.wait(1)
    assert master_refresh.request_master(r"//server/books/", r"//server/books/A/") == (
        None,
        0.0,
    )
    assert calls == [r"\\server\books"]

    release.set()
    deadline = time.time() + 2
    result = (None, 0.0)
    while time.time() < deadline:
        result = master_refresh.request_master(r"\\server\books", r"\\server\books\A")
        if result[0] is not None:
            break
        time.sleep(0.01)
    assert result[0] == {
        "account_sets": [{"path": r"\\server\books\A", "writable": True}],
        "accounts": [{"code": "1100"}],
    }
    assert result[1] > 0


def test_consumed_snapshot_does_not_rescan_before_interval(monkeypatch):
    scans = []
    monkeypatch.setattr(
        account_probe, "account_sets_payload", lambda root, **_kwargs: scans.append(root) or []
    )
    monkeypatch.setattr(account_probe, "chart_accounts_payload", lambda _path: [])
    monkeypatch.setattr(express_locate, "candidate_roots", lambda: [])

    master_refresh.request_master("C:/books", "C:/books/A")
    deadline = time.time() + 2
    first = (None, 0.0)
    while time.time() < deadline:
        first = master_refresh.request_master("C:/books", "C:/books/A")
        if first[0] is not None:
            break
        time.sleep(0.01)
    assert first[0] == {"account_sets": [], "accounts": []}
    assert master_refresh.request_master(
        "C:/books", "C:/books/A", newer_than=first[1]
    ) == (None, 0.0)
    assert scans == ["C:/books"]


def _wait(call):
    deadline = time.time() + 2
    result = (None, 0.0)
    while time.time() < deadline:
        result = call()
        if result[0] is not None:
            break
        time.sleep(0.01)
    return result


def test_endpoint_refresh_reads_company_names_and_skips_chart(monkeypatch):
    """网页「重新读取」:扫描带公司名(不再有丢公司名的快扫),科目表跟着所选账套另行刷新;
    没要求时不去找新的年度文件夹。"""
    scans = []

    def sets(root, **kwargs):
        scans.append((root, kwargs))
        return [{"path": "C:/books/A", "company": "ACME"}]

    monkeypatch.setattr(account_probe, "account_sets_payload", sets)
    monkeypatch.setattr(
        account_probe,
        "chart_accounts_payload",
        lambda _path: (_ for _ in ()).throw(AssertionError("chart scan must wait")),
    )
    monkeypatch.setattr(
        express_locate,
        "candidate_roots",
        lambda: (_ for _ in ()).throw(AssertionError("discovery was not asked for")),
    )

    kwargs = {"force": True, "account_sets_only": True}
    master_refresh.request_master("C:/books", "C:/books/A", **kwargs)
    result = _wait(lambda: master_refresh.request_master("C:/books", "C:/books/A", **kwargs))

    assert result[0] == {"account_sets": [{"path": "C:/books/A", "company": "ACME"}]}
    # 带详情（不传 include_details）；一次扫描共用一个换算网络路径的 locator（账套行的 uid / share，device_facts）、
    # 一个限 CPU 的节拍器（pace），登记表没变就用本机索引里的行（reuse_registry，2026-10-18）
    assert [root for root, _kwargs in scans] == ["C:/books"]
    assert set(scans[0][1]) == {"locator", "pace", "reuse_registry"} and scans[0][1]["reuse_registry"] is True


def test_endpoint_refresh_scans_each_authorized_root_once(monkeypatch):
    scans = []

    def sets(root, **kwargs):
        scans.append(root)
        return [{"path": f"{root}/ACME", "root": root}]

    monkeypatch.setattr(account_probe, "account_sets_payload", sets)
    monkeypatch.setattr(
        account_probe,
        "chart_accounts_payload",
        lambda _path: (_ for _ in ()).throw(AssertionError("chart scan must wait")),
    )

    kwargs = {
        "force": True,
        "account_sets_only": True,
        "authorized_roots": ["C:/books", "D:/books-2020", "d:\\books-2020\\"],
    }
    master_refresh.request_master("C:/books", "C:/books/A", **kwargs)
    result = _wait(lambda: master_refresh.request_master("C:/books", "C:/books/A", **kwargs))

    assert scans == ["C:/books", "D:/books-2020"]
    assert [row["root"] for row in result[0]["account_sets"]] == [
        "C:/books",
        "D:/books-2020",
    ]


def test_forced_refresh_with_discovery_adds_new_year_folders_once(monkeypatch):
    """「重新读取」+ 全部授权:新建的年度文件夹也扫进来;同一份登记表换个路径看见的只算一次。"""
    stamps = {"C:/books": (1, 1), "E:/70EXP": (2, 2), r"\\server\books": (1, 1), "F:/empty": (3, 3)}
    monkeypatch.setattr(master_refresh, "_sccomp_stamp", lambda root, **_kwargs: stamps.get(root))
    monkeypatch.setattr(
        express_locate,
        "candidate_roots",
        lambda: ["C:/books", r"\\server\books", "E:/70EXP", "F:/empty", "G:/missing"],
    )
    monkeypatch.setattr(
        express_locate, "sccomp_company_count", lambda root, **_kwargs: 0 if root == "F:/empty" else 5
    )
    monkeypatch.setattr(
        account_probe, "account_sets_payload", lambda root, **_kwargs: [{"path": f"{root}/A", "root": root}]
    )

    kwargs = {"force": True, "account_sets_only": True, "discover": True}
    master_refresh.request_master("C:/books", "C:/books/A", **kwargs)
    result = _wait(lambda: master_refresh.request_master("C:/books", "C:/books/A", **kwargs))

    assert result[0]["roots"] == ["C:/books", "E:/70EXP"]
    assert [row["root"] for row in result[0]["account_sets"]] == ["C:/books", "E:/70EXP"]


def test_unchanged_registry_is_not_rescanned_until_it_changes(monkeypatch):
    """平时只看登记表有没有变:没变不重扫、不出新快照;变了才重扫。"""
    scans = []
    stamp = {"value": (1, 1)}
    monkeypatch.setattr(master_refresh, "_WATCH_INTERVAL_S", 0.0)
    monkeypatch.setattr(master_refresh, "_sccomp_stamp", lambda root, **_kwargs: stamp["value"])
    monkeypatch.setattr(
        account_probe, "account_sets_payload", lambda root, **_kwargs: scans.append(root) or [{"path": "C:/books/A"}]
    )
    monkeypatch.setattr(account_probe, "chart_accounts_payload", lambda _path: [])

    first = _wait(lambda: master_refresh.request_master("C:/books", "C:/books/A"))
    assert scans == ["C:/books"]

    for _ in range(5):
        assert master_refresh.request_master("C:/books", "C:/books/A", newer_than=first[1]) == (None, 0.0)
        time.sleep(0.02)
    assert scans == ["C:/books"]

    stamp["value"] = (2, 2)
    second = _wait(lambda: master_refresh.request_master("C:/books", "C:/books/A", newer_than=first[1]))
    assert second[1] > first[1]
    assert scans == ["C:/books", "C:/books"]


def test_failed_scan_exposes_request_scoped_error(monkeypatch):
    monkeypatch.setattr(
        account_probe,
        "account_sets_payload",
        lambda _root: (_ for _ in ()).throw(OSError()),
    )
    monkeypatch.setattr(express_locate, "candidate_roots", lambda: [])

    master_refresh.request_master("C:/books", "C:/books/A", force=True)
    deadline = time.time() + 2
    completed_at = 0.0
    while time.time() < deadline and not completed_at:
        _, completed_at = master_refresh.request_master(
            "C:/books", "C:/books/A", force=True
        )
        time.sleep(0.01)

    assert completed_at > 0
    assert (
        master_refresh.refresh_error(
            "C:/books", "C:/books/A", newer_than=completed_at - 1
        )
        == "ERR_MASTER_SCAN_FAILED"
    )


def test_slow_registry_stamp_does_not_hold_back_a_full_scan(monkeypatch):
    """够不到的共享盘读修改时间会卡好几秒(Windows):整套重扫先交结果,修改时间随后再记。"""
    monkeypatch.setattr(master_refresh, "_sccomp_stamp", lambda root, **_kwargs: time.sleep(1.5) or (1, 1))
    monkeypatch.setattr(account_probe, "account_sets_payload", lambda root, **_kwargs: [{"path": f"{root}/A"}])
    kwargs = {"force": True, "account_sets_only": True}

    started = time.monotonic()
    master_refresh.request_master(r"\\server\books", r"\\server\books\A", **kwargs)
    result = _wait(lambda: master_refresh.request_master(r"\\server\books", r"\\server\books\A", **kwargs))

    assert result[0] == {"account_sets": [{"path": r"\\server\books/A"}]}
    assert time.monotonic() - started < 1.0


def test_a_full_rescan_also_looks_for_new_year_folders_without_anyone_clicking(monkeypatch):
    """2026-10-08：启动后第一次整套扫描（之后每天一次）就找新年度文件夹，不用等网页「重新读取」；
    平时只比登记表变没变的那几次不找（不额外跑盘）。"""
    looked = []
    monkeypatch.setattr(master_refresh, "_WATCH_INTERVAL_S", 0.0)
    monkeypatch.setattr(master_refresh, "_sccomp_stamp", lambda root, **_kwargs: (1, 1) if root != "E:/71EXP" else (2, 2))
    monkeypatch.setattr(express_locate, "candidate_roots", lambda: looked.append(True) or ["E:/71EXP"])
    monkeypatch.setattr(express_locate, "sccomp_company_count", lambda root, **_kwargs: 5)
    monkeypatch.setattr(account_probe, "account_sets_payload", lambda root, **_kwargs: [{"path": f"{root}/A", "root": root}])
    monkeypatch.setattr(account_probe, "chart_accounts_payload", lambda _path: [])

    first = _wait(lambda: master_refresh.request_master("C:/books", "C:/books/A", discover=True))
    assert first[0]["roots"] == ["C:/books", "E:/71EXP"]
    assert looked == [True]

    master_refresh.request_master("C:/books", "C:/books/A", newer_than=first[1], discover=True)
    time.sleep(0.1)
    assert looked == [True]  # 平时的变化检查不找


def test_after_rescan_soon_an_earlier_snapshot_is_never_handed_out_again(monkeypatch):
    """10-20 测试机：设置里「重新查找」重启取任务线程后，头一拍拿到的是进程里缓存的上一遍（账套目录改名时扫的），目录改回
    原名后仍报旧行，直到重启 CC。rescan_soon() 之后之前扫的结果不再交出去，下一次就是新扫的。"""
    scans = []

    def sets(root, **_kwargs):
        scans.append(root)
        return [{"path": "C:/books/A", "scan": len(scans)}]

    monkeypatch.setattr(account_probe, "account_sets_payload", sets)
    monkeypatch.setattr(account_probe, "chart_accounts_payload", lambda _path: [])
    monkeypatch.setattr(express_locate, "candidate_roots", lambda: [])

    first = _wait(lambda: master_refresh.request_master("C:/books", "C:/books/A"))
    assert first[0]["account_sets"] == [{"path": "C:/books/A", "scan": 1}]
    # 取任务线程重启（newer_than 从 0 起）：没要求重扫时照旧先交缓存的那一遍
    assert master_refresh.request_master("C:/books", "C:/books/A")[0] == first[0]
    master_refresh.rescan_soon()
    assert master_refresh.request_master("C:/books", "C:/books/A") == (None, 0.0)
    second = _wait(lambda: master_refresh.request_master("C:/books", "C:/books/A"))
    assert second[0]["account_sets"] == [{"path": "C:/books/A", "scan": 2}]
    assert scans == ["C:/books", "C:/books"]
