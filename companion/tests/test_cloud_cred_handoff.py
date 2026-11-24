"""云端代录的 Express 登录凭据交接（``cloud_link.cred_handoff`` / ``cred_cloud``，做法页 11-16 第二版），离线、真用 cryptography。

守住：正常往返；篡改密文 / nonce / 桥公钥 / 云端公钥都拒绝；桥侧与云端侧的重放都拒绝；过期拒绝；设备密钥不对 = unpaired；会话号
不对拒绝；桥本机没授权就不回应、不写文件；ctl 里任何时候都没有明文用户名 / 密码；日志里没有用户名、密码；撤销往返；读后删除并
回读；宿主（host.py）在中转循环里回应、兜底删、补发撤销；桥动作（tomo_bridge）只经原生小窗收密码、回包不含密码。"""
from __future__ import annotations

import base64
import json
import logging
import time

import pytest

pytest.importorskip("cryptography")

from companion.cloud_link import cred_cloud  # noqa: E402
from companion.cloud_link import cred_handoff as handoff  # noqa: E402
from companion.cloud_link import tunnel_protocol as protocol  # noqa: E402

DEVICE_KEY = "dk_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t"
OTHER_KEY = "dk_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2H1g"
EPOCH = "0123456789abcdef0123456789abcdef"
OTHER_EPOCH = "fedcba9876543210fedcba9876543210"
USER = "EXPRESS-USER-77"
PASSWORD = "S3cret-Pass!word#9"  # scan: fake
NOW = 1_800_000_000.0
CONNECTION = "default"


def _authorized(connection):
    return (USER, PASSWORD) if connection == CONNECTION else None


def _secret_free(data: bytes) -> bool:
    lowered = data.lower()
    for secret in (USER, PASSWORD):
        for encoded in (secret.encode("utf-8"), secret.encode("utf-16-le"), base64.b64encode(secret.encode())):
            if encoded.lower() in lowered:
                return False
    return True


def _round(now=NOW, device_key=DEVICE_KEY, epoch=EPOCH, bridge_key=DEVICE_KEY, bridge_epoch=EPOCH, seen=None,
           read=_authorized):
    pending, request = handoff.make_request(device_key, epoch, CONNECTION, now)
    result = handoff.answer(request, bridge_key, bridge_epoch, now, seen if seen is not None else set(), read)
    return pending, request, result


def _mutate(data: bytes, **changes) -> bytes:
    head = json.loads(data)
    head.update(changes)
    return json.dumps(head).encode()


def _flip_b64(value: str) -> str:
    raw = bytearray(base64.b64decode(value))
    raw[0] ^= 0x01
    return base64.b64encode(bytes(raw)).decode()


# ── 协议 ────────────────────────────────────────────────────────────────────

def test_round_trip_gives_back_the_credentials_and_files_hold_no_plaintext():
    pending, request, result = _round()
    assert result.code == handoff.OK and result.connection == CONNECTION
    assert _secret_free(request) and _secret_free(result.response)
    head = json.loads(result.response)
    assert set(head) == {"v", "id", "epoch", "bridge_pub", "nonce", "ct", "expires", "tag"}
    assert set(json.loads(request)) == {"v", "id", "epoch", "connection", "cloud_pub", "expires", "tag"}
    assert len(base64.b64decode(head["nonce"])) == 12
    assert json.loads(request)["expires"] == int(NOW) + 120
    assert handoff.open_response(result.response, pending, DEVICE_KEY, NOW + 1) == (USER, PASSWORD)
    assert PASSWORD not in repr(pending) and "private_key" not in repr(pending)
    assert "response" not in repr(result)


def test_each_request_uses_fresh_ids_keys_and_nonces():
    first, second = _round(), _round()
    assert first[0].id != second[0].id and first[0].cloud_pub != second[0].cloud_pub
    one, two = json.loads(first[2].response), json.loads(second[2].response)
    assert one["bridge_pub"] != two["bridge_pub"] and one["nonce"] != two["nonce"]


