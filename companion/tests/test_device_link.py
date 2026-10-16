# -*- coding: utf-8 -*-
"""这台电脑在池里的身份（多台电脑，ADR-021，CC 桌面版 1.7.0）：设备密钥、认电脑（device_hello）、断开（device_leave），
旧服务端（401 / 403 / 404）从不当成配对失效，以及服务端说「这把密钥不能再用」时本机怎么处理。"""
import base64
import json
import re
from types import SimpleNamespace

import httpx
import pytest

from src.companion import desktop_settings, device_link, pairing, pairing_flow
from src.companion.config import CompanionConfig, member_active
from src.companion.desktop_settings import DesktopSettings
from src.companion.profile_store import ProfileStore, ProfileStoreError

SERVER_DEVICE_KEY = re.compile(r"^[A-Za-z0-9_-]{32,128}$")   # 服务端 companion.contracts._DEVICE_KEY


class _Protector:
    prefix = "test:"

    def protect(self, value: str) -> str:
        return self.prefix + base64.b64encode(value.encode()).decode()

    def unprotect(self, value: str) -> str:
        if not value.startswith(self.prefix):
            raise ProfileStoreError("bad test ciphertext")
        return base64.b64decode(value[len(self.prefix):]).decode()


@pytest.fixture(autouse=True)
def _fresh():
    device_link._reset_for_tests()
    yield
    device_link._reset_for_tests()


def _store(tmp_path, *members):
    store = ProfileStore.load(tmp_path / "companion.json", protector=_Protector())
    for token, kind in members:
        pool = kind == "pool"   # 没有 Express、按只读成员加入 Express 池的
        kind = "folder" if pool else kind
        cfg = store.new_runtime_config()
        cfg.update(backend_url="https://testcowork.pearnly.com", bearer_token=token, paired=True, kind=kind, pool=pool,
                   endpoint_id=token.split("_")[1], account_dir="" if kind == "folder" else "S:\\EXP\\A",
                   account_set="" if kind == "folder" else "S:\\EXP\\A")
        store.save_profile(cfg)
    return store


def _reload(store):
    return ProfileStore.load(store.path, protector=_Protector())


# ── 设备密钥 ──

def test_a_new_device_key_is_32_random_bytes_the_server_accepts():
    keys = {device_link.new_device_key() for _ in range(20)}
    assert len(keys) == 20
    assert all(SERVER_DEVICE_KEY.fullmatch(key) and len(key) == 43 for key in keys)
    assert len(base64.urlsafe_b64decode(next(iter(keys)) + "=")) == 32


def test_the_device_key_is_made_once_kept_encrypted_and_survives_removing_every_member(tmp_path):
    store = _store(tmp_path, ("exp_C1_secret", "express"))
    key = store.device_key()
    assert SERVER_DEVICE_KEY.fullmatch(key)
    raw = store.path.read_text(encoding="utf-8")
    assert key not in raw and json.loads(raw)["device"]["key"].startswith("test:")   # 只存 DPAPI 密文
    assert _reload(store).device_key() == key == store.device_key()
    store.remove_profile(store.profiles[0].profile_id)
    assert _reload(store).device_key() == key


def test_an_older_copy_of_the_settings_never_drops_the_device_key(tmp_path):
    # 取任务线程手里的配置读得早（那时还没有设备密钥），之后存成员：文件里已有的设备密钥照留
    store = _store(tmp_path, ("exp_C1_secret", "express"))
    early = _reload(store)
    key = _reload(store).device_key()
    cfg = early.runtime_configs()[0]
    cfg.update(account_set="S:\\EXP\\B")
    early.save_profile(cfg)
    assert _reload(store).device_key() == key
    assert early.device_key() == key


def test_settings_written_by_an_older_version_still_load(tmp_path):
    store = _store(tmp_path, ("exp_C1_secret", "express"))
    document = json.loads(store.path.read_text(encoding="utf-8"))
    for profile in document["profiles"]:
        profile.pop("device_state")
    store.path.write_text(json.dumps(document), encoding="utf-8")
    reloaded = _reload(store)
    assert reloaded.profiles[0].device_state == "" and reloaded.device_key()


