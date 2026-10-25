"""已经不在的 Express 数据根（测试机 10-25：C:\\PearnlyExpress\\71EXP、72EXP 早删了，每次配对仍报「授权数据根暂时无法读取」）。

给成员定数据根之前一律过 express_machine.root_gone（express_machine.live_roots / prune_member）：所在盘 / 共享访问得到、
文件夹却不在的去掉；服务器关着、网络盘没连上（scan_pace.GATE 问不通）的照旧保留。三条路径：配对 / 换配对码、断开后重连、
心跳里的定时重扫。用临时目录当本机数据根，``\\\\server\\ACC\\69EXP`` 当这会儿关着的服务器（关卡换成不碰网络的替身）。
"""
import base64
from types import SimpleNamespace

import pytest

from src.companion import express_machine, pairing_flow, scan_pace, worker
from src.companion.account_probe import AccountSet
from src.companion.desktop_settings import DesktopSettings
from src.companion.pairing import PairResult
from src.companion.profile_store import ProfileStore, ProfileStoreError

OFFLINE = r"\\server\ACC\69EXP"   # 服务器这会儿关着：暂时访问不到，不算没了


class _Protector:
    prefix = "test:"

    def protect(self, value: str) -> str:
        return self.prefix + base64.b64encode(value.encode()).decode()

    def unprotect(self, value: str) -> str:
        if not value.startswith(self.prefix):
            raise ProfileStoreError("bad test ciphertext")
        return base64.b64decode(value[len(self.prefix):]).decode()


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """70EXP 在；71EXP、72EXP 所在盘在、文件夹不在（确实没了）；服务器 \\\\server 问不通。"""
    monkeypatch.setattr(scan_pace, "GATE", scan_pace.HostGate(lambda path: "server" not in path.lower()))
    monkeypatch.setenv("PEARNLY_FOLDER_STATE_DIR", str(tmp_path / "folders"))
    books = tmp_path / "PearnlyExpress"
    (books / "70EXP" / "ACME").mkdir(parents=True)
    return SimpleNamespace(books=str(books), y70=str(books / "70EXP"), y71=str(books / "71EXP"),
                           y72=str(books / "72EXP"), acme=str(books / "70EXP" / "ACME"))


def _load(path):
    return ProfileStore.load(path, protector=_Protector())


def _member(store, token, primary, authorized, account_dir):
    cfg = store.new_runtime_config()
    cfg.update(backend_url="https://testcowork.pearnly.com", bearer_token=token, account_dir=account_dir,
               account_set=account_dir, account_company="Acme Co.", express_root=primary,
               authorized_roots=list(authorized), paired=True, ui_lang="zh")
    store.save_profile(cfg)
    return cfg.profile_id


def _store(tmp_path, roots, *members):
    store = _load(tmp_path / "companion.json")
    ids = [_member(store, token, primary, authorized, roots.acme) for token, primary, authorized in members]
    store.set_places([roots.books, r"\\server\ACC"])
    return _load(store.path), ids


def _fake_pairing(monkeypatch, roots):
    """配对时不连网：读账套名单、ISINFO 默认科目、连 Pearnly 校验都换成替身，记下要写进成员的数据根。"""
    seen = {}
    monkeypatch.setattr(pairing_flow.pairing, "connection_kind", lambda token, cfg: "express")
    monkeypatch.setattr(pairing_flow, "probe_account_sets", lambda root: [
        AccountSet(code="A", name="ACME", company="Acme Co.", path=roots.acme, tax_id="", writable=True, row=1)])
    monkeypatch.setattr(pairing_flow, "probe_default_accounts", lambda path: {})
    monkeypatch.setattr(pairing_flow.pairing, "pair",
                        lambda **kwargs: seen.update(kwargs) or PairResult(True))
    return seen


def _settings(store):
    return DesktopSettings(load_store=lambda: _load(store.path), settle_old=lambda url, token, **kw: "",
                           leave=lambda url, token: True)


# ── 配对 / 换配对码 ──

def test_a_new_member_does_not_copy_a_data_root_that_is_gone(tmp_path, monkeypatch, roots):
    store, _ids = _store(tmp_path, roots, ("exp_endpoint-1_secret", roots.y70, [roots.y70, roots.y71, OFFLINE]))
    seen = _fake_pairing(monkeypatch, roots)

    result = _settings(store).pair("exp_endpoint-2_secret@testcowork.pearnly.com", "zh")

    assert result == {"ok": True, "profiles_changed": True}
    assert seen["express_root"] == roots.y70
    assert seen["authorized_roots"] == [roots.y70, OFFLINE]   # 71EXP 没了不带；关着的服务器照旧授权


