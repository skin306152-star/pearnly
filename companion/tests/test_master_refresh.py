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

    def slow_sets(root):
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
        account_probe, "account_sets_payload", lambda root: scans.append(root) or []
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


def test_endpoint_refresh_only_scans_bound_root_and_skips_chart(monkeypatch):
    scans = []

    def lightweight_sets(root, *, include_details):
        scans.append((root, include_details))
        return [{"path": "C:/books/A"}]

    monkeypatch.setattr(
        account_probe,
        "account_sets_payload",
        lightweight_sets,
    )
    monkeypatch.setattr(
        account_probe,
        "chart_accounts_payload",
        lambda _path: (_ for _ in ()).throw(AssertionError("chart scan must wait")),
    )
    monkeypatch.setattr(
        express_locate,
        "candidate_roots",
        lambda: (_ for _ in ()).throw(AssertionError("discovery root must not scan")),
    )

    master_refresh.request_master(
        "C:/books", "C:/books/A", force=True, account_sets_only=True
    )
    deadline = time.time() + 2
    result = (None, 0.0)
    while time.time() < deadline:
        result = master_refresh.request_master(
            "C:/books",
            "C:/books/A",
            force=True,
            account_sets_only=True,
        )
        if result[0] is not None:
            break
        time.sleep(0.01)

    assert result[0] == {"account_sets": [{"path": "C:/books/A"}]}
    assert scans == [("C:/books", False)]


def test_endpoint_refresh_scans_each_authorized_root_once(monkeypatch):
    scans = []

    def lightweight_sets(root, *, include_details):
        scans.append((root, include_details))
        return [{"path": f"{root}/ACME", "root": root}]

    monkeypatch.setattr(account_probe, "account_sets_payload", lightweight_sets)
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
    deadline = time.time() + 2
    result = (None, 0.0)
    while time.time() < deadline:
        result = master_refresh.request_master("C:/books", "C:/books/A", **kwargs)
        if result[0] is not None:
            break
        time.sleep(0.01)

    assert scans == [("C:/books", False), ("D:/books-2020", False)]
    assert [row["root"] for row in result[0]["account_sets"]] == [
        "C:/books",
        "D:/books-2020",
    ]


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
