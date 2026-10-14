"""按需请求的后台线程（小助手侧）：领一件、按种类交给处理函数、分块交回结果（续传、失败原因），与推送线和看门狗无关。"""

import ast
import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from companion import request_worker
from companion.request_client import NEXT, COMPLETE, Refused
from companion.request_contract import RequestFailed, Result
from companion.version import VERSION


def _cfg():
    return SimpleNamespace(backend_url="https://a.pearnly.com", bearer_token="exp_CONN1_s", paused=False, paired=True)


class FakeClient:
    def __init__(self, request=None, replies=None):
        self.request, self.replies, self.calls = request, list(replies or []), []
        self.closed = 0

    def __call__(self, url, token):
        return self

    def close(self):
        self.closed += 1

    def next(self, body):
        self.calls.append(("next", body))
        return {"ok": True, "request": self.request}

    def complete(self, body):
        self.calls.append(("complete", body))
        if self.replies:
            reply = self.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        return {"ok": True}

    def completes(self):
        return [body for kind, body in self.calls if kind == "complete"]


def _request(kind="folder_fetch", **extra):
    return {"id": "a" * 32, "kind": kind, "params": {"root_id": "r1", "path": "a.pdf"}, "handle": "b" * 32,
            "lease_seconds": 600, "chunk_bytes": 4, "max_bytes": 1000, **extra}


def test_nothing_to_do_returns_false_and_declares_what_it_can_do():
    client = FakeClient()
    worker = request_worker.RequestWorker(lambda: [_cfg()], client_factory=client)
    assert worker.round(_cfg()) is False
    body = client.calls[0][1]
    assert body["version"] == VERSION and body["kinds"] == list(request_worker.HANDLERS)
    assert set(request_worker.HANDLERS) == {"express_read", "folder_list", "folder_fetch", "account_history"}
    assert client.closed == 1


def test_a_result_is_sent_in_chunks_with_size_and_digest_on_the_last():
    content = b"0123456789"
    client = FakeClient(_request())
    worker = request_worker.RequestWorker(lambda: [_cfg()], client_factory=client,
                                          handlers={"folder_fetch": lambda cfg, params: Result(content, {"name": "a.pdf"})})
    assert worker.round(_cfg()) is True
    sent = client.completes()
    assert [body["offset"] for body in sent] == [0, 4, 8]
    assert b"".join(base64.b64decode(body["content"]) for body in sent) == content
    assert [body["final"] for body in sent] == [False, False, True]
    assert sent[-1]["size"] == 10 and sent[-1]["sha256"] == hashlib.sha256(content).hexdigest()
    assert sent[-1]["meta"] == {"name": "a.pdf"} and "size" not in sent[0]
    assert all(body["request"] == "a" * 32 and body["handle"] == "b" * 32 and body["ok"] for body in sent)


def test_it_resumes_from_what_the_server_already_has():
    content = b"0123456789"
    client = FakeClient(_request(), replies=[{"ok": True}, {"ok": False, "code": "companion.on_demand_offset",
                                                            "received": 8}, {"ok": True}])
    worker = request_worker.RequestWorker(lambda: [_cfg()], client_factory=client,
                                          handlers={"folder_fetch": lambda cfg, params: Result(content)})
    worker.round(_cfg())
    assert [body["offset"] for body in client.completes()] == [0, 4, 8]
    assert client.completes()[-1]["final"] is True


def test_a_network_error_is_retried_then_given_up(monkeypatch):
    client = FakeClient(_request(), replies=[OSError("reset")] * 10)
    worker = request_worker.RequestWorker(lambda: [_cfg()], client_factory=client,
                                          handlers={"folder_fetch": lambda cfg, params: Result(b"abc")})
    monkeypatch.setattr(worker._stop, "wait", lambda seconds: False)
    worker.round(_cfg())
    assert len(client.completes()) == request_worker.RETRIES + 1


def test_refusals_unknown_kinds_errors_and_oversize_are_reported_as_codes():
    def refuse(cfg, params):
        raise RequestFailed("root_missing")

    def broken(cfg, params):
        raise ValueError("bug")

    cases = [({"folder_fetch": refuse}, _request(), "root_missing"),
             ({"folder_fetch": refuse}, _request(kind="express_read"), "unsupported_kind"),
             ({"folder_fetch": broken}, _request(), "failed"),
             ({"folder_fetch": lambda cfg, params: Result(b"x" * 1001)}, _request(), "too_large")]
    for handlers, request, code in cases:
        client = FakeClient(request)
        request_worker.RequestWorker(lambda: [_cfg()], client_factory=client, handlers=handlers).round(_cfg())
        assert client.completes() == [{"request": "a" * 32, "handle": "b" * 32, "ok": False, "code": code}], code


def test_a_refused_server_skips_the_round():
    class Old(FakeClient):
        def next(self, body):
            raise Refused("404")

    with pytest.raises(Refused):
        request_worker.RequestWorker(lambda: [_cfg()], client_factory=Old()).round(_cfg())


def test_a_server_without_these_methods_is_asked_again_only_after_a_while():
    # 服务端还没部署新版（401 / 404）或总闸关着：不每 15 秒问一次、刷一次日志
    asked = []

    class Old(FakeClient):
        def next(self, body):
            asked.append(body)
            raise Refused("404")

    now = [1000.0]
    worker = request_worker.RequestWorker(lambda: [_cfg()], client_factory=Old(), timer=lambda: now[0])
    waits = []

    def wait(seconds):
        waits.append(seconds)
        now[0] += seconds
        if len(waits) >= 3:
            worker._stop.set()
        return worker._stop.is_set()

    worker._stop.wait = wait
    worker._loop()
    assert len(asked) == 1 and len(waits) == 3  # 退避期内的两轮不再问
    now[0] += request_worker.REFUSED_BACKOFF
    worker._stop.clear()
    waits.clear()
    worker._loop()
    assert len(asked) == 2


def test_the_thread_only_starts_for_paired_connections_and_stays_off_the_push_line():
    worker = request_worker.RequestWorker(lambda: [SimpleNamespace(backend_url="", bearer_token="", paused=False)])
    worker.start()
    assert not worker.running()
    cfg = _cfg()
    cfg.paused = True
    assert request_worker.RequestWorker(lambda: [cfg])._active() == []
    # 不导入推送线、看门狗、账套锁：读得再慢也不挡推送、不被当成卡死
    tree = ast.parse(Path(request_worker.__file__).read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} | {
        alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
    assert not imported & {"watchdog", "worker", "gui_tray_workers", "account_lock", "multi_profile_worker",
                           "QueueClient", "PollWorker"}
    assert NEXT.endswith("request_api.next_request") and COMPLETE.endswith("request_api.complete_request")