@pytest.mark.parametrize("field", ["ct", "nonce"])
def test_tampered_ciphertext_or_nonce_is_refused(field):
    pending, _, result = _round()
    head = json.loads(result.response)
    bad = _mutate(result.response, **{field: _flip_b64(head[field])})
    with pytest.raises(handoff.Refused) as caught:
        handoff.open_response(bad, pending, DEVICE_KEY, NOW)
    assert caught.value.code == handoff.TAMPERED


def test_swapped_bridge_public_key_is_refused():
    pending, _, result = _round()
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    other = X25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw,
                                                                  serialization.PublicFormat.Raw)
    bad = _mutate(result.response, bridge_pub=base64.b64encode(other).decode())
    with pytest.raises(handoff.Refused) as caught:
        handoff.open_response(bad, pending, DEVICE_KEY, NOW)
    assert caught.value.code == handoff.UNPAIRED


def test_swapped_cloud_public_key_is_refused_and_not_answered():
    _, request, _ = _round()
    head = json.loads(request)
    bad = _mutate(request, cloud_pub=_flip_b64(head["cloud_pub"]))
    reads = []
    result = handoff.answer(bad, DEVICE_KEY, EPOCH, NOW, set(), lambda c: reads.append(c) or (USER, PASSWORD))
    assert result.code == handoff.UNPAIRED and result.response is None and reads == []


def test_bridge_refuses_a_replayed_request():
    seen = set()
    pending, request = handoff.make_request(DEVICE_KEY, EPOCH, CONNECTION, NOW)
    assert handoff.answer(request, DEVICE_KEY, EPOCH, NOW, seen, _authorized).code == handoff.OK
    again = handoff.answer(request, DEVICE_KEY, EPOCH, NOW + 1, seen, _authorized)
    assert again.code == handoff.REPLAYED and again.response is None


def test_cloud_refuses_a_replayed_or_foreign_response():
    seen = set()
    pending, _, result = _round()
    assert handoff.open_response(result.response, pending, DEVICE_KEY, NOW, seen) == (USER, PASSWORD)
    with pytest.raises(handoff.Refused) as caught:
        handoff.open_response(result.response, pending, DEVICE_KEY, NOW, seen)
    assert caught.value.code == handoff.REPLAYED
    other_pending, _, _ = _round()
    with pytest.raises(handoff.Refused) as caught:
        handoff.open_response(result.response, other_pending, DEVICE_KEY, NOW)
    assert caught.value.code == handoff.REPLAYED


def test_expired_request_and_response_are_refused():
    pending, request = handoff.make_request(DEVICE_KEY, EPOCH, CONNECTION, NOW)
    late = handoff.answer(request, DEVICE_KEY, EPOCH, NOW + 121, set(), _authorized)
    assert late.code == handoff.EXPIRED and late.response is None
    pending, _, result = _round()
    with pytest.raises(handoff.Refused) as caught:
        handoff.open_response(result.response, pending, DEVICE_KEY, NOW + 121)
    assert caught.value.code == handoff.EXPIRED


def test_far_future_expiry_is_malformed():
    _, request = handoff.make_request(DEVICE_KEY, EPOCH, CONNECTION, NOW + 3600)
    assert handoff.answer(request, DEVICE_KEY, EPOCH, NOW, set(), _authorized).code == handoff.MALFORMED


def test_wrong_device_key_is_unpaired_on_both_sides():
    _, _, result = _round(device_key=OTHER_KEY)
    assert result.code == handoff.UNPAIRED and result.response is None
    pending, _, result = _round()
    with pytest.raises(handoff.Refused) as caught:
        handoff.open_response(result.response, pending, OTHER_KEY, NOW)
    assert caught.value.code == handoff.UNPAIRED


def test_wrong_epoch_is_refused():
    _, _, result = _round(bridge_epoch=OTHER_EPOCH)
    assert result.code == handoff.WRONG_EPOCH and result.response is None