def test_without_dpapi_there_is_no_device_key_and_nothing_breaks(tmp_path):
    def broken():
        raise ProfileStoreError("Windows CurrentUser DPAPI is required")

    assert device_link.device_key(broken) == ""
    assert device_link.identity(broken) == {}


def test_the_device_identity_for_the_page_is_the_key_and_the_computer_name(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setattr(device_link, "_device_name", lambda: "ACC-01")
    found = device_link.identity(lambda: _reload(store))
    assert found == {"key": _reload(store).device_key(), "name": "ACC-01"}


# ── 两条机器方法 ──

def _transport(monkeypatch, handler):
    real = httpx.Client

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(device_link.httpx, "Client", client)


def test_hello_and_leave_post_to_the_device_methods_with_the_member_key(monkeypatch):
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.headers["authorization"], json.loads(request.content)))
        status = "attached" if request.url.path.endswith(".hello") else "left"
        return httpx.Response(200, json={"message": {"ok": True, "protocol": 1, "status": status}})

    _transport(monkeypatch, handler)
    body = {"device_key": "k" * 43, "name": "PC", "host_names": ["PC"]}
    assert device_link.hello("https://site.test", "exp_C1_s", body)["status"] == "attached"
    assert device_link.leave("https://site.test", "exp_C1_s") is True
    assert seen == [("POST", "/api/method/pearnly_cowork.companion.device_api.hello", "Bearer exp_C1_s", body),
                    ("POST", "/api/method/pearnly_cowork.companion.device_api.leave", "Bearer exp_C1_s", {})]


@pytest.mark.parametrize("code", [401, 403, 404])
def test_401_403_404_mean_an_old_server_never_an_unpairing(monkeypatch, code):
    _transport(monkeypatch, lambda request: httpx.Response(code, json={}))
    with pytest.raises(device_link.OldServer):
        device_link.hello("https://site.test", "exp_C1_s", {})
    assert device_link.leave("https://site.test", "exp_C1_s") is False


