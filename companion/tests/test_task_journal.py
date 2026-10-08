# -*- coding: utf-8 -*-
"""同一个推送任务重发不写第二张;会计再推(新任务)照写 —— 不查重(用户 2026-09-30、2026-10-08)。"""

from types import SimpleNamespace

from companion import task_journal, worker


class _Queue:
    def __init__(self):
        self.acks = []

    def ack_success(self, task_id, extracted_invoice_no=None, line_modes=None, meta=None):
        self.acks.append(("success", task_id, extracted_invoice_no, meta))
        return True

    def ack_failure(self, task_id, error, error_code=None, outcome=None, meta=None):
        self.acks.append(("failed", task_id, error_code, meta))
        return True


def _run(monkeypatch, task_id, writes):
    from companion import dbf_writer
    from companion.write_result import DbfWriteResult

    def write_purchase(payload, account_dir, **kwargs):
        writes.append(task_id)
        return DbfWriteResult(True, docnum=f"RR-{len(writes)}")

    monkeypatch.setattr(dbf_writer, "write_purchase", write_purchase)
    monkeypatch.setattr(worker, "_task_settings", lambda cfg, payload: SimpleNamespace(
        account_dir="C:/acct", account_set="A", account_company="", has_selection=lambda: True))
    queue = _Queue()
    cfg = SimpleNamespace(default_depcod="", ui_lang="en")
    worker._handle_dbf_task(cfg, queue, SimpleNamespace(id=task_id, payload={"direction": "purchase"}, invoice_no="X"))
    return queue.acks[-1]


def test_redelivered_task_is_not_written_twice(monkeypatch):
    writes = []
    first = _run(monkeypatch, "CWP-1", writes)
    again = _run(monkeypatch, "CWP-1", writes)
    assert writes == ["CWP-1"]
    assert first[0] == again[0] == "success" and again[2] == first[2] == "RR-1"
    assert again[3]["written"] is False and again[3]["duplicate_skipped"] is True


def test_a_new_push_of_the_same_invoice_is_written(monkeypatch):
    writes = []
    _run(monkeypatch, "CWP-1", writes)
    second = _run(monkeypatch, "CWP-2", writes)
    assert writes == ["CWP-1", "CWP-2"] and second[2] == "RR-2"


def test_journal_keeps_only_the_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(task_journal, "KEEP", 3)
    for n in range(5):
        task_journal.remember(f"T{n}", {"docnum": str(n)})
    assert task_journal.written("T0") is None and task_journal.written("T4")["docnum"] == "4"


def test_unreadable_journal_means_write_as_usual(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    assert task_journal.written("T1", path) is None
