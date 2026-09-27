from __future__ import annotations

import threading
import time

from src.companion import account_probe, catalog_refresh


def setup_function():
    catalog_refresh._reset_for_tests()


def test_slow_scan_returns_immediately_and_is_shared(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow_probe(account_dir):
        calls.append(account_dir)
        started.set()
        release.wait(2)
        return {"products": [{"code": "P1"}]}

    monkeypatch.setattr(catalog_refresh, "catalog_payload", slow_probe)

    assert catalog_refresh.request_catalog(r"\\server\books\A") == (None, 0.0)
    assert started.wait(1)
    assert catalog_refresh.request_catalog(r"//server/books/A/") == (None, 0.0)
    assert calls == [r"\\server\books\A"]

    release.set()
    deadline = time.time() + 2
    while time.time() < deadline:
        payload, completed_at = catalog_refresh.request_catalog(r"\\server\books\A")
        if payload is not None:
            break
        time.sleep(0.01)
    assert payload == {"products": [{"code": "P1"}]}
    assert completed_at > 0


def test_sent_snapshot_schedules_a_new_refresh(monkeypatch):
    values = iter(({"products": [1]}, {"products": [2]}))
    monkeypatch.setattr(catalog_refresh, "catalog_payload", lambda _path: next(values))

    catalog_refresh.request_catalog(r"C:\books\A")
    deadline = time.time() + 2
    first = None
    while time.time() < deadline:
        first = catalog_refresh.request_catalog(r"C:\books\A")
        if first[0] is not None:
            break
        time.sleep(0.01)
    assert first[0] == {"products": [1]}

    assert catalog_refresh.request_catalog(r"C:\books\A", newer_than=first[1]) == (
        None,
        0.0,
    )
    deadline = time.time() + 2
    second = None
    while time.time() < deadline:
        second = catalog_refresh.request_catalog(r"C:\books\A", newer_than=first[1])
        if second[0] is not None:
            break
        time.sleep(0.01)
    assert second[0] == {"products": [2]}
    assert second[1] >= first[1]


def test_failed_target_scan_exposes_request_scoped_error(monkeypatch):
    monkeypatch.setattr(
        catalog_refresh,
        "catalog_payload",
        lambda _path: (_ for _ in ()).throw(OSError()),
    )

    catalog_refresh.request_target_catalog("C:/books/A")
    deadline = time.time() + 2
    completed_at = 0.0
    while time.time() < deadline and not completed_at:
        _, completed_at = catalog_refresh.request_target_catalog("C:/books/A")
        time.sleep(0.01)

    assert completed_at > 0
    assert (
        catalog_refresh.target_refresh_error("C:/books/A", newer_than=completed_at - 1)
        == "ERR_TARGET_CATALOG_SCAN_FAILED"
    )


def test_target_scan_reads_the_full_catalog_for_stock_checks(monkeypatch):
    """推到非默认账套也要做库存检查(用户 2026-09-27):按单个账套刷新带回记账指纹与存货科目组。"""
    full = {"products": [{"code": "P1"}], "customers": [],
            "fingerprint": {"stock_master_count": 3}, "isacc_candidates": [{"code": "01"}]}
    monkeypatch.setattr(catalog_refresh, "catalog_payload", lambda _path: full)
    monkeypatch.setattr(
        account_probe,
        "chart_accounts_payload",
        lambda _path: [{"code": "1100"}],
    )

    catalog_refresh.request_target_catalog("C:/books/A")
    deadline = time.time() + 2
    result = (None, 0.0)
    while time.time() < deadline:
        result = catalog_refresh.request_target_catalog("C:/books/A")
        if result[0] is not None:
            break
        time.sleep(0.01)

    assert result[0] == {
        "catalog": full,
        "accounts": [{"code": "1100"}],
    }