def test_changing_the_key_drops_a_gone_root_and_the_next_root_becomes_primary(tmp_path, monkeypatch, roots):
    store, (member,) = _store(tmp_path, roots, ("exp_endpoint-1_secret", roots.y71, [roots.y71, roots.y70, OFFLINE]))
    seen = _fake_pairing(monkeypatch, roots)

    pairing_flow.run(store, "exp_endpoint-1_new@testcowork.pearnly.com", roots=[], profile_id=member, lang="zh")

    assert seen["express_root"] == roots.y70   # 主根 71EXP 没了：主根在前的下一个
    assert seen["authorized_roots"] == [roots.y70, OFFLINE]


def test_pairing_keeps_the_roots_when_every_one_is_gone(tmp_path, monkeypatch, roots):
    store, _ids = _store(tmp_path, roots, ("exp_endpoint-1_secret", roots.y70, [roots.y70]))
    probed = []
    monkeypatch.setattr(pairing_flow.pairing, "connection_kind", lambda token, cfg: "express")
    monkeypatch.setattr(pairing_flow, "probe_account_sets", lambda root: probed.append(root) or [])

    assert pairing_flow.run(store, "exp_e_s@testcowork.pearnly.com", roots=[roots.y71, roots.y72], lang="zh") is None
    assert probed == [roots.y71]   # 一个都不剩：原样交回（不弄成没有数据根），照旧读不到账套


# ── 断开后重连 ──

def test_reconnecting_after_disconnect_leaves_out_a_kept_root_that_is_gone(tmp_path, monkeypatch, roots):
    (tmp_path / "PearnlyExpress" / "71EXP").mkdir()
    store, (member,) = _store(tmp_path, roots, ("exp_endpoint-1_secret", roots.y70, [roots.y70, roots.y71, OFFLINE]))
    monkeypatch.setattr(express_machine, "places_roots", lambda places: [roots.y70, roots.y71])
    settings = _settings(store)
    assert settings.disconnect(member)["ok"] is True
    assert settings._kept == [roots.y70, roots.y71, OFFLINE]
    assert settings.detect() == [roots.y70, roots.y71, OFFLINE]   # 断开前授权的照旧（这会儿 71EXP 还在）

    (tmp_path / "PearnlyExpress" / "71EXP").rmdir()   # 之后 71EXP 被删了，再连接
    seen = _fake_pairing(monkeypatch, roots)
    assert settings.pair("exp_endpoint-1_secret@testcowork.pearnly.com", "zh") == {"ok": True,
                                                                                   "profiles_changed": True}
    assert seen["authorized_roots"] == [roots.y70, OFFLINE]


def test_reconnecting_with_no_detection_yet_also_checks_the_kept_roots(tmp_path, monkeypatch, roots):
    store, (member,) = _store(tmp_path, roots, ("exp_endpoint-1_secret", roots.y70, [roots.y70, roots.y71, OFFLINE]))
    monkeypatch.setattr(express_machine, "places_roots", lambda places: [roots.y70])
    settings = _settings(store)
    settings.disconnect(member)
    seen = _fake_pairing(monkeypatch, roots)

    assert settings.pair("exp_endpoint-1_secret@testcowork.pearnly.com", "zh")["ok"] is True
    assert seen["express_root"] == roots.y70 and seen["authorized_roots"] == [roots.y70, OFFLINE]


# ── 心跳里的定时重扫 ──

def test_a_full_scan_drops_gone_roots_from_every_member_and_keeps_offline_ones(tmp_path, monkeypatch, roots):
    store, (first, second) = _store(tmp_path, roots,
                                    ("exp_endpoint-1_secret", roots.y70, [roots.y70, roots.y71, OFFLINE]),
                                    ("exp_endpoint-2_secret", roots.y70, [roots.y70, OFFLINE, roots.y72]))
    copies = {member: store.runtime_config(member) for member in (first, second)}   # 取任务线程里各成员各持一份副本

    worker._drop_gone_roots(copies[first])

    saved = _load(store.path)
    assert {r.profile_id: list(r.authorized_roots) for r in saved.profiles} == {first: [roots.y70, OFFLINE],
                                                                               second: [roots.y70, OFFLINE]}
    assert {r.express_root for r in saved.profiles} == {roots.y70}
    assert list(copies[first].authorized_roots) == [roots.y70, OFFLINE]
    # 另一个成员手里的旧副本把 72EXP 又存回去：别的成员补数据根时不再把没了的补进来
    store.save_profile(copies[second])
    worker._adopt_machine_roots(copies[first])
    assert list(copies[first].authorized_roots) == [roots.y70, OFFLINE]


