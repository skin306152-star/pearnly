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
    monkeypatch.setattr(account_lock, "_exclusive_error", _held_by_express("ISRUN"), raising=False)  # Express 开着单据窗口
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
    monkeypatch.setattr(account_lock, "_exclusive_error", lambda path: 0, raising=False)
    monkeypatch.setattr(account_lock, "is_account_busy", lambda account_dir, *a, **k: False)
    outcome, acks = _run(monkeypatch, datat, attended=False)
    assert outcome is None and acks[-1]["ok"] is True and acks[-1]["docnum"] == "RR581231-001"


# ---------------- 按本次要写的表探占用（B8 正版 18 种窗口实测，11-12） ----------------
#
# 正版 Express 停在主界面不占任何文件；除「科目表窗口」外的窗口都开着 ISRUN；科目表窗口开着 ARTRNRM、GLACC、GLBAL、ISTAB
# （共享笔记「共享写入实验-11-12/b8_matrix.txt」）。只试 ISRUN 会漏判「科目表窗口开着 + 单据带行说明（写 ARTRNRM）」，以及没测到的窗口。
# 改成：本次要写的每张表的 DBF 与 CDX 都独占试开一遍，任一打不开 = 占用；范围与写前备份 / 重建索引要独占的表一致。


def _held_by_express(*names):
    """假装 Express 只开着这几张表（DBF 与 CDX 都打不开，共享冲突 32）；别的文件都能独占打开。"""
    held = {name.upper() for name in names}

    def exclusive_error(path):
        return 32 if Path(path).stem.upper() in held else 0

    return exclusive_error


def _with_remarks(**over):
    from test_dbf_writer import _payload_items

    payload = _payload_items(**over)
    payload["items"][0]["remarks"] = ["เลขตัวถัง ABC123"]
    payload["payload_version"] = 17
    return payload


def test_a_table_the_chart_of_accounts_window_holds_stops_a_document_with_line_remarks(datat, monkeypatch):
    """科目表窗口开着（ARTRNRM 被占、ISRUN 没被占）而这张采购单带行说明：以前只试 ISRUN、判没开，照写到重建索引才发现；现在一个字节不动。"""
    monkeypatch.setattr(account_lock, "_exclusive_error", _held_by_express("ARTRNRM", "GLACC", "GLBAL", "ISTAB"), raising=False)
    monkeypatch.setattr(account_lock, "is_account_busy", lambda account_dir, *a, **k: False)  # 旧探测（只试 ISRUN）：没开
    monkeypatch.setattr(W, "_reindex", _express_saves_then_holds_the_tables)
    backups = []
    real_backup = W._backup
    monkeypatch.setattr(W, "_backup", lambda *a, **k: backups.append(a) or real_backup(*a, **k))
    monkeypatch.setattr(worker, "_task_settings", lambda cfg, payload: SimpleNamespace(
        account_dir=str(datat), account_set="DATAT", account_company="", has_selection=lambda: True))
    before = _snapshot(datat)
    queue = _Queue()
    task = SimpleNamespace(id="CWP-OPEN-2", payload={**_with_remarks(), "direction": "purchase"}, invoice_no="INV-9001",
                           attended=False)
    outcome = worker._handle_dbf_task(SimpleNamespace(default_depcod="", ui_lang="en"), queue, task)
    assert outcome == "waiting_lock"
    assert queue.acks == [{"ok": False, "error_code": W.ERR_WAITING_LOCK, "outcome": "waiting_lock"}]
    # 以前：照写 → 重建索引被挡 → 用写前备份整表覆盖回去（字节看着一样，但这期间 Express 里存的单被抹掉）。现在：没备份、没写
    assert backups == []
    assert _snapshot(datat) == before
    assert _rows(datat, "APTRN", DOCNUM=EXPRESS_DOC) == []  # 没走到重建索引那一步（替 Express 存单的桩没被调用）


def test_a_document_without_line_remarks_is_not_stopped_by_the_chart_of_accounts_window(datat, monkeypatch):
    """同一个窗口开着，但这张单不写 ARTRNRM：照写（不多拦）。"""
    monkeypatch.setattr(account_lock, "_exclusive_error", _held_by_express("ARTRNRM", "GLACC", "GLBAL", "ISTAB"))
    outcome, acks = _run(monkeypatch, datat, attended=False)
    assert outcome is None and acks[-1]["ok"] is True


def test_busy_tables_names_every_table_whose_dbf_or_cdx_cannot_be_opened(tmp_path, monkeypatch):
    for name in ("ISRUN.DBF", "ISRUN.CDX", "APTRN.DBF", "APTRN.CDX", "STLOC.DBF"):
        (tmp_path / name).write_bytes(b"x")
    calls = []

    def exclusive_error(path):
        calls.append(Path(path).name)
        return {"APTRN.CDX": 32, "STLOC.DBF": 33}.get(Path(path).name, 0)

    monkeypatch.setattr(account_lock, "_exclusive_error", exclusive_error)
    assert account_lock.busy_tables(tmp_path, ("ISRUN", "APTRN", "STLOC", "ARTRNRM")) == ["APTRN", "STLOC"]
    # 文件不存在的不试（ARTRNRM 没有、STLOC.CDX 没有）；APTRN 的 CDX 被占后不再试别的文件也无妨，这里逐个试
    assert "ARTRNRM.DBF" not in calls and "STLOC.CDX" not in calls
    monkeypatch.setattr(account_lock, "_exclusive_error", lambda path: 2)  # 别的错误（找不到等）不算占用
    assert account_lock.busy_tables(tmp_path, ("ISRUN", "APTRN")) == []
    assert account_lock.busy_tables("", ("ISRUN",)) == []


@pytest.mark.parametrize("direction, payload, own", [
    ("purchase", {"doctype": "RR"}, S.PURCHASE_BACKUP_TABLES),
    ("sales", {"doctype": "IV"}, S.SALES_BACKUP_TABLES),
    ("sales", {"doctype": "SR", "refund": {}}, S.SALES_BACKUP_TABLES),
    ("receipt", {}, None),
    ("stock", {"doctype": "OU"}, None),
    ("stock", {"doctype": "RL"}, None),
    ("stock", {"doctype": "OU", "action": "void"}, None),
])
def test_each_direction_probes_the_tables_it_backs_up(direction, payload, own):
    from companion import dbf_receipt, dbf_stock, dbf_transfer, dbf_void

    if own is None:
        own = {"receipt": dbf_receipt.BACKUP_TABLES}.get(direction) or (
            dbf_void.TABLES if payload.get("action") == "void" else dbf_transfer.TABLES if payload["doctype"] == "RL"
            else dbf_stock.TABLES)
    tables = worker._write_tables(direction, payload)
    assert set(own) <= set(tables), (direction, payload)
    if direction != "receipt":
        assert "STLOC" in tables
    # 行说明表只在这张单带了说明时写；作废照「原单可能有说明」带上（原单有没有要读账套才知道，只多试一张表），作废调拨不动它
    assert ("ARTRNRM" in tables) is (payload.get("action") == "void")
    assert "ARTRNRM" in worker._write_tables(direction, {**payload, "items": [{"remarks": ["x"]}]}) or direction == "receipt"
    assert "STMAS" in worker._write_tables("stock", {"doctype": "RL", "items": [{"new_ok": True}]})


def test_voiding_a_transfer_never_probes_the_line_remarks_table():
    assert "ARTRNRM" not in worker._write_tables("stock", {"doctype": "RL", "action": "void"})