def test_leave_without_a_network_only_reports_that_nothing_was_revoked(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("down")

    _transport(monkeypatch, handler)
    assert device_link.leave("https://site.test", "exp_C1_s") is False
    assert device_link.leave("", "exp_C1_s") is False and device_link.leave("https://x", "") is False


# ── 认电脑 ──

class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _announcer(replies, clock, names=lambda: ["PC1", "192.168.0.78"], key=lambda: "K" * 43, results=None):
    sent = []

    def send(url, token, body):
        sent.append((url, token, body))
        reply = replies.pop(0) if replies else {"ok": True, "status": "attached"}
        if isinstance(reply, Exception):
            raise reply
        return reply

    announcer = device_link.Announcer(key=key, send=send, names=names, clock=clock,
                                      on_result=(lambda cfg, status: results.append((cfg.profile_id, status)))
                                      if results is not None else None)
    return announcer, sent


def _cfg(token="exp_C1_secret", **extra):
    return CompanionConfig(profile_id="P1", backend_url="https://site.test", bearer_token=token, paired=True, **extra)


def test_each_member_says_hello_once_with_the_device_key_name_and_host_names(monkeypatch):
    # 1.7.0 起不再报「常驻」（ADR-021 第 56 条，用户 10-16）
    names = ["PC1"]
    monkeypatch.setattr(device_link, "_device_name", lambda: names[0])
    clock = _Clock()
    announcer, sent = _announcer([], clock)
    assert announcer.announce(_cfg()) == "attached"
    assert sent == [("https://site.test", "exp_C1_secret",
                     {"device_key": "K" * 43, "name": "PC1", "host_names": ["PC1", "192.168.0.78"]})]
    assert announcer.announce(_cfg()) is None   # 内容没变：不再发
    clock.now = 10_000
    assert announcer.announce(_cfg()) is None
    assert len(sent) == 1
    assert announcer.announce(_cfg(token="exp_C2_other")) == "attached"   # 另一个成员（MR.ERP）各发各的
    names[0] = "PC1-NEW"
    assert announcer.announce(_cfg()) == "attached"   # 电脑改了名：马上再说一次
    assert [body["name"] for _u, _t, body in sent] == ["PC1", "PC1", "PC1-NEW"]
    assert all("always_on" not in body for _u, _t, body in sent)


def test_a_new_network_address_is_told_again(monkeypatch):
    clock, names = _Clock(), [["PC1", "192.168.0.78"]]
    announcer, sent = _announcer([], clock, names=lambda: names[0])
    announcer.announce(_cfg())
    names[0] = ["PC1", "192.168.0.99"]   # DHCP 换了地址（device_facts 十分钟重查一次）
    assert announcer.announce(_cfg()) == "attached"
    assert [body["host_names"] for _u, _t, body in sent] == [["PC1", "192.168.0.78"], ["PC1", "192.168.0.99"]]


def test_an_old_server_is_asked_again_in_half_an_hour_and_nothing_changes_locally():
    clock = _Clock()
    announcer, sent = _announcer([device_link.OldServer("404")], clock)
    cfg = _cfg()
    assert announcer.announce(cfg) is None and cfg.device_state == "" and member_active(cfg)
    clock.now = 1799
    assert announcer.announce(cfg) is None and len(sent) == 1
    clock.now = 1801
    assert announcer.announce(cfg) == "attached" and len(sent) == 2


def test_a_failed_or_refused_hello_is_tried_again_in_five_minutes():
    clock = _Clock()
    announcer, sent = _announcer([httpx.ConnectError("x"), {"ok": False, "code": "companion.request_invalid"}], clock)
    assert announcer.announce(_cfg()) is None
    clock.now = 301
    assert announcer.announce(_cfg()) is None
    clock.now = 602
    assert announcer.announce(_cfg()) == "attached" and len(sent) == 3


@pytest.mark.parametrize("status", ["superseded", "claimed_elsewhere"])
def test_a_key_the_server_will_not_take_any_more_stops_the_member(status):
    clock, results = _Clock(), []
    announcer, sent = _announcer([{"ok": True, "status": status}], clock, results=results)
    cfg = _cfg()
    assert announcer.announce(cfg) == status
    assert cfg.device_state == status and not member_active(cfg)
    assert results == [("P1", status)]
    clock.now = 10_000
    assert announcer.announce(cfg) is None and len(sent) == 1   # 停用的成员不再认电脑


def test_no_device_key_or_no_machine_key_means_no_hello():
    clock = _Clock()
    announcer, sent = _announcer([], clock, key=lambda: "")
    assert announcer.announce(_cfg()) is None
    other, sent_other = _announcer([], clock)
    assert other.announce(_cfg(token="dev-mock-token")) is None
    assert sent == sent_other == []


def test_settle_drops_a_superseded_key_when_this_computer_holds_its_own_on_the_same_connection(tmp_path):
    store = _store(tmp_path, ("exp_C1_old", "express"), ("exp_C1_mine", "express"), ("exp_M1_x", "folder"))
    old, mine, mrerp = (record.profile_id for record in store.profiles)
    assert device_link.settle(_reload(store), old, "superseded") == "removed"
    assert [r.profile_id for r in _reload(store).profiles] == [mine, mrerp]
    assert device_link.settle(_reload(store), mine, "superseded") == "marked"   # 再没有别的：标出来等重新登记
    assert device_link.settle(_reload(store), mrerp, "claimed_elsewhere") == "marked"
    states = {r.profile_id: r.device_state for r in _reload(store).profiles}
    assert states == {mine: "superseded", mrerp: "claimed_elsewhere"}
    assert device_link.settle(_reload(store), "gone", "superseded") == "gone"
    assert device_link.settle(_reload(store), mine, "attached") == "gone"


def test_a_stopped_member_shows_attention_and_the_page_sees_why(tmp_path):
    store = _store(tmp_path, ("exp_C1_secret", "express"))
    member = store.profiles[0].profile_id
    store.set_device_state(member, "claimed_elsewhere")
    settings = DesktopSettings(load_store=lambda: _reload(store), states=lambda: {member: ("idle", "")},
                               device=lambda: {"key": "K" * 43, "name": "PC1"})
    snap = settings.snapshot()
    assert snap["device"] == {"key": "K" * 43, "name": "PC1"}
    assert snap["members"][0]["status"] == "attention"
    assert snap["members"][0]["device_state"] == "claimed_elsewhere"
    runtime = _reload(store).runtime_configs(include_paused=True)[0]
    assert runtime.device_state == "claimed_elsewhere" and not member_active(runtime)


def test_pairing_a_new_key_clears_the_stop(tmp_path, monkeypatch):
    store = _store(tmp_path, ("exp_M1_old", "folder"))
    member = store.profiles[0].profile_id
    store.set_device_state(member, "claimed_elsewhere")

    class _Folder:
        def __init__(self, *args, **kwargs):
            pass

        def sync(self, body):
            return {"ok": True, "adapter": "mrerp", "label": "MR.ERP"}

        def close(self):
            pass

    monkeypatch.setattr("src.companion.folder_client.FolderClient", _Folder)
    result = pairing.pair_folder(token="exp_M1_new@testcowork.pearnly.com", enable_autostart=False,
                                 config=_reload(store).runtime_config(member), profile_store=_reload(store))
    assert result.ok
    record = _reload(store).profiles[0]
    assert record.device_state == "" and _reload(store).runtime_config(member).bearer_token == "exp_M1_new"
    assert record.pool is False   # MR.ERP 成员不是 Express 池的


def test_pairing_again_with_the_same_key_keeps_the_stop(tmp_path, monkeypatch):
    # 留空配对码 = 沿用原密钥：服务端说过这把不能再用，重配一次也还是不能用，标记不清（也就不会悄悄又干起活来）
    store = _store(tmp_path, ("exp_C1_pool", "pool"))
    member = store.profiles[0].profile_id
    store.set_device_state(member, "claimed_elsewhere")

    class _Folder:
        def __init__(self, *args, **kwargs):
            pass

        def sync(self, body):
            return {"ok": True, "adapter": "express", "label": ""}

        def close(self):
            pass

    monkeypatch.setattr("src.companion.folder_client.FolderClient", _Folder)
    result = pairing.pair_folder(token="", enable_autostart=False, pool=True,
                                 config=_reload(store).runtime_config(member), profile_store=_reload(store))
    assert result.ok and _reload(store).profiles[0].device_state == "claimed_elsewhere"


def test_a_new_key_makes_the_computer_say_hello_again_right_away():
    clock = _Clock()
    announcer, sent = _announcer([{"ok": True, "status": "claimed_elsewhere"}], clock)
    assert announcer.announce(_cfg()) == "claimed_elsewhere"
    cfg = _cfg(token="exp_C1_new", device_state="claimed_elsewhere")
    previous = SimpleNamespace(bearer_token="exp_C1_secret", device_state="claimed_elsewhere", paired=True)
    real = device_link.ANNOUNCER
    device_link.ANNOUNCER = announcer
    try:
        assert pairing._device_state_after(previous, "exp_C1_secret", "exp_C1_secret") == "claimed_elsewhere"
        assert "exp_C1_secret" in announcer._memory
        assert pairing._device_state_after(previous, "exp_C1_new", "exp_C1_secret") == ""
        assert "exp_C1_secret" not in announcer._memory   # 旧密钥的记录也忘掉
    finally:
        device_link.ANNOUNCER = real
    cfg.device_state = ""
    assert announcer.announce(cfg) == "attached" and len(sent) == 2


def test_a_cleared_stop_on_the_same_key_is_told_again_not_trusted_from_memory():
    clock = _Clock()
    announcer, sent = _announcer([{"ok": True, "status": "claimed_elsewhere"}], clock)
    cfg = _cfg()
    assert announcer.announce(cfg) == "claimed_elsewhere"
    cfg.device_state = ""   # 标记被清掉了（不是换密钥）：内容没变也要再问服务端，不能悄悄干活
    assert announcer.announce(cfg) == "attached" and len(sent) == 2


def test_an_attached_computer_says_hello_again_twice_a_day_even_if_nothing_changed():
    clock = _Clock()
    announcer, sent = _announcer([], clock)
    assert announcer.announce(_cfg()) == "attached"
    clock.now = device_link.REFRESH_SECONDS - 1
    assert announcer.announce(_cfg()) is None
    clock.now = device_link.REFRESH_SECONDS + 1
    assert announcer.announce(_cfg()) == "attached" and len(sent) == 2


# ── 设备密钥不悄悄换 ──

class _Backup:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.writes = 0

    def read(self):
        return dict(self.data)

    def write(self, data):
        self.writes += 1
        self.data = dict(data)


def _with_backup(monkeypatch, backup, machine=""):
    from src.companion import device_key_backup

    monkeypatch.setattr(device_key_backup, "default_backup", lambda: backup)
    monkeypatch.setattr(device_key_backup, "machine_id", lambda: machine)


def test_the_device_key_is_copied_to_the_registry_and_comes_back_after_an_older_version_dropped_it(tmp_path,
                                                                                                    monkeypatch):
    backup = _Backup()
    _with_backup(monkeypatch, backup, machine="guid-a")
    store = _store(tmp_path, ("exp_C1_secret", "express"))
    key = store.device_key()
    assert backup.data["key"].startswith("test:") and backup.data["machine"] == "guid-a"
    document = json.loads(store.path.read_text(encoding="utf-8"))
    assert document["device"] == {"key": backup.data["key"], "machine": "guid-a"}
    document.pop("device")   # 降级到 1.6 再升级：旧版重写文件丢了这一键
    store.path.write_text(json.dumps(document), encoding="utf-8")
    assert _reload(store).device_key() == key
    assert json.loads(store.path.read_text(encoding="utf-8"))["device"]["key"] == backup.data["key"]


def test_an_unreadable_device_key_is_replaced_instead_of_kept_forever(tmp_path, monkeypatch):
    backup = _Backup()
    _with_backup(monkeypatch, backup)
    store = _store(tmp_path, ("exp_C1_secret", "express"))
    document = json.loads(store.path.read_text(encoding="utf-8"))
    document["device"] = {"key": "other-user:abc"}   # 另一个 Windows 用户的 DPAPI 密文：解不开
    store.path.write_text(json.dumps(document), encoding="utf-8")
    key = _reload(store).device_key()
    assert SERVER_DEVICE_KEY.fullmatch(key)
    assert _reload(store).device_key() == key and backup.data["key"] != "other-user:abc"


def test_a_cloned_computer_gets_its_own_device_key(tmp_path, monkeypatch):
    backup = _Backup()
    _with_backup(monkeypatch, backup, machine="guid-a")
    store = _store(tmp_path, ("exp_C1_secret", "express"))
    original = store.device_key()
    _with_backup(monkeypatch, _Backup(backup.data), machine="guid-b")   # 同一个镜像克隆出来、sysprep 过的另一台
    clone = _reload(store).device_key()
    assert clone != original
    assert json.loads(store.path.read_text(encoding="utf-8"))["device"]["machine"] == "guid-b"
    assert _reload(store).device_key() == clone


def test_a_computer_whose_system_id_cannot_be_read_keeps_its_key(tmp_path, monkeypatch):
    backup = _Backup()
    _with_backup(monkeypatch, backup, machine="guid-a")
    store = _store(tmp_path, ("exp_C1_secret", "express"))
    key = store.device_key()
    _with_backup(monkeypatch, backup, machine="")
    assert _reload(store).device_key() == key


# ── 断开连接 ──

def test_disconnect_revokes_this_key_first_then_removes_the_member(tmp_path):
    store = _store(tmp_path, ("exp_C1_secret", "express"), ("exp_M1_x", "folder"))
    member = store.profiles[0].profile_id
    calls = []
    settings = DesktopSettings(load_store=lambda: _reload(store),
                               leave=lambda url, token: calls.append((url, token, len(_reload(store).profiles))) or True)
    assert settings.disconnect(member) == {"ok": True, "left": True, "profiles_changed": True}
    assert calls == [("https://testcowork.pearnly.com", "exp_C1_secret", 2)]   # 先吊销，再删
    assert [r.endpoint_id for r in _reload(store).profiles] == ["M1"]


def test_disconnect_on_an_old_server_only_removes_the_member(tmp_path):
    store = _store(tmp_path, ("exp_C1_secret", "express"))
    member = store.profiles[0].profile_id
    settings = DesktopSettings(load_store=lambda: _reload(store), leave=lambda url, token: False)
    assert settings.disconnect(member)["left"] is False and _reload(store).profiles == ()


def test_switching_to_another_company_leaves_every_old_member_first(tmp_path, monkeypatch):
    store = _store(tmp_path, ("exp_C1_secret", "express"), ("exp_M1_x", "folder"))
    left = []
    settings = DesktopSettings(load_store=lambda: _reload(store), leave=lambda url, token: left.append(token) or True)
    monkeypatch.setattr(desktop_settings.pairing_flow, "run", lambda *args, **kwargs: SimpleNamespace(ok=True))
    result = settings.pair("exp_X1_new@other.pearnly.com", "zh", switch=True)
    assert result["ok"] and sorted(left) == ["exp_C1_secret", "exp_M1_x"]


# ── 换掉成员的旧密钥前先拿它认一次电脑（第 6 批审查第 5 条） ──

def test_replacing_a_members_key_lets_the_old_key_say_hello_first(tmp_path, monkeypatch):
    """升级到 1.7.0 后对话页的登记先于旧槽位密钥认电脑：配对换钥前先拿旧钥认一次，服务端才会吊销它、把旧电脑并进这台。
    被服务端说过不能再用的（device_state）、同一把密钥都不发。"""
    store = _store(tmp_path, ("exp_C1_old", "express"), ("exp_C2_gone", "express"))
    store.set_device_state(store.profiles[1].profile_id, "claimed_elsewhere")
    settled, order = [], []
    settings = DesktopSettings(load_store=lambda: _reload(store),
                               settle_old=lambda url, token, **kw: settled.append((url, token, kw)) or order.append("hello")
                               or "superseded")
    monkeypatch.setattr(desktop_settings.pairing_flow, "run",
                        lambda *args, **kwargs: order.append("pair") or SimpleNamespace(ok=True))
    assert settings.pair("exp_C1_new@testcowork.pearnly.com", "zh")["ok"]
    assert settled == [("https://testcowork.pearnly.com", "exp_C1_old", {})]
    assert order == ["hello", "pair"]
    settings.pair("exp_C2_new@testcowork.pearnly.com", "zh")   # 被说过不能再用：服务端已处理过，不发
    settings.pair("exp_C1_old@testcowork.pearnly.com", "zh")   # 同一把密钥：不发
    settings.pair("exp_C9_new@testcowork.pearnly.com", "zh")   # 新成员：没有旧密钥
    assert len(settled) == 1


def test_a_failing_old_key_hello_never_blocks_pairing(tmp_path, monkeypatch):
    store = _store(tmp_path, ("exp_C1_old", "express"))

    def boom(url, token, **kw):
        raise RuntimeError("network")

    settings = DesktopSettings(load_store=lambda: _reload(store), settle_old=boom)
    monkeypatch.setattr(desktop_settings.pairing_flow, "run", lambda *args, **kwargs: SimpleNamespace(ok=True))
    assert settings.pair("exp_C1_new@testcowork.pearnly.com", "zh") == {"ok": True, "profiles_changed": True}


def test_settle_old_key_sends_hello_with_the_old_key_and_swallows_refusals(monkeypatch):
    key = "k" * 43
    sent = []
    monkeypatch.setattr(device_link, "device_key", lambda *a, **k: key)
    monkeypatch.setattr(device_link.device_facts, "host_names", lambda: ["ACC-01.local"])

    def reply(url, token, body, timeout=15.0):
        sent.append((url, token, body))
        return {"ok": True, "status": device_link.SUPERSEDED}

    monkeypatch.setattr(device_link, "hello", reply)
    assert device_link.settle_old_key("https://t.pearnly.com", "exp_C1_old") == device_link.SUPERSEDED
    assert sent[0][:2] == ("https://t.pearnly.com", "exp_C1_old")
    assert sent[0][2]["device_key"] == key and "always_on" not in sent[0][2]
    assert device_link.settle_old_key("", "exp_C1_old") == "" and device_link.settle_old_key("https://t", "x") == ""
    assert len(sent) == 1

    def refused(url, token, body, timeout=15.0):
        raise device_link.OldServer("401")

    monkeypatch.setattr(device_link, "hello", refused)
    assert device_link.settle_old_key("https://t.pearnly.com", "exp_C1_old") == ""


# ── 没有 Express 也能加入池 ──

def test_a_computer_without_express_joins_the_express_pool_as_a_read_only_member(tmp_path, monkeypatch):
    store = _store(tmp_path)

    class _Folder:
        def __init__(self, *args, **kwargs):
            pass

        def sync(self, body):
            return {"ok": True, "adapter": "express", "label": ""}

        def close(self):
            pass

    monkeypatch.setattr("src.companion.folder_client.FolderClient", _Folder)
    detected = []
    settings = DesktopSettings(load_store=lambda: _reload(store))
    monkeypatch.setattr(desktop_settings, "detect_roots", lambda _store: detected.append(True) or [])
    assert settings.pair("exp_C1_pool@testcowork.pearnly.com", "zh") == {"ok": True, "profiles_changed": True}
    assert detected == [True]   # 先找过 Express，找不到才按只读成员加入
    record = _reload(store).profiles[0]
    assert (record.kind, record.endpoint_id, record.endpoint_name) == ("folder", "C1", "Express")
    assert record.pool is True   # 以后找到 Express 只按这个标记升级


def test_an_express_code_still_needs_express_for_an_existing_writer(tmp_path, monkeypatch):
    store = _store(tmp_path, ("exp_C1_secret", "express"))
    member = store.profiles[0]
    monkeypatch.setattr(pairing, "connection_kind", lambda code, cfg=None: "express")
    monkeypatch.setattr(pairing_flow, "member_roots", lambda cfg: [])
    result = pairing_flow.run(_reload(store), "exp_C1_new@testcowork.pearnly.com", roots=[],
                              profile_id=member.profile_id)
    assert result.ok is False and result.error_key == pairing_flow.NEED_ROOTS


def test_finding_express_later_turns_a_read_only_pool_member_into_a_writer(tmp_path, monkeypatch):
    store = _store(tmp_path, ("exp_C1_pool", "pool"), ("exp_M1_x", "folder"), ("exp_C2_gone", "pool"))
    store.set_device_state(store.profiles[2].profile_id, "claimed_elsewhere")

    def no_network(code, cfg=None):
        raise AssertionError("upgrading reads the pool mark, it never asks the server")

    monkeypatch.setattr(pairing, "connection_kind", no_network)
    ran = []
    monkeypatch.setattr(pairing_flow, "run", lambda st, code, **kw: ran.append((code, kw["profile_id"], kw["roots"]))
                        or SimpleNamespace(ok=True))
    settings = DesktopSettings(load_store=lambda: _reload(store))
    assert settings._use_roots(["S:\\2569\\EXP69"], "zh") == {"ok": True, "profiles_changed": True}
    # 只动 Express 池的那一个、用它原来的密钥；MR.ERP 成员与被说过不能再用的密钥都不动（断网时也不会把 MR.ERP 当 Express）
    assert ran == [("", store.profiles[0].profile_id, ["S:\\2569\\EXP69"])]
