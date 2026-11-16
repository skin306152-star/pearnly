"""桥本机的云端连接配置（``companion.cloud_link.link_store``）与配对代登记的桥动作（``tomo_bridge`` 的 cloud_pending / cloud_token）。

守住：配置单独一个文件、没有云端连接就不建文件（现有 companion.json 与 state() 逐字节不变）；设备密钥与 device_link 同格式、
令牌与设备密钥只以保护器密文落盘；一个桥最多一条；删除连带吊销令牌、删两条本机凭据；密码只经凭据管理器，不进文件、日志、
repr、异常文本；桥只把设备密钥交给这台电脑连着的站点，令牌通道要与本站成员相同。"""
from __future__ import annotations

import base64
import json
import logging
import re
import sys
import types

import pytest

from companion import config
from companion.cloud_link import credentials as rdp_credentials
from companion.cloud_link import link_store
from companion.cloud_link.link_store import LinkStore, LinkStoreError, PasswordMissing

PASSWORD = "Cloud-Pass-9!x"
TOKEN = "exp_CWEC-7_secrettokenvalue"
CORE_TOKEN = "pcc_ABCD_secretcorevalue"


class _Protector:
    """可逆的假 DPAPI：密文里看不出明文。"""

    def protect(self, value: str) -> str:
        return "dpapi-current-user:v1:" + base64.b64encode(value[::-1].encode()).decode()

    def unprotect(self, value: str) -> str:
        return base64.b64decode(value.split(":", 2)[2]).decode()[::-1]


def _store(tmp_path):
    return LinkStore.load(tmp_path / "cloud_link.json", protector=_Protector())


def _saved(tmp_path):
    store = _store(tmp_path)
    store.save_link(host="198.51.100.7", port=3390, user="CLOUDUSER-01", domain="example",
                    host_name="CLOUD-HOST", via="OFFICE-PC")
    return store


def test_the_file_lives_next_to_companion_json_and_is_not_created_without_a_link(tmp_path):
    assert config.CLOUD_LINK_FILE.parent == config.CONFIG_FILE.parent
    assert config.CLOUD_LINK_FILE != config.CONFIG_FILE
    store = _store(tmp_path)
    assert store.links == () and not (tmp_path / "cloud_link.json").exists()
    assert list(tmp_path.iterdir()) == []


def test_save_and_reload_keep_the_fields_and_protect_the_device_key(tmp_path):
    store = _saved(tmp_path)
    raw = (tmp_path / "cloud_link.json").read_text(encoding="utf-8")
    key = store.device_key("198.51.100.7")
    # 与 device_link.new_device_key 同格式（服务端收 32–128 个 [A-Za-z0-9_-]）
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", key)
    assert key not in raw and PASSWORD not in raw
    doc = json.loads(raw)
    assert doc["schema_version"] == 1 and len(doc["links"]) == 1
    assert doc["links"][0]["device"]["key"].startswith("dpapi-current-user:v1:")
    again = _store(tmp_path)
    link = again.get("198.51.100.7")
    assert (link.host, link.port, link.user, link.domain, link.host_name, link.via) == \
        ("198.51.100.7", 3390, "CLOUDUSER-01", "example", "CLOUD-HOST", "OFFICE-PC")
    assert link.device_name == "CLOUD-HOST"
    assert again.device_key("198.51.100.7") == key
    # 改端口不换设备密钥
    again.save_link(host="198.51.100.7", port=4491, user="CLOUDUSER-01", domain="example")
    assert again.device_key("198.51.100.7") == key and again.get("198.51.100.7").host_name == "CLOUD-HOST"


def test_one_link_per_bridge_and_bad_values_are_refused(tmp_path):
    store = _saved(tmp_path)
    with pytest.raises(LinkStoreError):
        store.save_link(host="other.example.com", port=3389, user="u")
    with pytest.raises(LinkStoreError):
        store.save_link(host="bad host\n", port=3389, user="u")
    with pytest.raises(LinkStoreError):
        store.save_link(host="198.51.100.7", port=0, user="u")


