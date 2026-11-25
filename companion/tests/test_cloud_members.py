"""云端成员（第二阶段 D3，11-24 首测阻塞项）：桥经 ``ctl\\members.json`` 下发「这台云端电脑在哪些站点、哪种通道」，云端据此建成员。
守住：文件用当前会话密钥加密、换会话 / 改密文 / 改种类都解不开、内容里没有令牌；桥起会话与对话页交回令牌都会下发；云端按下发建、改、删
``cloud-<站点>`` 成员（令牌只放占位、通道按前缀、找不到账套先不建）、别的成员不碰；成员变了直写循环重载；云端认电脑用配对的那把密钥。
只在 Mac 上离线测：隧道盘、远程桌面、Windows 凭据管理器都是假的（真机证据层级见 docs/work 的施工记录）。"""
from __future__ import annotations

import base64
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from companion import device_link, runtime_mode
from companion.cloud_link import cloud_members, cloud_side, ctl_files
from companion.cloud_link import tunnel_protocol as protocol
from companion.profile_store import ProfileStore

from test_cloud_host import CORE_TOKEN, COWORK_TOKEN, HOST, _host, _load, _saved

SITE_ERP = "erp.example.test"
SITE_COWORK = "cowork.example.test"


class _Protector:
    def protect(self, value: str) -> str:
        return "dpapi-current-user:v1:" + base64.b64encode(value[::-1].encode()).decode()

    def unprotect(self, value: str) -> str:
        return base64.b64decode(value.split(":", 2)[2]).decode()[::-1]


# ── ctl 文件编解码 ─────────────────────────────────────────────────────────────

def test_sealed_file_opens_only_with_the_same_session_and_kind():
    session = protocol.new_session()
    data = ctl_files.seal(session, "members", {"members": [{"site": SITE_ERP, "channel": "core", "endpoint": "AB"}]})
    _id, payload = ctl_files.open_(data, session, "members")
    assert payload["members"][0]["site"] == SITE_ERP

    with pytest.raises(ctl_files.CtlError) as wrong:
        ctl_files.open_(data, protocol.new_session(), "members")
    assert wrong.value.code == ctl_files.WRONG_EPOCH

    same_epoch_other_key = protocol.Session(epoch=session.epoch, key=b"x" * 32)
    with pytest.raises(ctl_files.CtlError) as tampered:
        ctl_files.open_(data, same_epoch_other_key, "members")
    assert tampered.value.code == ctl_files.TAMPERED

    with pytest.raises(ctl_files.CtlError):
        ctl_files.open_(data, session, "status")   # 种类对不上

    head = json.loads(data)
    raw = base64.b64decode(head["ct"])
    head["ct"] = base64.b64encode(bytes([raw[0] ^ 0xFF]) + raw[1:]).decode()
    with pytest.raises(ctl_files.CtlError) as changed:
        ctl_files.open_(json.dumps(head).encode(), session, "members")
    assert changed.value.code == ctl_files.TAMPERED


def test_file_is_ciphertext_only():
    session = protocol.new_session()
    data = ctl_files.seal(session, "members", {"members": [{"site": SITE_ERP, "channel": "core", "endpoint": "AB"}]})
    assert SITE_ERP.encode() not in data and set(json.loads(data)) == {"v", "kind", "epoch", "id", "nonce", "ct"}


@pytest.mark.parametrize("items", [
    [{"site": "Bad Site", "channel": "core"}],
    [{"site": SITE_ERP, "channel": "mrerp"}],
    [{"site": SITE_ERP, "channel": "core", "endpoint": "a_b"}],
    "nope",
])
def test_bad_members_are_refused(items):
    with pytest.raises(ctl_files.CtlError):
        ctl_files.members_of({"members": items})


def test_members_keep_the_first_entry_per_site():
    got = ctl_files.members_of({"members": [{"site": "ERP.example.test.", "channel": "core"},
                                            {"site": SITE_ERP, "channel": "cowork"}]})
    assert got == [{"site": SITE_ERP, "channel": "core", "endpoint": ""}]


# ── 桥：下发 ─────────────────────────────────────────────────────────────────

def _take_members(host):
    data = (host.root / "ctl" / ctl_files.MEMBERS_FILE).read_bytes()
    return ctl_files.members_of(ctl_files.open_(data, host._session, "members")[1])


