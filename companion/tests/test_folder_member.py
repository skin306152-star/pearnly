"""MR.ERP 的小助手成员（只读文件夹，定时任务，2026-10-09）：配对时问服务端连接类型，存成 folder 成员，
不进取任务线程、不算 Express 数据根，状态由文件夹线程每次同步报。"""

import base64
from types import SimpleNamespace

from companion import express_machine, folder_client, folder_store, pairing
from companion.folder_worker import FolderIntake
from companion.profile_store import ProfileRecord, ProfileStore

CODE = "exp_CWC-abc_" + "s" * 43 + "@testcowork.pearnly.com"


class _Protector:
    def protect(self, value: str) -> str:
        return "test:" + base64.b64encode(value.encode()).decode()

    def unprotect(self, value: str) -> str:
        return base64.b64decode(value[5:]).decode()


class _Client:
    replies = []
    calls = []

    def __init__(self, url, token, timeout=30.0):
        self.url, self.token = url, token

    def sync(self, body):
        _Client.calls.append(body)
        reply = _Client.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def close(self):
        pass


def _store(tmp_path):
    return ProfileStore.load(tmp_path / "companion.json", protector=_Protector())


def test_mrerp_code_pairs_as_a_folder_only_member(tmp_path, monkeypatch):
    monkeypatch.setattr(folder_client, "FolderClient", _Client)
    monkeypatch.setattr(pairing.autostart, "enable", lambda: None)
    _Client.calls = []
    _Client.replies = [{"ok": True, "adapter": "mrerp", "label": "MR.ERP"}] * 2
    store = _store(tmp_path)
    cfg = store.new_runtime_config()
    assert pairing.connection_kind(CODE, cfg) == "mrerp"
    result = pairing.pair_folder(token=CODE, config=cfg, profile_store=store)
    assert result.ok and result.company == "MR.ERP"
    assert all(call == {"probe": True} for call in _Client.calls)  # 只问身份，不报根目录
    saved = ProfileStore.load(tmp_path / "companion.json", protector=_Protector())
    record = saved.profiles[0]
    assert record.kind == "folder" and record.endpoint_name == "MR.ERP" and not record.account_dir
    runtime = saved.runtime_config(record.profile_id)
    assert runtime.kind == "folder" and runtime.backend_url == "https://testcowork.pearnly.com"
    assert folder_store.connection_key(runtime)
    # 没有 Express 数据：不算这台电脑的 Express 数据根，「更换…」也不改它
    assert express_machine.express_members(saved) == []
    assert express_machine.machine_roots(saved) == []


def test_express_code_or_an_unreachable_server_keeps_the_express_pairing(monkeypatch):
    monkeypatch.setattr(folder_client, "FolderClient", _Client)
    _Client.replies = [{"ok": True, "adapter": "express"}, folder_client.Refused("404"), {"ok": True}]
    cfg = SimpleNamespace(paired=False)
    assert pairing.connection_kind(CODE, cfg) == "express"
    assert pairing.connection_kind(CODE, cfg) == "express"  # 旧服务端 / 拒绝：按 Express 照旧配对
    assert pairing.connection_kind(CODE, cfg) == "express"  # 旧服务端不回 adapter
    assert pairing.connection_kind("not-a-code", cfg) == "express"


def test_a_code_that_is_not_mrerp_is_refused_as_a_folder_member(tmp_path, monkeypatch):
    monkeypatch.setattr(folder_client, "FolderClient", _Client)
    _Client.replies = [{"ok": True, "adapter": "express"}]
    store = _store(tmp_path)
    result = pairing.pair_folder(token=CODE, config=store.new_runtime_config(), profile_store=store)
    assert not result.ok and result.error_key == "pair_err_rejected" and not store.profiles


def test_folder_records_need_no_account_set_but_express_records_still_do():
    base = {"profile_id": "p", "token_ciphertext": "x", "account_dir": "", "account_set": "", "account_company": ""}
    assert ProfileRecord.from_dict({**base, "kind": "folder"}).kind == "folder"
    try:
        ProfileRecord.from_dict(base)
    except Exception as error:
        assert "incomplete" in str(error)
    else:
        raise AssertionError("an Express member without an account set must not load")


def test_folder_member_syncs_without_folders_and_reports_its_status(tmp_path, monkeypatch):
    monkeypatch.setenv("PEARNLY_FOLDER_STATE_DIR", str(tmp_path / "folders"))
    statuses, bodies = [], []

    class Client(_Client):
        def sync(self, body):
            bodies.append(body)
            return {"ok": True, "server_time": 0, "rules": [], "entries": {}, "adapter": "mrerp"}

    member = SimpleNamespace(profile_id="m1", kind="folder", paused=False, paired=True,
                             backend_url="https://testcowork.pearnly.com", bearer_token=CODE.split("@")[0], endpoint_id="")
    express = SimpleNamespace(profile_id="e1", kind="express", paused=False, paired=True,
                              backend_url="https://testcowork.pearnly.com", bearer_token="exp_CWC-x_" + "t" * 43, endpoint_id="")
    def report(pid, key):
        statuses.append((pid, key))
        intake.stop()  # 一轮就停

    intake = FolderIntake(lambda: [express, member], client_factory=Client, on_status=report)
    assert intake.wanted()  # 只有 MR.ERP 成员、还没加文件夹也要同步
    intake._loop()
    # MR.ERP 成员没文件夹也同步（网页据此知道小助手已配好）；Express 成员没文件夹只报一次空的（云端不留旧文件夹），之后不再报
    assert len(bodies) == 2 and all(body["roots"] == [] for body in bodies)
    assert [cfg.profile_id for cfg in (express, member) if intake._due(cfg, folder_store.connection_key(cfg))] == ["m1"]
    assert statuses == [("m1", "idle")]  # Express 成员的状态仍由取任务线程报