def test_tokens_per_site_pending_and_drop(tmp_path):
    store = _saved(tmp_path)
    host = "198.51.100.7"
    assert [link.host for link in store.pending("testcowork.pearnly.com")] == [host]
    store.set_token(host, "testcowork.pearnly.com", "https://testcowork.pearnly.com", TOKEN)
    store.set_token(host, "testerp.pearnly.com", "https://testerp.pearnly.com", CORE_TOKEN)
    raw = (tmp_path / "cloud_link.json").read_text(encoding="utf-8")
    assert TOKEN not in raw and CORE_TOKEN not in raw
    again = _store(tmp_path)
    assert again.token(host, "TESTCOWORK.pearnly.com") == TOKEN
    assert again.token(host, "testerp.pearnly.com") == CORE_TOKEN
    assert again.backend_url(host, "testerp.pearnly.com") == "https://testerp.pearnly.com"
    assert again.pending("testcowork.pearnly.com") == [] and again.token(host, "nope.pearnly.com") == ""
    for bad in ("", "abc", TOKEN + "@testcowork.pearnly.com", "exp_ x"):
        with pytest.raises(LinkStoreError):
            again.set_token(host, "testcowork.pearnly.com", "https://testcowork.pearnly.com", bad)
    assert again.drop_token(host, "testcowork.pearnly.com") is True
    assert again.token(host, "testcowork.pearnly.com") == "" and again.drop_token(host, "testcowork.pearnly.com") is False


def test_remove_revokes_tokens_and_forgets_both_local_credentials(tmp_path, monkeypatch):
    store = _saved(tmp_path)
    host = "198.51.100.7"
    store.set_token(host, "testcowork.pearnly.com", "https://testcowork.pearnly.com", TOKEN)
    forgotten = []
    monkeypatch.setattr(rdp_credentials, "forget", lambda name: forgotten.append(("TERMSRV", name)) or True)
    monkeypatch.setattr(link_store, "forget_password", lambda name: forgotten.append(("password", name)) or True)
    left = []
    assert store.remove(host, leave=lambda url, token: left.append((url, token)) or True) is True
    assert left == [("https://testcowork.pearnly.com", TOKEN)]
    assert forgotten == [("TERMSRV", host), ("password", host)]
    assert not (tmp_path / "cloud_link.json").exists() and _store(tmp_path).links == ()
    assert store.remove(host, leave=lambda *a: True) is False


def test_remove_still_deletes_when_the_site_cannot_be_reached(tmp_path, monkeypatch):
    store = _saved(tmp_path)
    store.set_token("198.51.100.7", "testcowork.pearnly.com", "https://testcowork.pearnly.com", TOKEN)
    monkeypatch.setattr(rdp_credentials, "forget", lambda name: False)
    monkeypatch.setattr(link_store, "forget_password", lambda name: (_ for _ in ()).throw(OSError(5, "CredDeleteW")))

    def down(url, token):
        raise ConnectionError("down " + token)   # 即使异常里带了令牌，日志也只记异常类型

    assert store.remove("198.51.100.7", leave=down) is True
    assert _store(tmp_path).links == ()


class _FakeCreds(types.SimpleNamespace):
    pass


def _fake_creds():
    saved = {}
    fake = _FakeCreds(
        store=lambda name, user, password, comment="Pearnly CC": saved.__setitem__(name, (user, password)),
        read=lambda name: saved.get(name),
        user_name=lambda name: saved.get(name, ("", ""))[0],
        forget=lambda name: saved.pop(name, None) is not None,
    )
    return fake, saved


