"""丢在电脑手里的单重发（ADR-021 11-02 修订）：CC 被结束 / 重启后，Cowork 把同一条推送记录重发给原来那台，投递号（``log_id``）
每次领都是新的，服务端另给一个每次都一样的不透明键 ``task_key``。本机记账（``task_journal``）按它认「写过没有」：写过的照上次
回执回报、不写第二张；旧服务端不给这一键时照旧按投递号记。"""
from __future__ import annotations

from types import SimpleNamespace

from companion import task_journal, worker
from companion.queue_client import QueueClient, QueueClientConfig


class _Queue:
    def __init__(self):
        self.acks = []

    def ack_success(self, task_id, extracted_invoice_no=None, line_modes=None, meta=None):
        self.acks.append(("ok", task_id, extracted_invoice_no, meta))
        return True

    def ack_failure(self, task_id, error, error_code=None, outcome=None, meta=None):
        self.acks.append(("failed", task_id, error_code, outcome))
        return True


def test_the_journal_key_is_the_stable_key_and_the_delivery_id_without_one():
    assert task_journal.key_of(SimpleNamespace(id="d-1", key="k-1")) == "k-1"
    assert task_journal.key_of(SimpleNamespace(id="d-1", key=None)) == "d-1"
    assert task_journal.key_of(SimpleNamespace(id="d-1")) == "d-1"  # 旧调用方的任务对象没有 key


def test_a_redelivered_task_with_a_new_delivery_id_is_not_written_again(monkeypatch):
    task_journal.remember("k-1", {"docnum": "RR691102-001", "meta": {"written": True}})

    def must_not_look_up(cfg, payload):
        raise AssertionError("a task written here must be answered from the journal, not written again")

    monkeypatch.setattr(worker, "_task_settings", must_not_look_up)
    queue = _Queue()
    again = SimpleNamespace(id="d-2", key="k-1", payload={"direction": "purchase"}, invoice_no="INV")
    worker._handle_dbf_task(SimpleNamespace(default_depcod="", ui_lang="en"), queue, again)
    status, task_id, docnum, meta = queue.acks[-1]
    assert (status, task_id, docnum) == ("ok", "d-2", "RR691102-001")  # 回执按这次的投递号
    assert meta["duplicate_skipped"] is True and meta["written"] is False


class _Response:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": self.body}


class _Client:
    def __init__(self, jobs):
        self.jobs = jobs

    def post(self, path, json=None):
        return _Response({"ok": True, "lease_seconds": 120, "jobs": self.jobs})

    def close(self):
        return None


def _poll(monkeypatch, job):
    import companion.queue_client as module

    monkeypatch.setattr(module.httpx, "Client", lambda *a, **kw: _Client([job]))
    client = QueueClient(QueueClientConfig(backend_url="https://lab.invalid", bearer_token="exp_x", companion_id="c"))
    return client.poll()


def test_poll_reads_the_stable_key_and_an_old_server_without_it_leaves_none(monkeypatch):
    base = {"log_id": "1b4e28ba-2fa1-11d2-883f-0016d3cca427", "history_id": "H", "invoice_no": "INV", "payload": {}}
    assert _poll(monkeypatch, {**base, "task_key": "7c9e6679-7425-40de-944b-e07fc1f90ae7"}).key == \
        "7c9e6679-7425-40de-944b-e07fc1f90ae7"
    assert _poll(monkeypatch, base).key is None
    assert _poll(monkeypatch, {**base, "task_key": 12}).key is None


def test_poll_reads_the_conversation_a_task_was_pushed_from_and_an_old_server_without_it_leaves_none(monkeypatch):
    # 1.12.23 ⑨：一批没录成的通知点开回到推这批的那段对话
    base = {"log_id": "1b4e28ba-2fa1-11d2-883f-0016d3cca427", "history_id": "H", "invoice_no": "INV", "payload": {}}
    assert _poll(monkeypatch, {**base, "session": "sess-0001"}).session == "sess-0001"
    assert _poll(monkeypatch, base).session is None
    assert _poll(monkeypatch, {**base, "session": " "}).session is None
    assert _poll(monkeypatch, {**base, "session": 12}).session is None
