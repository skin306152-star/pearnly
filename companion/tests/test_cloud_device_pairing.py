"""设备密钥的一次性配对（``cloud_link.device_pairing``），离线、真用 cryptography。

守住：令牌格式（Crockford Base32、4 个一组、2 个校验字符）与容错；任意一个字符打错、相邻对调都被校验发现且不发请求；
正常往返（云端存进凭据管理器并读回、回执后桥为 paired）；校验碰巧对但令牌不对 → 标签不对、令牌作废、再用正确令牌也不回应；
过期拒绝；重放拒绝；替换云端公钥 / 桥公钥 / 密文都拒绝；隧道文件里任何时候都没有设备密钥与令牌（明文、base32、base64、hex）；
日志里没有令牌与设备密钥；cred_cloud 默认从凭据管理器取设备密钥；宿主生成令牌、改回窗口标题；桥动作只回令牌与过期时间。"""
from __future__ import annotations

import base64
import json
import logging
import time

import pytest

pytest.importorskip("cryptography")

from companion.cloud_link import cred_cloud  # noqa: E402
from companion.cloud_link import cred_handoff as handoff  # noqa: E402
from companion.cloud_link import device_pairing as pairing  # noqa: E402

HOST = "198.51.100.7"
DEVICE_KEY = "dk_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t"
NOW = 1_800_000_000.0


class Clock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now


class Vault:
    """假的云端凭据管理器。"""

    def __init__(self, fail=False):
        self.items, self.fail = {}, fail

    def store(self, host, key):
        if self.fail:
            raise OSError(5, "CredWriteW")
        self.items[pairing.device_target(host)] = (host, key)

    def read_back(self, host):
        found = self.items.get(pairing.device_target(host))
        return found[1] if found else ""


@pytest.fixture
def written(monkeypatch):
    """记下写进隧道 ctl 的每一份文件内容。"""
    seen = []
    real = handoff.write_atomic

    def record(path, data):
        seen.append((path.name, bytes(data)))
        real(path, data)

    monkeypatch.setattr(handoff, "write_atomic", record)
    return seen


def _bridge(clock=None, wall=lambda: NOW, key=DEVICE_KEY):
    return pairing.BridgePairing(HOST, lambda: key, clock=clock or Clock(), wall=wall)


def _run(ctl, bridge, token, *, vault=None, before_bridge=None, after_bridge=None, timeout=0.3, now=lambda: NOW):
    """云端 pair()：每次等待时桥看一遍 ctl（可在桥看之前 / 之后改文件）。回 (原因码, vault)。"""
    vault = vault or Vault()

    def sleep(_):
        if before_bridge is not None:
            before_bridge(ctl)
        bridge.pump(ctl)
        if after_bridge is not None:
            after_bridge(ctl)
        time.sleep(0.005)

    code = pairing.pair(ctl, token, HOST, timeout=timeout, poll=0, now=now, sleep=sleep, store=vault.store,
                        read_back=vault.read_back)
    bridge.pump(ctl)   # 收回执
    return code, vault


def _mutate(path, **changes):
    if not path.exists():
        return
    head = json.loads(path.read_bytes())
    head.update(changes)
    path.write_bytes(json.dumps(head).encode())


def _other_pub():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    raw = X25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw,
                                                                 serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def _flip_b64(value):
    raw = bytearray(base64.b64decode(value))
    raw[0] ^= 0x01
    return base64.b64encode(bytes(raw)).decode()


# ── 令牌 ──────────────────────────────────────────────────────────────────────

def test_token_format_and_round_trip():
    raw, text = pairing.new_token()
    assert len(raw) == 16 and len(text) == 34
    groups = text.split("-")
    assert len(groups) == 7 and all(len(group) == 4 for group in groups)
    assert set(text.replace("-", "")) <= set(pairing.ALPHABET)
    assert not set(text) & set("ILOU")
    assert pairing.parse_token(text) == raw


def test_token_input_tolerates_case_dashes_spaces_and_lookalikes():
    raw = bytes(range(16))
    text = pairing.format_token(raw)
    assert pairing.parse_token(text.lower()) == raw
    assert pairing.parse_token(text.replace("-", "")) == raw
    assert pairing.parse_token("  " + text.replace("-", " ") + "\n") == raw
    assert pairing.parse_token(text.replace("0", "O").replace("1", "l")) == raw