def test_not_authorized_gets_no_answer():
    reads = []
    _, _, result = _round(read=lambda connection: reads.append(connection) or None)
    assert result.code == handoff.NOT_AUTHORIZED and result.response is None and reads == [CONNECTION]
    _, _, failing = _round(read=lambda connection: (_ for _ in ()).throw(OSError(1168, "CredReadW")))
    assert failing.code == handoff.NOT_AUTHORIZED and failing.response is None


@pytest.mark.parametrize("data", [b"", b"not json", b"[]", b'{"v": 2}', b"x" * 20000,
                                  json.dumps({"v": 1, "id": "zz", "epoch": EPOCH}).encode()])
def test_malformed_requests_are_refused(data):
    assert handoff.answer(data, DEVICE_KEY, EPOCH, NOW, set(), _authorized).code == handoff.MALFORMED


def test_bad_connection_names_are_refused():
    for bad in ("", "a/b", "x" * 141, "a b"):
        with pytest.raises(handoff.Refused):
            handoff.make_request(DEVICE_KEY, EPOCH, bad, NOW)
    assert handoff.bridge_target("198.51.100.7", "c-1") == "Pearnly CC/cloud-express/198.51.100.7/c-1"


def test_forget_round_trip_and_its_refusals():
    pending, data = handoff.make_forget(DEVICE_KEY, EPOCH, "c-1", NOW)
    assert set(json.loads(data)) == {"v", "id", "epoch", "connection", "expires", "tag"}
    removed, seen = [], set()
    code, connection, ack = handoff.answer_forget(data, DEVICE_KEY, EPOCH, NOW, seen, removed.append)
    assert (code, connection, removed) == (handoff.OK, "c-1", ["c-1"])
    assert handoff.open_forget_ack(ack, pending, DEVICE_KEY) == handoff.OK
    assert handoff.open_forget_ack(ack, pending, OTHER_KEY) == handoff.UNPAIRED
    assert handoff.answer_forget(data, DEVICE_KEY, EPOCH, NOW, seen, removed.append)[0] == handoff.REPLAYED
    assert handoff.answer_forget(data, OTHER_KEY, EPOCH, NOW, set(), removed.append)[0] == handoff.UNPAIRED
    assert handoff.answer_forget(data, DEVICE_KEY, OTHER_EPOCH, NOW, set(), removed.append)[0] == handoff.WRONG_EPOCH
    assert handoff.answer_forget(data, DEVICE_KEY, EPOCH, NOW + 121, set(), removed.append)[0] == handoff.EXPIRED
    bad = _mutate(data, connection="c-2")
    assert handoff.answer_forget(bad, DEVICE_KEY, EPOCH, NOW, set(), removed.append)[0] == handoff.UNPAIRED
    assert removed == ["c-1"]


def test_take_reads_then_deletes_and_confirms(tmp_path):
    path = tmp_path / "ctl" / handoff.REQUEST_FILE
    handoff.write_atomic(path, b"payload")
    assert not path.with_name(path.name + ".part").exists()
    assert handoff.take(path) == b"payload"
    assert not path.exists()
    assert handoff.take(path) is None
    (tmp_path / "ctl" / (handoff.RESPONSE_FILE + ".part")).write_bytes(b"x")
    assert handoff.sweep(tmp_path / "ctl") is True
    assert list((tmp_path / "ctl").iterdir()) == []


def test_sweeper_runs_only_when_due(tmp_path):
    sweeper = handoff.Sweeper()
    (tmp_path / handoff.RESPONSE_FILE).write_bytes(b"x")
    sweeper.schedule(10.0, (handoff.RESPONSE_FILE,))
    assert sweeper.run_due(tmp_path, 9.9) == 0 and (tmp_path / handoff.RESPONSE_FILE).exists()
    assert sweeper.run_due(tmp_path, 10.0) == 1 and not (tmp_path / handoff.RESPONSE_FILE).exists()
    assert sweeper.pending == 0