def test_the_bridge_publishes_registered_sites_without_tokens(tmp_path):
    store = _saved(tmp_path)
    store.set_token(HOST, SITE_ERP, "https://" + SITE_ERP, CORE_TOKEN)
    store.set_token(HOST, SITE_COWORK, "https://" + SITE_COWORK, COWORK_TOKEN)
    host, *_ = _host(tmp_path)
    host.start()
    raw = (host.root / "ctl" / ctl_files.MEMBERS_FILE).read_bytes()
    assert CORE_TOKEN.encode() not in raw and b"secret" not in raw
    assert sorted(_take_members(host), key=lambda m: m["site"]) == [
        {"site": SITE_COWORK, "channel": "cowork", "endpoint": "CWEC-7"},
        {"site": SITE_ERP, "channel": "core", "endpoint": "ABCD"},
    ]
    payload = ctl_files.open_(raw, host._session, "members")[1]
    assert "secret" not in json.dumps(payload)
    host.stop()
    assert not (host.root / "ctl" / ctl_files.MEMBERS_FILE).exists()


def test_each_new_session_publishes_again_with_its_own_key(tmp_path):
    _saved(tmp_path).set_token(HOST, SITE_ERP, "https://" + SITE_ERP, CORE_TOKEN)
    host, *_ = _host(tmp_path)
    host.start()
    first = host._session
    (host.root / "ctl" / ctl_files.MEMBERS_FILE).unlink()   # 云端读走了
    host._rotate()
    assert host._session is not first
    assert _take_members(host)[0]["site"] == SITE_ERP
    with pytest.raises(ctl_files.CtlError) as old:
        ctl_files.open_((host.root / "ctl" / ctl_files.MEMBERS_FILE).read_bytes(), first, "members")
    assert old.value.code == ctl_files.WRONG_EPOCH


def test_an_unusual_endpoint_id_still_publishes_the_site(tmp_path):
    _saved(tmp_path).set_token(HOST, SITE_ERP, "https://" + SITE_ERP, "pcc_odd.name.x_secretvalue")
    host, *_ = _host(tmp_path)
    host.start()
    assert _take_members(host) == [{"site": SITE_ERP, "channel": "core", "endpoint": ""}]


def test_a_new_site_token_is_published_at_once(tmp_path):
    _saved(tmp_path)
    host, *_ = _host(tmp_path)
    host.start()
    assert _take_members(host) == []
    _load(tmp_path).set_token(HOST, SITE_ERP, "https://" + SITE_ERP, CORE_TOKEN)
    assert host.publish_members() is True
    assert _take_members(host) == [{"site": SITE_ERP, "channel": "core", "endpoint": "ABCD"}]
    host.stop()
    assert host.publish_members() is False   # 没在跑：不写


def test_the_page_handing_back_a_token_publishes_to_the_running_host(tmp_path, monkeypatch):
    from companion import tomo_bridge

    _saved(tmp_path)
    calls = []
    bridge = tomo_bridge.TomoBridge.__new__(tomo_bridge.TomoBridge)
    bridge._settings = SimpleNamespace(member_for=lambda page: (SITE_ERP, "https://" + SITE_ERP, "pcc_OWN_x"))
    bridge._page_host = lambda: SITE_ERP
    bridge._cloud_links = lambda: _load(tmp_path)
    bridge._cloud_host = SimpleNamespace(host=HOST, publish_members=lambda: calls.append("published"))
    assert bridge._cloud_token({"host": HOST, "token": CORE_TOKEN}) == {"ok": True}
    assert calls == ["published"]


# ── 云端：建成员 ──────────────────────────────────────────────────────────────

ROOT = r"D:\CLOUDUSER\ExpressI-Cloud"
BOOK = SimpleNamespace(path=str(Path(__file__)), company="บริษัท ทดสอบ จำกัด", row=3)   # 路径要存在（default_account）


def _sync(tmp_path, members, *, roots=(ROOT,), sets=(BOOK,)):
    load = lambda: ProfileStore.load(tmp_path / "companion.json", protector=_Protector())  # noqa: E731
    return cloud_members.sync(members, load=load, detect=lambda store: list(roots),
                              probe_sets=lambda root: list(sets), probe_defaults=lambda path: {"ap_acc": "2100"}), load