def test_every_single_character_typo_and_adjacent_swap_is_caught_by_the_check():
    raw, text = pairing.new_token()
    plain = text.replace("-", "")
    for index, original in enumerate(plain):
        for replacement in pairing.ALPHABET:
            if replacement == original:
                continue
            typo = plain[:index] + replacement + plain[index + 1:]
            with pytest.raises(pairing.Refused) as caught:
                pairing.parse_token(typo)
            assert caught.value.code == pairing.CHECKSUM
    for index in range(len(plain) - 1):
        if plain[index] == plain[index + 1]:
            continue
        swapped = plain[:index] + plain[index + 1] + plain[index] + plain[index + 2:]
        with pytest.raises(pairing.Refused) as caught:
            pairing.parse_token(swapped)
        assert caught.value.code == pairing.CHECKSUM


def test_wrong_length_or_letters_are_malformed():
    for bad in ("", "ABCD", "U" * 28, "!" * 28, "0" * 29):
        with pytest.raises(pairing.Refused) as caught:
            pairing.parse_token(bad)
        assert caught.value.code == pairing.MALFORMED


# ── 往返 ──────────────────────────────────────────────────────────────────────

def test_pairing_round_trip_stores_the_device_key_and_leaves_ctl_empty(tmp_path):
    ctl = tmp_path / "ctl"
    bridge = _bridge()
    ticket = bridge.begin()
    assert bridge.status()["state"] == pairing.WAITING and ticket.expires == int(NOW + pairing.TOKEN_TTL)
    code, vault = _run(ctl, bridge, ticket.token)
    assert code == pairing.OK
    assert vault.items == {"Pearnly CC/cloud-device/" + HOST: (HOST, DEVICE_KEY)}
    assert bridge.status() == {"state": pairing.PAIRED, "code": pairing.OK, "expires": ticket.expires}
    assert not ctl.exists() or list(ctl.iterdir()) == []


def test_a_typo_is_caught_before_any_request_and_the_token_stays_usable(tmp_path):
    ctl = tmp_path / "ctl"
    bridge = _bridge()
    ticket = bridge.begin()
    plain = ticket.token.replace("-", "")
    typo = plain[:5] + ("A" if plain[5] != "A" else "B") + plain[6:]
    code, _ = _run(ctl, bridge, typo)
    assert code == pairing.CHECKSUM
    assert not ctl.exists() or list(ctl.iterdir()) == []
    assert bridge.status()["state"] == pairing.WAITING
    assert _run(ctl, bridge, ticket.token)[0] == pairing.OK


def test_a_wrong_token_with_a_valid_check_voids_the_real_one(tmp_path):
    ctl = tmp_path / "ctl"
    bridge = _bridge()
    ticket = bridge.begin()
    _, other = pairing.new_token()
    code, vault = _run(ctl, bridge, other)
    assert code == pairing.TIMEOUT and vault.items == {}
    assert bridge.status()["state"] == pairing.FAILED and bridge.status()["code"] == pairing.TAMPERED
    # 再用正确令牌也不行：要重新生成
    code, vault = _run(ctl, bridge, ticket.token)
    assert code == pairing.TIMEOUT and vault.items == {}
    assert bridge.status()["state"] == pairing.FAILED


def test_an_expired_token_is_refused(tmp_path):
    ctl = tmp_path / "ctl"
    clock = Clock()
    bridge = _bridge(clock)
    ticket = bridge.begin()
    clock.now += pairing.TOKEN_TTL + 1
    code, vault = _run(ctl, bridge, ticket.token)
    assert code == pairing.TIMEOUT and vault.items == {}
    assert bridge.status()["code"] == pairing.EXPIRED


def test_an_expired_request_is_refused_and_voids_the_token(tmp_path):
    ctl = tmp_path / "ctl"
    bridge = _bridge(wall=lambda: NOW + pairing.REQUEST_TTL + 5)
    ticket = bridge.begin()
    code, _ = _run(ctl, bridge, ticket.token, now=lambda: NOW)
    assert code == pairing.TIMEOUT
    assert bridge.status()["code"] == pairing.EXPIRED


def test_the_token_expires_by_itself_without_any_request(tmp_path):
    clock = Clock()
    bridge = _bridge(clock)
    bridge.begin()
    clock.now += pairing.TOKEN_TTL + 1
    assert bridge.pump(tmp_path) is True
    assert bridge.status()["state"] == pairing.FAILED and bridge.status()["code"] == pairing.EXPIRED