def test_password_goes_only_to_the_generic_credential(tmp_path, monkeypatch):
    fake, saved = _fake_creds()
    monkeypatch.setattr(link_store, "_creds", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    link_store.store_password("198.51.100.7", "CLOUDUSER-01", "example", PASSWORD)
    assert saved == {"Pearnly CC/cloud/198.51.100.7": ("example\\CLOUDUSER-01", PASSWORD)}
    assert link_store.password_target("198.51.100.7") == "Pearnly CC/cloud/198.51.100.7"
    assert link_store.has_password("198.51.100.7")
    assert link_store.password_provider("198.51.100.7")() == PASSWORD
    assert link_store.forget_password("198.51.100.7") is True and saved == {}
    with pytest.raises(PasswordMissing) as missing:
        link_store.password_provider("198.51.100.7")()
    assert "CLOUDUSER" not in str(missing.value) and PASSWORD not in str(missing.value)
    with pytest.raises(ValueError):
        link_store.store_password("198.51.100.7", "u", "", "")


def test_password_store_refuses_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(rdp_credentials.NotWindows):
        link_store.store_password("198.51.100.7", "u", "", PASSWORD)


def test_secrets_never_reach_logs_repr_or_errors(tmp_path, monkeypatch, caplog):
    fake, _ = _fake_creds()
    monkeypatch.setattr(link_store, "_creds", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(rdp_credentials, "forget", lambda name: True)
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(logging.getLogger("pearnly_companion"), "propagate", True)
    store = _saved(tmp_path)
    host = "198.51.100.7"
    link_store.store_password(host, "CLOUDUSER-01", "example", PASSWORD)
    store.set_token(host, "testcowork.pearnly.com", "https://testcowork.pearnly.com", TOKEN)
    key = store.device_key(host)
    link = store.get(host)
    cipher = link.device_cipher
    shown = repr(store) + repr(link) + repr(link.members) + str(link)
    for secret in (PASSWORD, TOKEN, key, cipher, link.members[0].token_cipher):
        assert secret not in shown
    errors = []
    for bad in (lambda: store.set_token(host, "x.pearnly.com", "https://x.pearnly.com", "bad" + TOKEN),
                lambda: store.save_link(host="other.example.com", port=1, user="u"),
                lambda: LinkStore.load(tmp_path / "missing" / "x.json", protector=_Protector()).device_key(host)):
        with pytest.raises(LinkStoreError) as caught:
            bad()
        errors.append(str(caught.value))
    store.remove(host, leave=lambda url, token: (_ for _ in ()).throw(RuntimeError(token)))
    text = caplog.text + " ".join(errors)
    for secret in (PASSWORD, TOKEN, key, cipher):
        assert secret not in text
    assert "198.51.100.7" in caplog.text


# ── 桥：配对代登记 ──────────────────────────────────────────────────────────────

class _Settings:
    def __init__(self, member):
        self.member = member

    def snapshot(self):
        return {"members": [{"id": "m1"}], "version": "1.12.22"}

    def member_for(self, host):
        return self.member if self.member and host == self.member[0] else None


def _bridge(tmp_path, member, store):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from companion.tomo_bridge import TomoBridge

    QApplication.instance() or QApplication([])
    return TomoBridge(_Settings(member), allowed=lambda: True, window=lambda: None, set_lang=lambda lang: None,
                      page_url=lambda: "https://testcowork.pearnly.com/assistant", cloud_links=lambda: store)


def test_bridge_state_is_unchanged_without_a_cloud_link(tmp_path):
    bridge = _bridge(tmp_path, None, None)
    from companion.tomo_bridge import FEATURES

    expected = json.dumps({"members": [{"id": "m1"}], "version": "1.12.22", "window": {"frameless": True},
                           "features": list(FEATURES)}, ensure_ascii=False)
    assert bridge.state() == expected
    empty = _bridge(tmp_path, None, _store(tmp_path))
    assert empty.state() == expected
    assert json.loads(_bridge(tmp_path, None, _saved(tmp_path)).state())["cloud_link"] is True


def test_default_cloud_store_reads_nothing_without_the_file(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from companion import tomo_bridge

    monkeypatch.setattr(config, "CLOUD_LINK_FILE", tmp_path / "cloud_link.json")
    assert tomo_bridge._cloud_store() is None
    assert list(tmp_path.iterdir()) == []


def test_bridge_hands_the_cloud_device_key_only_to_a_connected_site(tmp_path):
    store = _saved(tmp_path)
    refused = _bridge(tmp_path, None, store)
    assert json.loads(refused.call("cloud_pending", "{}")) == {"ok": False, "code": "not_connected"}
    member = ("testcowork.pearnly.com", "https://testcowork.pearnly.com", "exp_OWN_member")
    bridge = _bridge(tmp_path, member, store)
    pending = json.loads(bridge.call("cloud_pending", "{}"))
    assert pending == {"ok": True, "links": [{"host": "198.51.100.7",
                                              "device": {"key": store.device_key("198.51.100.7"),
                                                         "name": "CLOUD-HOST"}}]}


def test_bridge_stores_the_token_only_for_the_same_channel(tmp_path):
    store = _saved(tmp_path)
    member = ("testcowork.pearnly.com", "https://testcowork.pearnly.com", "exp_OWN_member")
    bridge = _bridge(tmp_path, member, store)
    wrong = json.loads(bridge.call("cloud_token", json.dumps({"host": "198.51.100.7", "token": CORE_TOKEN})))
    assert wrong == {"ok": False, "code": "token_invalid"}
    missing = json.loads(bridge.call("cloud_token", json.dumps({"host": "other.example.com", "token": TOKEN})))
    assert missing == {"ok": False, "code": "not_found"}
    stored = bridge.call("cloud_token", json.dumps({"host": "198.51.100.7", "token": TOKEN}))
    assert json.loads(stored) == {"ok": True} and TOKEN not in stored
    assert _store(tmp_path).token("198.51.100.7", "testcowork.pearnly.com") == TOKEN
    assert json.loads(bridge.call("cloud_pending", "{}")) == {"ok": True, "links": []}