def test_cloud_builds_one_member_per_site_with_a_placeholder_token(tmp_path):
    (code, changed), load = _sync(tmp_path, [ctl_files.member_entry(SITE_ERP, "core", "ABCD"),
                                             ctl_files.member_entry(SITE_COWORK, "cowork", "CWEC-7")])
    assert (code, changed) == (cloud_members.READY, True)
    configs = {cfg.profile_id: cfg for cfg in load().runtime_configs()}
    erp, cowork = configs["cloud-" + SITE_ERP], configs["cloud-" + SITE_COWORK]
    assert erp.bearer_token == "pcc_ABCD_cloud-relay" and erp.backend_url == "https://" + SITE_ERP
    assert cowork.bearer_token == "exp_CWEC-7_cloud-relay"
    assert {r.channel for r in load().profiles} == {"core", "cowork"}
    assert erp.express_root == ROOT and erp.account_dir == BOOK.path and erp.account_company == BOOK.company
    assert erp.ap_acc == "2100" and erp.kind == "express"


def test_cloud_without_books_builds_nothing_and_says_so(tmp_path):
    (code, changed), load = _sync(tmp_path, [ctl_files.member_entry(SITE_ERP, "core", "ABCD")], roots=())
    assert (code, changed) == (cloud_members.NO_BOOKS, False)
    assert load().profiles == ()
    (code, _), _ = _sync(tmp_path, [ctl_files.member_entry(SITE_ERP, "core")], sets=())
    assert code == cloud_members.NO_BOOKS


def test_cloud_drops_sites_the_bridge_no_longer_has_and_leaves_other_members(tmp_path):
    _sync(tmp_path, [ctl_files.member_entry(SITE_ERP, "core", "ABCD"), ctl_files.member_entry(SITE_COWORK, "cowork")])
    store = ProfileStore.load(tmp_path / "companion.json", protector=_Protector())
    other = store.new_runtime_config()
    other.update(bearer_token="exp_X_y", account_dir=BOOK.path, account_set=BOOK.path, profile_id="profile-local")
    store.save_profile(other)

    (code, changed), load = _sync(tmp_path, [ctl_files.member_entry(SITE_ERP, "core", "ABCD")])
    assert (code, changed) == (cloud_members.READY, True)
    assert sorted(r.profile_id for r in load().profiles) == ["cloud-" + SITE_ERP, "profile-local"]
    (code, changed), _ = _sync(tmp_path, [ctl_files.member_entry(SITE_ERP, "core", "ABCD")])
    assert (code, changed) == (cloud_members.READY, False)   # 没变：不重载
    (code, changed), load = _sync(tmp_path, [])
    assert (code, changed) == (cloud_members.NONE, True)
    assert [r.profile_id for r in load().profiles] == ["profile-local"]


def test_a_changed_endpoint_updates_the_member_and_keeps_its_book(tmp_path):
    _sync(tmp_path, [ctl_files.member_entry(SITE_ERP, "core", "ABCD")])
    (code, changed), load = _sync(tmp_path, [ctl_files.member_entry(SITE_ERP, "core", "EFGH")], roots=())
    assert (code, changed) == (cloud_members.READY, True)
    cfg = load().runtime_config("cloud-" + SITE_ERP)
    assert cfg.bearer_token == "pcc_EFGH_cloud-relay" and cfg.account_dir == BOOK.path


def test_a_republished_site_clears_the_rejected_mark(tmp_path):
    """云端 401 记下的「密钥不能再用」：桥再下发这个站点（它现在有可用令牌）就清掉，否则端点号不变时成员永远停着。"""
    _sync(tmp_path, [ctl_files.member_entry(SITE_ERP, "core", "ABCD")])
    store = ProfileStore.load(tmp_path / "companion.json", protector=_Protector())
    store.set_device_state("cloud-" + SITE_ERP, device_link.STALE_KEY)
    (code, changed), load = _sync(tmp_path, [ctl_files.member_entry(SITE_ERP, "core", "ABCD")], roots=())
    assert (code, changed) == (cloud_members.READY, True)
    assert load().runtime_config("cloud-" + SITE_ERP).device_state == ""


def test_the_second_site_reuses_the_roots_the_first_one_found(tmp_path):
    _sync(tmp_path, [ctl_files.member_entry(SITE_ERP, "core")])
    (code, _), load = _sync(tmp_path, [ctl_files.member_entry(SITE_ERP, "core"),
                                       ctl_files.member_entry(SITE_COWORK, "cowork")], roots=())
    assert code == cloud_members.READY
    assert load().runtime_config("cloud-" + SITE_COWORK).express_root == ROOT


# ── 云端：读 ctl ─────────────────────────────────────────────────────────────

def _link(tmp_path):
    for name in ("in", "out", "ctl"):
        (tmp_path / name).mkdir()
    return cloud_side.CloudLink(tmp_path, protocol.new_session(), take=lambda root: None)