def test_a_new_token_voids_the_previous_one(tmp_path):
    ctl = tmp_path / "ctl"
    bridge = _bridge()
    first = bridge.begin()
    second = bridge.begin()
    assert first.token != second.token
    assert _run(ctl, bridge, first.token)[0] == pairing.TIMEOUT
    assert bridge.status()["code"] == pairing.TAMPERED


def test_a_replayed_request_gets_no_answer(tmp_path):
    ctl = tmp_path / "ctl"
    bridge = _bridge()
    ticket = bridge.begin()
    copies = []

    def keep(path):
        request = path / pairing.REQUEST_FILE
        if request.exists():
            copies.append(request.read_bytes())

    assert _run(ctl, bridge, ticket.token, before_bridge=keep)[0] == pairing.OK
    assert copies
    (ctl / pairing.REQUEST_FILE).write_bytes(copies[0])
    bridge.pump(ctl)
    assert not (ctl / pairing.RESPONSE_FILE).exists()
    assert not (ctl / pairing.REQUEST_FILE).exists()
    assert bridge.status()["state"] == pairing.PAIRED


def test_a_swapped_cloud_public_key_is_refused_and_voids_the_token(tmp_path):
    ctl = tmp_path / "ctl"
    bridge = _bridge()
    ticket = bridge.begin()
    swap = _other_pub()
    code, vault = _run(ctl, bridge, ticket.token,
                       before_bridge=lambda path: _mutate(path / pairing.REQUEST_FILE, cloud_pub=swap))
    assert code == pairing.TIMEOUT and vault.items == {}
    assert bridge.status()["code"] == pairing.TAMPERED


@pytest.mark.parametrize("field", ["bridge_pub", "ct", "nonce"])
def test_a_tampered_response_is_refused_and_reported_back(tmp_path, field):
    ctl = tmp_path / "ctl"
    bridge = _bridge()
    ticket = bridge.begin()

    def tamper(path):
        target = path / pairing.RESPONSE_FILE
        if not target.exists():
            return
        head = json.loads(target.read_bytes())
        head[field] = _other_pub() if field == "bridge_pub" else _flip_b64(head[field])
        target.write_bytes(json.dumps(head).encode())

    code, vault = _run(ctl, bridge, ticket.token, after_bridge=tamper)
    assert code == pairing.TAMPERED and vault.items == {}
    assert bridge.status()["state"] == pairing.FAILED and bridge.status()["code"] == pairing.NOT_STORED


def test_a_failed_store_is_reported_back(tmp_path):
    ctl = tmp_path / "ctl"
    bridge = _bridge()
    ticket = bridge.begin()
    code, _ = _run(ctl, bridge, ticket.token, vault=Vault(fail=True))
    assert code == pairing.STORE_FAILED
    assert bridge.status()["code"] == pairing.NOT_STORED


def test_a_forged_ack_fails_the_pairing(tmp_path):
    ctl = tmp_path / "ctl"
    bridge = _bridge()
    ticket = bridge.begin()
    vault = Vault()
    code = pairing.pair(ctl, ticket.token, HOST, timeout=0.3, poll=0, now=lambda: NOW,
                        sleep=lambda _: bridge.pump(ctl), store=vault.store, read_back=vault.read_back)
    assert code == pairing.OK and (ctl / pairing.ACK_FILE).exists()
    _mutate(ctl / pairing.ACK_FILE, tag="00" * 32)
    bridge.pump(ctl)
    assert bridge.status()["state"] == pairing.FAILED and bridge.status()["code"] == pairing.TAMPERED


