"""Companion 骨架测试:config / logger / queue_client / health_check / tray"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion.config import CompanionConfig  # noqa: E402
from companion.health_check import run_health_check  # noqa: E402
from companion.queue_client import QueueClient, QueueClientConfig  # noqa: E402
from companion.tray import TrayController, TrayState  # noqa: E402


# ── config ──────────────────────────────────────────────────
def test_config_default(tmp_path, monkeypatch):
    cfg_file = tmp_path / "companion.json"
    cfg = CompanionConfig.load(cfg_file)
    assert cfg.backend_url == "http://127.0.0.1:8765"
    assert cfg.poll_interval_ms == 3000
    assert cfg.debug_mode is False
    assert cfg.companion_id.startswith("comp-")
    assert cfg_file.exists()


def test_config_roundtrip(tmp_path):
    cfg_file = tmp_path / "companion.json"
    cfg = CompanionConfig.load(cfg_file)
    cfg.update(debug_mode=True, poll_interval_ms=500, dpi_simulation=1.5)
    cfg.save(cfg_file)
    reloaded = CompanionConfig.load(cfg_file)
    assert reloaded.debug_mode is True
    assert reloaded.poll_interval_ms == 500
    assert reloaded.dpi_simulation == 1.5


def test_config_ignores_unknown_keys(tmp_path):
    cfg_file = tmp_path / "companion.json"
    cfg_file.write_text(
        json.dumps(
            {
                "backend_url": "http://test",
                "unknown_key": "ignored",
                "another_alien": 42,
            }
        )
    )
    cfg = CompanionConfig.load(cfg_file)
    assert cfg.backend_url == "http://test"
    assert not hasattr(cfg, "unknown_key")


# ── tray ────────────────────────────────────────────────────
def test_tray_state_changes_propagate():
    t = TrayController()
    received: list[TrayState] = []
    t.on_state_change(lambda s: received.append(TrayState(**s.__dict__)))
    t.set_status("working", "field 3/5")
    assert t.state.status == "working"
    assert t.state.message == "field 3/5"
    assert len(received) == 1
    assert received[0].status == "working"


# ── queue_client (→ P1 Agent API: heartbeat/lease/ack) ──────
class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeP1Client:
    """最小 P1 Agent 后端(heartbeat/lease/ack)· 内存单条队列。"""

    def __init__(self, *a, **kw):
        self.headers = kw.get("headers", {})
        self.jobs = [
            {
                "log_id": "log-123",
                "history_id": "hist-9",
                "invoice_no": "INV-2015-001",
                "payload": {
                    "doctype": "RR",
                    "account_set": "DATAT",
                    "ref_no": "INV-2015-001",
                },
            }
        ]
        self.master_refresh = {
            "request_id": "refresh-1",
            "scope_kind": "endpoint",
            "account_set_key": "@endpoint",
        }
        self.acks: list[dict] = []

    def close(self):
        pass

    def post(self, url, json=None, **kw):
        # 新平台原生接口:地址是 /api/method/…,回应包在 {"message": …} 里。
        assert isinstance(json, dict), "native machine RPCs require a JSON object body"
        if url == "/api/method/pearnly_cowork.companion.heartbeat.report":
            return _FakeResp({"message":
                {"ok": True, "connected": True, "account_set": "DATAT", "method": "rpa"}
            })
        if url == "/api/method/pearnly_cowork.companion.queue_api.lease":
            n = (json or {}).get("max", 1)
            taken, self.jobs = self.jobs[:n], self.jobs[n:]
            command, self.master_refresh = self.master_refresh, None
            return _FakeResp({"message":
                {
                    "ok": True,
                    "lease_seconds": 120,
                    "jobs": taken,
                    "master_refresh": command,
                }
            })
        if url == "/api/method/pearnly_cowork.companion.queue_api.acknowledge":
            self.acks.append(json)
            return _FakeResp({"message": {"ok": True}})
        raise AssertionError(f"unexpected url: {url}")


def test_queue_client_p1_flow(monkeypatch):
    import companion.queue_client as qc_mod

    created: list[_FakeP1Client] = []
    monkeypatch.setattr(
        qc_mod.httpx,
        "Client",
        lambda *a, **kw: created.append(_FakeP1Client(*a, **kw)) or created[-1],
    )

    cfg = QueueClientConfig(
        backend_url="https://testcowork.pearnly.com",
        bearer_token="agent-tok",
        companion_id="comp-1",
    )
    qc = QueueClient(cfg)
    fake = created[0]
    assert fake.headers["Authorization"] == "Bearer agent-tok"

    # heartbeat
    h = qc.health()
    assert h["connected"] is True and h["account_set"] == "DATAT"

    # lease → IncomingTask(id=log_id, payload=job payload)
    task = qc.poll()
    assert task is not None
    assert task.id == "log-123"
    assert task.invoice_no == "INV-2015-001"
    assert task.payload["account_set"] == "DATAT"
    assert qc.take_master_refresh()["request_id"] == "refresh-1"
    assert qc.take_master_refresh() is None

    # 空队列 → None
    assert qc.poll() is None

    # ack success → result=success + express_docnum + agent_id
    assert qc.ack_success(task.id, extracted_invoice_no="RR581231-002") is True
    ok_ack = fake.acks[-1]
    assert ok_ack["log_id"] == "log-123"
    assert ok_ack["result"] == "success"
    assert ok_ack["express_docnum"] == "RR581231-002"
    assert ok_ack["agent_id"] == "comp-1"

    # ack failure → result=failed + error 含 error_code 前缀
    assert (
        qc.ack_failure(task.id, "window not found", error_code="EXPRESS_NOT_OPEN")
        is True
    )
    fail_ack = fake.acks[-1]
    assert fail_ack["result"] == "failed"
    assert "EXPRESS_NOT_OPEN" in fail_ack["error"]


# ── health_check ─────────────────────────────────────────────
def test_health_check_runs_without_crash(tmp_path):
    cfg_file = tmp_path / "companion.json"
    cfg = CompanionConfig.load(cfg_file)
    cfg.update(backend_url="http://127.0.0.1:9", bearer_token="x")
    cfg.save(cfg_file)
    rep = run_health_check(cfg)
    # 后端肯定 fail,但应该不崩,结构正确(RPA 自检项已随引擎移除,仅留后端连通)。
    assert hasattr(rep, "overall_ok")
    assert isinstance(rep.items, list)
    names = {i.name for i in rep.items}
    assert "backend" in names
