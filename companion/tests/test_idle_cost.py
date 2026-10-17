"""后台空闲时不白花 CPU（2026-10-18 轻量化，办公室测试机 1.7.0 实测数据见 docs/work/2026-10-15-multi-desktop-build.md）：

- 所有 HTTPS 客户端共用一份 SSL 上下文（http_tls.py）：Windows 上每新建一份要约 250 ms CPU，按需请求线程原来每 15 秒建一次；
- 按需请求线程空闲时一直用同一个客户端（连接保持），出错、被拒、停下时才关掉重建；
- 托盘图标、悬停提示、菜单状态行只在变了时才重画（取任务线程每 3 秒报一次「空闲」）。"""
import ast
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from companion import http_tls, request_worker
from companion.request_client import Refused

SRC = Path(__file__).resolve().parents[1] / "src" / "companion"


def test_one_ssl_context_is_shared_by_every_thread():
    seen = []
    threads = [threading.Thread(target=lambda: seen.append(http_tls.context())) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len({id(ctx) for ctx in seen + [http_tls.context()]}) == 1
    ctx = http_tls.context()
    assert ctx.verify_mode.name == "CERT_REQUIRED" and ctx.check_hostname is True   # 与 httpx 默认一样校验


def test_every_https_client_uses_the_shared_context():
    missing = []
    for path in sorted(SRC.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and ast.unparse(node.func) in ("httpx.Client", "httpx.AsyncClient"):
                verify = next((k.value for k in node.keywords if k.arg == "verify"), None)
                if verify is None or ast.unparse(verify) not in ("http_tls.context()", "tls_context()"):
                    missing.append(f"{path.name}:{node.lineno}")
    assert missing == []


def _cfg(token="exp_CONN1_s"):
    return SimpleNamespace(backend_url="https://a.pearnly.com", bearer_token=token, paused=False, paired=True)


class _Clients:
    def __init__(self, outcomes=()):
        self.made = []
        self.outcomes = list(outcomes)

    def __call__(self, url, token):
        client = SimpleNamespace(closed=0, calls=0)
        owner = self

        def next_(body, client=client):
            client.calls += 1
            if owner.outcomes:
                outcome = owner.outcomes.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
            return {"ok": True, "request": None}

        client.next = next_
        client.close = lambda client=client: setattr(client, "closed", client.closed + 1)
        self.made.append(client)
        return client


def test_idle_rounds_keep_one_client_open():
    clients = _Clients()
    worker = request_worker.RequestWorker(lambda: [_cfg()], client_factory=clients)
    for _ in range(5):
        assert worker.round(_cfg()) is False
    assert len(clients.made) == 1 and clients.made[0].calls == 5 and clients.made[0].closed == 0
    worker.close_clients()
    assert clients.made[0].closed == 1


def test_a_refusal_or_a_network_error_drops_the_client_and_the_next_round_starts_fresh():
    clients = _Clients([Refused("on-demand request refused: 401"), httpx.ConnectError("down")])
    worker = request_worker.RequestWorker(lambda: [_cfg()], client_factory=clients)
    with pytest.raises(Refused):
        worker.round(_cfg())
    with pytest.raises(httpx.ConnectError):
        worker.round(_cfg())
    assert worker.round(_cfg()) is False
    assert [c.closed for c in clients.made] == [1, 1, 0] and len(clients.made) == 3


def test_a_changed_key_gets_its_own_client_and_the_old_one_is_closed():
    clients = _Clients()
    worker = request_worker.RequestWorker(lambda: [_cfg()], client_factory=clients)
    worker.round(_cfg("exp_A"))
    worker.round(_cfg("exp_B"))   # 重新配对换了密钥：旧连接不再用
    worker.forget_others({request_worker._key(_cfg("exp_B"))})
    assert [c.closed for c in clients.made] == [1, 0]


def test_the_loop_closes_its_clients_when_it_stops():
    clients = _Clients()
    worker = request_worker.RequestWorker(lambda: [_cfg()], client_factory=clients, idle=0.01)
    worker.start()
    deadline = __import__("time").monotonic() + 5
    while not clients.made and __import__("time").monotonic() < deadline:
        __import__("time").sleep(0.01)
    worker.stop()
    worker._thread.join(5)
    assert clients.made and all(c.closed == 1 for c in clients.made)


def test_the_tray_redraws_its_icon_only_when_the_status_changes():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from src.companion.gui_tray import TrayApp

    QApplication.instance() or QApplication([])
    app = TrayApp.__new__(TrayApp)
    calls = []
    app.tray = SimpleNamespace(setIcon=lambda icon: calls.append("icon"), setToolTip=lambda text: calls.append(text))
    app._status_action = SimpleNamespace(setText=lambda text: calls.append("menu:" + text))
    app.configs = [SimpleNamespace(profile_id="p1", paused=False, device_state="", kind="express",
                                   pearnly_account="a@x")]
    app.lang = "zh"
    app._profile_states = {}
    app._last_keys = {}
    for _ in range(5):
        app._on_status("p1", "idle", "")
    assert calls.count("icon") == 1 and len([c for c in calls if c.startswith("menu:")]) == 1
    app._on_status("p1", "working", "INV-1")
    assert calls.count("icon") == 2
    app._on_status("p1", "working", "INV-2")   # 同一个图标、文字变了：只改文字
    assert calls.count("icon") == 2 and calls[-1] == "menu:" + calls[-1][5:] and "INV-2" in calls[-1]
