# -*- coding: utf-8 -*-
"""Express 开着这本账套时，直写一个字节都不动（2026-11-12）。

复现的事故路径：没人在电脑前的单（定时任务、自动推、LINE 来的单）以前不探占用，直接「整表备份 → 写 → 重建索引」，到重建索引
拿不到独占才发现 Express 开着，然后用写前备份**整表覆盖回去**——备份之后 Express 里的人刚保存的单随之被抹掉。

这里用合成账套模拟：``account_lock.is_account_busy`` 回 True（Express 开着，真机上是独占试开 ISRUN.DBF 被拒）；重建索引那一步先
替「Express 里的人」往 APTRN 追加一行，再照真机报占用（``ERR_WAITING_LOCK``）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import dbf
import pytest

from companion import account_lock, dbf_schema as S, dbf_writer as W, worker
from test_dbf_writer import _payload, _rows, datat  # noqa: F401  合成账套夹具与载荷

EXPRESS_DOC = "RR581231-777"   # Express 里的人在我们备份之后保存的那张单


class _Queue:
    def __init__(self):
        self.acks = []

    def ack_success(self, task_id, extracted_invoice_no=None, line_modes=None, meta=None):
        self.acks.append({"ok": True, "docnum": extracted_invoice_no})
        return True

    def ack_failure(self, task_id, error, error_code=None, outcome=None, meta=None):
        self.acks.append({"ok": False, "error_code": error_code, "outcome": outcome})
        return True


def _express_saves_then_holds_the_tables(account_dir, tables=()):
    """重建索引这一步：Express 里的人先存了一张单，随后我们的独占重建被 Express 挡住。"""
    table = dbf.Table(str(Path(account_dir) / "APTRN.DBF"), codepage=S.CODEPAGE)
    table.open(dbf.READ_WRITE)
    try:
        table.append({"DOCNUM": EXPRESS_DOC, "SUPCOD": "S001"})
    finally:
        table.close()
    raise W._WriteError("REINDEX APTRN 账套被占用", W.ERR_WAITING_LOCK)


def _snapshot(account_dir) -> dict:
    return {path.name: path.read_bytes() for path in sorted(Path(account_dir).iterdir()) if path.is_file()
            and path.name != "PEARNLY.LCK"}


def _run(monkeypatch, account_dir, *, attended, direction="purchase"):
    monkeypatch.setattr(worker, "_task_settings", lambda cfg, payload: SimpleNamespace(
        account_dir=str(account_dir), account_set="DATAT", account_company="", has_selection=lambda: True))
    queue = _Queue()
    cfg = SimpleNamespace(default_depcod="", ui_lang="en")
    task = SimpleNamespace(id="CWP-OPEN-1", payload={**_payload(), "direction": direction}, invoice_no="INV-9001",
                           attended=attended)
    outcome = worker._handle_dbf_task(cfg, queue, task)
    return outcome, queue.acks


@pytest.mark.parametrize("attended", [False, True])
def test_a_write_never_touches_an_account_set_that_express_has_open(datat, monkeypatch, attended):
    monkeypatch.setattr(account_lock, "is_account_busy", lambda account_dir, *a, **k: True)
    monkeypatch.setattr(W, "_reindex", _express_saves_then_holds_the_tables)
    backups = []
    real_backup = W._backup
    monkeypatch.setattr(W, "_backup", lambda *a, **k: backups.append(a) or real_backup(*a, **k))
    before = _snapshot(datat)

    outcome, acks = _run(monkeypatch, datat, attended=attended)

    assert outcome == "waiting_lock"
    assert acks == [{"ok": False, "error_code": W.ERR_WAITING_LOCK, "outcome": "waiting_lock"}]
    assert backups == []                     # 没备份
    assert _snapshot(datat) == before        # 账套一个字节没动（没写、没重建、没回滚）


def test_without_the_probe_the_rollback_wipes_what_express_saved_meanwhile(datat, monkeypatch):
    """记录风险本身（Express 在探测之后才打开时仍存在，留给「会话 + 差异回写」解决）：写前备份之后别人存的行，
    整表回滚会抹掉。这条固定现状，改回滚方式时要跟着改。"""
    monkeypatch.setattr(W, "_reindex", _express_saves_then_holds_the_tables)
    res = W.write_purchase(_payload(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=True)
    assert not res.ok and res.error_code == W.ERR_WAITING_LOCK
    assert _rows(datat, "APTRN", DOCNUM=EXPRESS_DOC) == []


def test_a_free_account_set_is_written_as_before(datat, monkeypatch):
    monkeypatch.setattr(account_lock, "is_account_busy", lambda account_dir, *a, **k: False)
    outcome, acks = _run(monkeypatch, datat, attended=False)
    assert outcome is None and acks[-1]["ok"] is True and acks[-1]["docnum"] == "RR581231-001"
