# -*- coding: utf-8 -*-
"""云端请读本机没选的那份账套（2026-10-13 生产录制：一台电脑四份账套 DATAZ / DEMO01 / DEMO02 / DEMO03，所选 DEMO01，
推往 DEMO02 的票拿不到科目表）。

- 读的是请求的那份账套自己的 DBF（科目表、供应商、部门……），心跳带着 `master_refresh_account_set` 交回；
  本机所选账套不变，所选账套每 30 分钟的例行上报不被推迟；
- 能读哪些与推送写入同一条规则：授权数据根下、可写；数据根没授权的不读；
- 按账套读完的结果只在内存里留最近几份。
"""

from __future__ import annotations

import sys
import time
import types
from pathlib import Path
from unittest import mock

import dbf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import _synth_account  # noqa: E402

from src.companion import account_probe, catalog_refresh, projection_refresh, worker  # noqa: E402
from src.companion import dbf_schema as S  # noqa: E402


def _add(account_dir: Path, table: str, **fields) -> None:
    t = dbf.Table(str(account_dir / f"{table}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        t.append(fields)
    finally:
        t.close()


def _book(root: Path, name: str, account: str, supplier: str) -> Path:
    book = _synth_account.build(root / name)
    _add(book, "GLACC", ACCNUM=account, ACCNAM=f"ค่าใช้จ่าย {name}", GROUP="5", ACCTYP="0", STATUS="A")
    _add(book, "APMAS", SUPCOD=supplier, SUPNAM=f"Supplier of {name}", TAXID="0105599210021")
    _add(book, "ISTAB", TABTYP="50", TYPCOD=name[-2:], TYPDES=f"Dept {name}")
    return book


class _QC:
    def __init__(self):
        self.health_calls = []

    def health(self, **kwargs):
        self.health_calls.append(kwargs)
        return {}


def _cfg(root: Path, selected: Path, authorized=()):
    return types.SimpleNamespace(
        heartbeat_interval_s=60, method="dbf", account_set=str(selected), account_dir=str(selected),
        account_company="DEMO01", account_set_row=1, revenue_acc="", ar_acc="", vat_output_acc="",
        fallback_acc="", ap_acc="", vat_input_acc="", resolved_express_root=lambda: str(root),
        authorized_roots=list(authorized), backend_url="https://x", profile_id="p")


def test_a_requested_account_set_other_than_the_selected_one_is_read_from_its_own_dbfs(tmp_path):
    catalog_refresh._reset_for_tests()
    root = tmp_path / "ACCOUNT" / "70EXP"
    demo01 = _book(root, "DEMO01", "1111-00", "S-DEMO01")
    demo02 = _book(root, "DEMO02", "5300-01", "S-DEMO02")
    cfg = _cfg(root, demo01)
    qc = _QC()
    last_catalog = time.time() - 60  # 所选账套一分钟前刚例行上报过
    state = {
        "last_heartbeat": 0.0, "last_catalog": last_catalog,
        "reported_account_sets": [{"path": str(book), "root": str(root), "writable": True}
                                  for book in (demo01, demo02)],
        "master_refresh_received_at": time.time() - 1,
        "master_refresh_request": {"request_id": "r-demo02", "scope_kind": "account_set",
                                   "account_set_key": str(demo02).lower()},
    }
    with mock.patch("src.companion.master_refresh.request_master", return_value=(None, 0.0)):
        deadline = time.time() + 5
        while time.time() < deadline:
            worker._run_periodic(cfg, qc, state, on_update_available=None)
            if qc.health_calls[-1].get("master_refresh_request_id"):
                break
            time.sleep(0.05)
    heartbeat = qc.health_calls[-1]
    assert heartbeat["master_refresh_request_id"] == "r-demo02"
    assert heartbeat["master_refresh_scope"] == "account_set"
    assert heartbeat["master_refresh_account_set"] == str(demo02).lower()
    codes = {row["code"] for row in heartbeat["accounts"]}
    assert "5300-01" in codes and "1111-00" not in codes  # DEMO02 自己的科目表
    assert [row["code"] for row in heartbeat["catalog"]["suppliers"]] == ["S-DEMO02"]
    assert {"code": "02", "name": "Dept DEMO02"} in heartbeat["catalog"]["departments"]
    # 只读：所选账套不变，所选账套的例行上报节奏不变
    assert heartbeat["selected"]["account_set"] == str(demo01)
    assert cfg.account_set == str(demo01)
    assert state["last_catalog"] == last_catalog
    assert "master_refresh_request" not in state


def test_an_account_set_outside_the_authorized_roots_is_not_read(tmp_path, monkeypatch):
    """与推送写入同一条规则：数据根没授权的账套，清单里有也不读（登录清单也只在授权的数据根里找）。"""
    probed = []
    monkeypatch.setattr(account_probe, "probe_account_sets", lambda root: probed.append(root) or [])
    started = []
    monkeypatch.setattr(catalog_refresh, "request_target_catalog",
                        lambda path, newer_than: started.append(path) or (None, 0.0))
    other = r"D:\OTHER\70EXP\SECRET"
    rows = [{"path": other, "root": r"D:\OTHER\70EXP", "writable": True}]
    state = {"master_refresh_received_at": time.time()}
    result = projection_refresh.collect_account(state, rows, other.lower(), time.time(),
                                                roots=[r"C:\ACCOUNT\70EXP"])
    assert result[3] == "ERR_ACCOUNT_SET_UNAVAILABLE"
    assert (started, probed) == ([], [r"C:\ACCOUNT\70EXP"])
    # 授权的数据根下、可写的照读；只读（不可写）的不读
    ok = projection_refresh.collect_account(state, rows, other.lower(), time.time(), roots=[r"d:\other\70exp\\"])
    assert ok[3] == "" and started == [other]
    rows[0]["writable"] = False
    assert projection_refresh.collect_account(state, rows, other.lower(), time.time(),
                                              roots=[r"D:\OTHER\70EXP"])[3] == "ERR_ACCOUNT_SET_UNAVAILABLE"


def test_only_the_latest_account_set_reads_stay_in_memory(monkeypatch):
    catalog_refresh._reset_for_tests()
    monkeypatch.setattr(catalog_refresh, "catalog_payload", lambda path: {"products": [{"code": path}]})
    monkeypatch.setattr(account_probe, "chart_accounts_payload", lambda path: [{"code": "1100"}])
    for index in range(catalog_refresh._TARGET_KEEP + 4):
        path = rf"C:\ACCOUNT\70EXP\BOOK{index}"
        catalog_refresh.request_target_catalog(path)
        deadline = time.time() + 2
        while time.time() < deadline and catalog_refresh.request_target_catalog(path)[0] is None:
            time.sleep(0.01)
    assert len(catalog_refresh._target_refreshes) <= catalog_refresh._TARGET_KEEP + 1
    newest = catalog_refresh._key(rf"C:\ACCOUNT\70EXP\BOOK{catalog_refresh._TARGET_KEEP + 3}")
    assert catalog_refresh._target_refreshes[newest].payload is not None


def test_a_refused_reply_keeps_the_read_and_the_sites_next_ok_without_it_clears_it(tmp_path):
    """11-06 测试机（服务端 refresh-stale-heartbeat）：读账套时断网休眠，醒来交的回执服务端认过期。旧服务端整份心跳回 417，
    这条请求在 CC 里一直留着、每拍重报；新服务端这一拍照收、回复里不再派它，CC 现有逻辑（交了回执 + 回复没有新请求）就清掉，
    下一拍不再带回执——已装着的 CC 桌面版不用升级也能自愈。"""
    catalog_refresh._reset_for_tests()
    root = tmp_path / "ACCOUNT" / "70EXP"
    demo01 = _book(root, "DEMO01", "1111-00", "S-DEMO01")
    demo02 = _book(root, "DEMO02", "5300-01", "S-DEMO02")
    cfg = _cfg(root, demo01)

    class Refused(Exception):
        response = types.SimpleNamespace(status_code=417)

    class _Site(_QC):
        refuse = True

        def health(self, **kwargs):
            self.health_calls.append(kwargs)
            if self.refuse and kwargs.get("master_refresh_request_id"):
                raise Refused("417 EXPECTATION FAILED")
            return {"ok": True}

    qc = _Site()
    state = {
        "last_heartbeat": 0.0, "last_catalog": time.time() - 60,
        "reported_account_sets": [{"path": str(book), "root": str(root), "writable": True} for book in (demo01, demo02)],
        "master_refresh_received_at": time.time() - 1,
        "master_refresh_request": {"request_id": "r-stale", "scope_kind": "account_set",
                                   "account_set_key": str(demo02).lower()},
    }
    with mock.patch("src.companion.master_refresh.request_master", return_value=(None, 0.0)):
        deadline = time.time() + 5
        while time.time() < deadline and not any(c.get("master_refresh_request_id") for c in qc.health_calls):
            worker._run_periodic(cfg, qc, state, on_update_available=None)
            time.sleep(0.05)
        assert qc.health_calls[-1]["master_refresh_request_id"] == "r-stale"
        assert state["master_refresh_request"]["request_id"] == "r-stale"  # 旧服务端：拒收，留着
        qc.refuse = False
        worker._run_periodic(cfg, qc, state, on_update_available=None)  # 有请求时每拍立即重报
        assert qc.health_calls[-1]["master_refresh_request_id"] == "r-stale"
        assert "master_refresh_request" not in state  # 新服务端：照收、没再派 → 清掉
        state["last_heartbeat"] = 0.0
        worker._run_periodic(cfg, qc, state, on_update_available=None)
        assert not qc.health_calls[-1].get("master_refresh_request_id")