def test_take_members_reads_deletes_and_bumps_only_on_change(tmp_path):
    link = _link(tmp_path)
    got = []
    path = link.ctl_dir / ctl_files.MEMBERS_FILE
    path.write_bytes(ctl_files.seal(link.session, "members", {"members": [{"site": SITE_ERP, "channel": "core"}]}))
    assert cloud_side.take_members(link, sync=lambda m: (got.append(m) or (cloud_members.READY, True))) == "ready"
    assert not path.exists() and link.generation == 1 and link.members_code == "ready"
    assert got == [[{"site": SITE_ERP, "channel": "core", "endpoint": ""}]]

    path.write_bytes(ctl_files.seal(link.session, "members", {"members": []}))
    assert cloud_side.take_members(link, sync=lambda m: (cloud_members.NONE, False)) == "none"
    assert link.generation == 1
    assert cloud_side.take_members(link, sync=lambda m: pytest.fail("no file")) is None


def test_take_members_ignores_an_old_session_file(tmp_path):
    link = _link(tmp_path)
    path = link.ctl_dir / ctl_files.MEMBERS_FILE
    path.write_bytes(ctl_files.seal(protocol.new_session(), "members", {"members": []}))
    assert cloud_side.take_members(link, sync=lambda m: pytest.fail("old session")) == ctl_files.WRONG_EPOCH
    assert not path.exists() and link.generation == 0


def test_members_sealed_with_a_session_the_bridge_just_rotated_to_are_not_lost(tmp_path):
    for name in ("in", "out", "ctl"):
        (tmp_path / name).mkdir()
    old, new = protocol.new_session(), protocol.new_session()
    (tmp_path / "ctl" / protocol.SESSION_FILE).write_bytes(protocol.encode_session(new))
    (tmp_path / "ctl" / ctl_files.MEMBERS_FILE).write_bytes(
        ctl_files.seal(new, "members", {"members": [{"site": SITE_ERP, "channel": "core"}]}))
    link = cloud_side.CloudLink(tmp_path, old)   # 还拿着旧会话（换会话的那次 check 之后桥又换了一次）
    assert cloud_side.take_members(link, sync=lambda m: (cloud_members.READY, True)) == "ready"
    assert link.session == new and link.generation == 1


def test_check_ctl_takes_the_new_session_before_the_members(tmp_path):
    for name in ("in", "out", "ctl"):
        (tmp_path / name).mkdir()
    old, new = protocol.new_session(), protocol.new_session()
    (tmp_path / "ctl" / protocol.SESSION_FILE).write_bytes(protocol.encode_session(new))
    (tmp_path / "ctl" / ctl_files.MEMBERS_FILE).write_bytes(ctl_files.seal(new, "members", {"members": []}))
    link = cloud_side.CloudLink(tmp_path, old)
    seen = []
    cloud_side.check_ctl(link, members=lambda got: seen.append(cloud_side.take_members(
        got, sync=lambda m: (cloud_members.NONE, True))))
    assert link.session == new and seen == ["none"] and link.generation == 1


def test_wait_change_returns_when_the_generation_moves(tmp_path):
    link = _link(tmp_path)
    timer = threading.Timer(0.05, link.bump)
    timer.start()
    link.wait_change(0, lambda: False, step=0.01)
    assert link.generation == 1
    link.wait_change(5, lambda: True)   # 要停：立刻回


# ── 云端认电脑用配对的那把密钥 ─────────────────────────────────────────────────

def test_cloud_device_key_is_the_paired_one_and_never_a_new_one(monkeypatch):
    monkeypatch.setenv(runtime_mode.CLOUD_ENV, "1")
    device_link._reset_for_tests()
    monkeypatch.setattr(cloud_side, "paired_device_key", lambda: "")
    monkeypatch.setattr(ProfileStore, "load", classmethod(lambda cls: pytest.fail("cloud must not make a key")))
    assert device_link.device_key() == ""
    monkeypatch.setattr(cloud_side, "paired_device_key", lambda: "k" * 43)
    assert device_link.device_key() == "k" * 43
    device_link._reset_for_tests()


def test_paired_device_key_reads_the_paired_host_entry():
    assert cloud_side.paired_device_key(host=lambda: "", load=lambda h: pytest.fail("no host")) == ""
    assert cloud_side.paired_device_key(host=lambda: HOST, load=lambda h: "key-of-" + h) == "key-of-" + HOST