def test_the_bridge_sweeps_its_response_ten_seconds_later(tmp_path):
    ctl = tmp_path / "ctl"
    clock = Clock()
    bridge = _bridge(clock)
    ticket = bridge.begin()
    # 手写一份合规请求，云端写完就不再管（模拟云端没取走回应）
    ctl.mkdir(parents=True, exist_ok=True)
    raw = pairing.parse_token(ticket.token)
    key = pairing.auth_key(raw, HOST)
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    cloud_pub = pairing._pub(X25519PrivateKey.generate())
    request_id = "ab" * 16
    expires = int(NOW) + 60
    head = {"v": 1, "id": request_id, "cloud_pub": cloud_pub, "expires": expires,
            "tag": pairing._tag(key, pairing._request_canon(request_id, HOST, cloud_pub, expires))}
    (ctl / pairing.REQUEST_FILE).write_bytes(json.dumps(head).encode())
    bridge.pump(ctl)
    assert (ctl / pairing.RESPONSE_FILE).exists() and bridge.status()["state"] == pairing.ANSWERED
    assert bridge.next_due() is not None
    clock.now += pairing.SWEEP_DELAY + 0.1
    bridge.pump(ctl)
    assert not (ctl / pairing.RESPONSE_FILE).exists()
    clock.now += pairing.ACK_WAIT
    bridge.pump(ctl)
    assert bridge.status()["code"] == pairing.TIMEOUT


# ── 秘密不外露 ────────────────────────────────────────────────────────────────

def _forms(raw_token: bytes, token_text: str):
    plain = token_text.replace("-", "")
    forms = [raw_token, raw_token.hex().encode(), base64.b64encode(raw_token), base64.urlsafe_b64encode(raw_token),
             base64.b32encode(raw_token), token_text.encode(), plain.encode(), plain.lower().encode(),
             plain[:20].encode()]
    key = DEVICE_KEY.encode()
    forms += [key, key[3:], base64.b64encode(key), base64.b32encode(key), key.hex().encode(),
              DEVICE_KEY.encode("utf-16-le")]
    return forms


def test_tunnel_files_never_hold_the_device_key_or_the_token(tmp_path, written):
    ctl = tmp_path / "ctl"
    bridge = _bridge()
    ticket = bridge.begin()
    snapshots = []

    def look(path):
        if path.exists():
            for item in path.iterdir():
                snapshots.append(item.read_bytes())

    code, _ = _run(ctl, bridge, ticket.token, before_bridge=look, after_bridge=look)
    assert code == pairing.OK
    names = {name for name, _ in written}
    assert names == {pairing.REQUEST_FILE, pairing.RESPONSE_FILE, pairing.ACK_FILE}
    raw = pairing.parse_token(ticket.token)
    blobs = [data for _, data in written] + snapshots
    for blob in blobs:
        for form in _forms(raw, ticket.token):
            assert form not in blob and form.lower() not in blob.lower()


def test_logs_never_hold_the_device_key_or_the_token(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    ctl = tmp_path / "ctl"
    bridge = _bridge()
    ticket = bridge.begin()
    _, other = pairing.new_token()
    _run(ctl, bridge, other)
    ticket = bridge.begin()
    assert _run(ctl, bridge, ticket.token)[0] == pairing.OK
    text = caplog.text + repr(bridge) + repr(ticket)
    raw = pairing.parse_token(ticket.token)
    for form in _forms(raw, ticket.token):
        assert form.decode("latin-1") not in text
    assert "事件=paired" in caplog.text and "事件=request_refused 原因=tampered" in caplog.text


# ── 云端默认从凭据管理器取设备密钥 ────────────────────────────────────────────

def test_load_device_key_reads_the_generic_credential():
    store = {"Pearnly CC/cloud-device/" + HOST: (HOST, DEVICE_KEY)}
    assert pairing.load_device_key(HOST, read=store.get) == DEVICE_KEY
    assert pairing.load_device_key("other.example.com", read=store.get) == ""


def test_cred_cloud_takes_the_device_key_from_the_credential_manager(tmp_path, monkeypatch):
    ctl = tmp_path / "ctl"
    asked = []
    monkeypatch.setattr(pairing, "load_device_key", lambda host: asked.append(host) or "")
    code = cred_cloud.fetch(ctl, epoch="0" * 32, connection="default", host=HOST, timeout=0, sleep=lambda _: None)
    assert code == handoff.UNPAIRED and asked == [HOST]
    # 有设备密钥：撤销照常校验
    monkeypatch.setattr(pairing, "load_device_key", lambda host: DEVICE_KEY)
    _, data = handoff.make_forget(DEVICE_KEY, "0" * 32, "default", NOW)
    handoff.write_atomic(ctl / handoff.FORGET_FILE, data)
    forgotten = []
    code = cred_cloud.handle_forget(ctl, epoch="0" * 32, seen_ids=set(), host=HOST, now=lambda: NOW,
                                    forget=forgotten.append)
    assert code == handoff.OK and forgotten == ["default"]