def test_a_full_scan_moves_the_primary_to_the_next_root_and_never_empties_a_member(tmp_path, monkeypatch, roots):
    store, (moved, empty) = _store(tmp_path, roots,
                                   ("exp_endpoint-1_secret", roots.y71, [roots.y71, roots.y70, OFFLINE]),
                                   ("exp_endpoint-2_secret", roots.y71, [roots.y71, roots.y72]))

    worker._drop_gone_roots(store.runtime_config(moved))

    saved = _load(store.path)
    cfg = saved.runtime_config(moved)
    assert (cfg.express_root, list(cfg.authorized_roots)) == (roots.y70, [roots.y70, OFFLINE])
    assert cfg.express_exe.endswith("ExpressI.exe") and roots.y70 in cfg.express_exe
    assert cfg.account_dir == roots.acme   # 账套不在这里换
    # 全都没了：保持现状，不把成员弄成没有数据根
    left = saved.runtime_config(empty)
    assert (left.express_root, list(left.authorized_roots)) == (roots.y71, [roots.y71, roots.y72])


def test_the_heartbeat_drops_gone_roots_only_after_a_complete_scan(tmp_path, monkeypatch, roots):
    store, (member,) = _store(tmp_path, roots, ("exp_endpoint-1_secret", roots.y71, [roots.y71, roots.y70, OFFLINE]))
    cfg = store.runtime_config(member)
    snapshots = [{"account_sets": [], "partial": True, "scanned_roots": []}, {"account_sets": []}]
    monkeypatch.setattr(worker.projection_refresh, "collect_endpoint",
                        lambda *args, **kwargs: (snapshots.pop(0), 1.0, ""))
    sent = []
    qc = SimpleNamespace(health=lambda **kwargs: sent.append(kwargs) or {})
    state = {"last_heartbeat": 0.0}

    worker._run_periodic(cfg, qc, state)   # 「进行中」快照：不动
    assert list(_load(store.path).runtime_config(member).authorized_roots) == [roots.y71, roots.y70, OFFLINE]

    state["last_heartbeat"] = 0.0
    worker._run_periodic(cfg, qc, state)   # 完整快照：去掉并存盘，这一拍就报新的主根
    saved = _load(store.path).runtime_config(member)
    assert (saved.express_root, list(saved.authorized_roots)) == (roots.y70, [roots.y70, OFFLINE])
    assert len(sent) == 2 and sent[-1]["selected"]["express_root"] == roots.y70


def test_the_heartbeat_check_does_not_wait_for_a_server_in_backoff(monkeypatch):
    """心跳在取任务线程上：所在服务器在退避期内的不去问（不等），按暂时访问不到保留；手动查找照旧马上再问一次，
    同一次查找里同一台服务器只问一次。"""
    probes = []
    gate = scan_pace.HostGate(lambda path: probes.append(path) or False)
    monkeypatch.setattr(scan_pace, "GATE", gate)
    gate.fail(OFFLINE)   # 刚问过不通，正在退避

    assert express_machine.live_roots([OFFLINE, OFFLINE + r"\..\68EXP"], wait=False) == [OFFLINE,
                                                                                         OFFLINE + r"\..\68EXP"]
    assert probes == []
    gone = express_machine.gone_test({})
    assert gone(OFFLINE) is False and gone(OFFLINE + r"\..\68EXP") is False
    assert len(probes) == 1


def test_a_member_with_one_root_or_nothing_gone_is_left_alone(roots):
    keep = SimpleNamespace(express_root=roots.y70, account_dir=roots.acme, authorized_roots=[roots.y70, OFFLINE])
    alone = SimpleNamespace(express_root=roots.y71, account_dir="", authorized_roots=[roots.y71])
    assert express_machine.prune_member(keep) == {}
    assert express_machine.prune_member(alone) == {}