def test_logs_never_contain_user_password_or_keys(caplog):
    caplog.set_level(logging.DEBUG)
    pending, request, result = _round()
    handoff.open_response(result.response, pending, DEVICE_KEY, NOW)
    _round(device_key=OTHER_KEY)
    _round(read=lambda c: None)
    pending2, _, result2 = _round()
    bad = _mutate(result2.response, ct=_flip_b64(json.loads(result2.response)["ct"]))
    with pytest.raises(handoff.Refused):
        handoff.open_response(bad, pending2, DEVICE_KEY, NOW)
    text = caplog.text
    head = json.loads(result.response)
    for secret in (USER, PASSWORD, DEVICE_KEY, head["ct"], head["bridge_pub"], json.loads(request)["cloud_pub"]):
        assert secret not in text
    assert "事件=answer" in text and "原因=unpaired" in text and "原因=tampered" in text


# ── 云端侧（cred_cloud） ──────────────────────────────────────────────────────

def test_cloud_fetch_stores_through_the_bridge_and_leaves_ctl_empty(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    ctl = tmp_path / "ctl"
    stored, snapshots, bridge_seen = [], [], set()

    def bridge_turn(_seconds):
        for path in ctl.iterdir():
            snapshots.append(path.read_bytes())
        data = handoff.take(ctl / handoff.REQUEST_FILE)
        if data is not None:
            result = handoff.answer(data, DEVICE_KEY, EPOCH, NOW, bridge_seen, _authorized)
            handoff.write_atomic(ctl / handoff.RESPONSE_FILE, result.response)
            snapshots.append((ctl / handoff.RESPONSE_FILE).read_bytes())

    code = cred_cloud.fetch(ctl, device_key=DEVICE_KEY, epoch=EPOCH, connection=CONNECTION, now=lambda: NOW,
                            sleep=bridge_turn, store=lambda c, u, p: stored.append((c, u, p)))
    assert code == handoff.OK
    assert stored == [(CONNECTION, USER, PASSWORD)]
    assert snapshots and all(_secret_free(item) for item in snapshots)
    assert list(ctl.iterdir()) == []
    assert USER not in caplog.text and PASSWORD not in caplog.text


def test_cloud_fetch_times_out_when_the_bridge_is_not_authorized(tmp_path):
    ctl = tmp_path / "ctl"

    def bridge_turn(_seconds):
        data = handoff.take(ctl / handoff.REQUEST_FILE)
        if data is not None:
            assert handoff.answer(data, DEVICE_KEY, EPOCH, NOW, set(), lambda c: None).response is None

    stored = []
    code = cred_cloud.fetch(ctl, device_key=DEVICE_KEY, epoch=EPOCH, connection=CONNECTION, now=lambda: NOW,
                            timeout=0.05, poll=0.01, sleep=lambda s: (bridge_turn(s), time.sleep(s)),
                            store=lambda *a: stored.append(a))
    assert code == cred_cloud.TIMEOUT and stored == [] and list(ctl.iterdir()) == []


def test_cloud_fetch_reports_a_tampered_response(tmp_path):
    ctl = tmp_path / "ctl"

    def bridge_turn(_seconds):
        data = handoff.take(ctl / handoff.REQUEST_FILE)
        if data is not None:
            response = handoff.answer(data, DEVICE_KEY, EPOCH, NOW, set(), _authorized).response
            head = json.loads(response)
            handoff.write_atomic(ctl / handoff.RESPONSE_FILE, _mutate(response, ct=_flip_b64(head["ct"])))

    stored = []
    code = cred_cloud.fetch(ctl, device_key=DEVICE_KEY, epoch=EPOCH, connection=CONNECTION, now=lambda: NOW,
                            sleep=bridge_turn, store=lambda *a: stored.append(a))
    assert code == handoff.TAMPERED and stored == [] and list(ctl.iterdir()) == []


def test_cloud_handles_a_forget_and_writes_an_ack(tmp_path):
    ctl = tmp_path / "ctl"
    assert cred_cloud.handle_forget(ctl, device_key=DEVICE_KEY, epoch=EPOCH, seen_ids=set()) is None
    pending, data = handoff.make_forget(DEVICE_KEY, EPOCH, "c-1", NOW)
    handoff.write_atomic(ctl / handoff.FORGET_FILE, data)
    removed = []
    code = cred_cloud.handle_forget(ctl, device_key=DEVICE_KEY, epoch=EPOCH, seen_ids=set(), now=lambda: NOW,
                                    forget=removed.append)
    assert code == handoff.OK and removed == ["c-1"] and not (ctl / handoff.FORGET_FILE).exists()
    assert handoff.open_forget_ack(handoff.take(ctl / handoff.FORGET_ACK_FILE), pending, DEVICE_KEY) == handoff.OK


# ── 桥侧宿主（host.py） ──────────────────────────────────────────────────────

def _cloud_host(tmp_path, *, authorized=True, wall=None, clock=None):
    from test_cloud_host import FakeDrives, _host, _saved  # noqa: F401 - 复用宿主测试的假件

    _saved(tmp_path)
    host, *_ = _host(tmp_path)
    host._read_credential = lambda h, c: (USER, PASSWORD) if authorized and c == CONNECTION else None
    if wall is not None:
        host._wall = wall
    if clock is not None:
        host._clock = clock
    return host


def _device_key(tmp_path):
    from test_cloud_host import _load, HOST

    return _load(tmp_path).device_key(HOST)


def test_host_answers_a_cloud_request_and_sweeps_later(tmp_path):
    ticks = {"mono": 100.0}
    host = _cloud_host(tmp_path, wall=lambda: NOW, clock=lambda: ticks["mono"])
    host.start()
    key, epoch = _device_key(tmp_path), host._session.epoch
    pending, request = handoff.make_request(key, epoch, CONNECTION, NOW)
    handoff.write_atomic(host.ctl_dir / handoff.REQUEST_FILE, request)
    host.pump()
    response_path = host.ctl_dir / handoff.RESPONSE_FILE
    assert not (host.ctl_dir / handoff.REQUEST_FILE).exists()
    assert _secret_free(response_path.read_bytes())
    assert handoff.open_response(response_path.read_bytes(), pending, key, NOW) == (USER, PASSWORD)
    # 云端没删：10 秒后桥兜底删
    ticks["mono"] += 9
    host.pump()
    assert response_path.exists()
    ticks["mono"] += 2
    host.pump()
    assert not response_path.exists()
    # 同一份请求再来一次：重放，不回应
    handoff.write_atomic(host.ctl_dir / handoff.REQUEST_FILE, request)
    host.pump()
    assert not response_path.exists()
    host.stop()


def test_host_does_not_answer_without_authorization_or_with_a_stale_epoch(tmp_path):
    host = _cloud_host(tmp_path, authorized=False, wall=lambda: NOW)
    host.start()
    key = _device_key(tmp_path)
    _, request = handoff.make_request(key, host._session.epoch, CONNECTION, NOW)
    handoff.write_atomic(host.ctl_dir / handoff.REQUEST_FILE, request)
    host.pump()
    assert not (host.ctl_dir / handoff.RESPONSE_FILE).exists()
    host._read_credential = lambda h, c: (USER, PASSWORD)
    _, stale = handoff.make_request(key, OTHER_EPOCH, CONNECTION, NOW)
    handoff.write_atomic(host.ctl_dir / handoff.REQUEST_FILE, stale)
    host.pump()
    assert not (host.ctl_dir / handoff.RESPONSE_FILE).exists()
    host.stop()


def test_host_forget_is_sent_acked_and_resent_after_a_new_session(tmp_path):
    host = _cloud_host(tmp_path, wall=lambda: NOW)
    host.request_forget("c-1")   # 宿主没起：先记着
    assert host.pending_forgets == ("c-1",)
    host.start()
    key = _device_key(tmp_path)
    forget_path = host.ctl_dir / handoff.FORGET_FILE
    first_epoch = host._session.epoch
    assert forget_path.exists()   # 起宿主换会话时就补发了
    # 云端没取走就换会话：按新会话号补发
    handoff.remove_confirmed(forget_path)
    host._rotate()
    assert host._session.epoch != first_epoch
    host.pump()
    data = forget_path.read_bytes()
    assert json.loads(data)["epoch"] == host._session.epoch
    removed = []
    code = cred_cloud.handle_forget(host.ctl_dir, device_key=key, epoch=host._session.epoch, seen_ids=set(),
                                    now=lambda: NOW, forget=removed.append)
    assert code == handoff.OK and removed == ["c-1"]
    host.pump()
    assert host.pending_forgets == () and not (host.ctl_dir / handoff.FORGET_ACK_FILE).exists()
    host.stop()


def test_host_stop_removes_handoff_files(tmp_path):
    host = _cloud_host(tmp_path, wall=lambda: NOW)
    host.start()
    for name in handoff.FILES:
        (host.ctl_dir / name).write_bytes(b"x")
    host.stop()
    assert not any((host.ctl_dir / name).exists() for name in handoff.FILES)


# ── 桥动作（tomo_bridge） ────────────────────────────────────────────────────

def _bridge(tmp_path, holder):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from test_cloud_host import _load

    from companion.tomo_bridge import TomoBridge

    class _Settings:
        def snapshot(self):
            return {"members": [], "version": "x", "device": {"name": "OFFICE-PC"}}

    QApplication.instance() or QApplication([])
    vault = holder.setdefault("vault", {})
    return TomoBridge(_Settings(), allowed=lambda: True, window=lambda: None, set_lang=lambda lang: None,
                      cloud_links=lambda: _load(tmp_path) if (tmp_path / "cloud_link.json").exists() else None,
                      ask_login=lambda lang, title_key, user: holder.setdefault("asked", []).append((lang, title_key, user)) or holder.get("login"),
                      cloud_cred_store=lambda target, user, password: vault.__setitem__(target, (user, password)),
                      cloud_cred_forget=lambda target: vault.pop(target, None) is not None)


def test_bridge_authorize_stores_only_locally_and_never_returns_the_password(tmp_path, caplog):
    from test_cloud_host import HOST, _saved

    caplog.set_level(logging.DEBUG)
    holder = {"login": (USER, PASSWORD)}
    bridge = _bridge(tmp_path, holder)
    assert json.loads(bridge.call("cloud_operator_authorize", json.dumps({"connection": "c-1"}))) == \
        {"ok": False, "code": "not_found"}
    _saved(tmp_path)
    reply = bridge.call("cloud_operator_authorize",
                        json.dumps({"connection": "c-1", "lang": "th", "password": "typed-in-page"}))
    assert json.loads(reply) == {"ok": True}
    assert PASSWORD not in reply and USER not in reply
    assert holder["vault"] == {f"Pearnly CC/cloud-express/{HOST}/c-1": (USER, PASSWORD)}
    assert holder["asked"][-1] == ("th", "op_login_title", "")
    assert PASSWORD not in caplog.text and USER not in caplog.text
    assert json.loads(bridge.call("cloud_operator_authorize", json.dumps({"connection": "a/b"}))) == \
        {"ok": False, "code": "invalid"}
    holder["login"] = None
    assert json.loads(bridge.call("cloud_operator_authorize", json.dumps({"connection": "c-2"}))) == \
        {"ok": False, "cancelled": True}


def test_bridge_forget_removes_locally_and_queues_the_cloud_forget(tmp_path):
    from test_cloud_host import HOST, _saved

    holder = {"login": (USER, PASSWORD)}
    bridge = _bridge(tmp_path, holder)
    _saved(tmp_path)
    bridge.call("cloud_operator_authorize", json.dumps({"connection": "c-1"}))
    assert json.loads(bridge.call("cloud_operator_forget", json.dumps({"connection": "c-1"}))) == \
        {"ok": True, "removed": True}
    assert holder["vault"] == {}
    assert bridge._cloud_forgets == {"c-1"}   # 宿主没起：下次起宿主补发

    requested = []

    class _Host:
        host, running = HOST, True

        def request_forget(self, connection):
            requested.append(connection)

    bridge._cloud_host = _Host()
    assert json.loads(bridge.call("cloud_operator_forget", json.dumps({"connection": "c-2"}))) == \
        {"ok": True, "removed": False}
    assert requested == ["c-2"]
    bridge._cloud_host = None
