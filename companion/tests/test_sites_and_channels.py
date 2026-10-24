# -*- coding: utf-8 -*-
"""CC 桌面版 1.11.0（ADR-025）：同一台电脑同时连一个 Cowork 站点和一个 ERP 站点。

- 通道按密钥前缀认：``exp_`` 走 Cowork 的路径，``pcc_`` 走共用机器通道（pearnly_core.cc_desktop.machine.*）；请求体不变；
- 每个成员记自己的站点：旧文件（成员没记站点）读的时候取全局那一个；连 ERP 不改 Cowork 成员的站点；
- 同一种通道换站点才要「改连」，ERP 与 Cowork 互不影响；文件夹自动录入只给 Cowork 成员；
- 写一份账套期间锁住账套目录里的锁文件，另一处锁不到 → 按「等账套」回报，不写。
"""

from __future__ import annotations

import base64
import json
import types
from unittest import mock

import pytest

from companion import channel, desktop_settings, pairing, worker
from companion.account_write_lock import Busy, held, LOCK_NAME
from companion.desktop_settings import DesktopSettings
from companion.profile_store import ProfileStore, ProfileStoreError
from companion.queue_client import QueueClient, QueueClientConfig
from companion.request_client import RequestClient

COWORK_TOKEN = "exp_endpoint-1_secret"
ERP_TOKEN = "pcc_PCD-0123456789abcdef0123_" + "s" * 43


class _Protector:
    prefix = "test:"

    def protect(self, value):
        return self.prefix + base64.b64encode(value.encode()).decode()

    def unprotect(self, value):
        if not value.startswith(self.prefix):
            raise ProfileStoreError("bad test ciphertext")
        return base64.b64decode(value[len(self.prefix):]).decode()


def test_the_token_prefix_picks_the_paths():
    assert channel.of_token(COWORK_TOKEN) == channel.COWORK and channel.of_token(ERP_TOKEN) == channel.CORE
    assert channel.path(COWORK_TOKEN, "lease") == "/api/method/pearnly_cowork.companion.queue_api.lease"
    assert channel.path(ERP_TOKEN, "lease") == "/api/method/pearnly_core.cc_desktop.machine.lease"
    assert channel.path(ERP_TOKEN, "hello") == ""  # 认电脑只有 Cowork
    assert channel.folders(COWORK_TOKEN) and not channel.folders(ERP_TOKEN)
    queue = QueueClient(QueueClientConfig(backend_url="https://testerp.pearnly.com", bearer_token=ERP_TOKEN,
                                          companion_id="c"))
    requests = RequestClient("https://testerp.pearnly.com", ERP_TOKEN)
    try:
        assert queue._paths == {op: channel.PATHS[channel.CORE][op] for op in ("heartbeat", "lease", "ack")}
        assert (requests._next, requests._complete) == (channel.PATHS[channel.CORE]["request_next"],
                                                        channel.PATHS[channel.CORE]["request_complete"])
    finally:
        queue.close()
        requests.close()


def test_the_pairing_code_of_an_erp_site_is_accepted():
    token, site = pairing.split_pairing_code(f"{ERP_TOKEN}@testerp.pearnly.com")
    assert (token, site) == (ERP_TOKEN, "https://testerp.pearnly.com")
    assert pairing._endpoint_id_from_token(ERP_TOKEN) == "PCD-0123456789abcdef0123"
    assert pairing.split_pairing_code("xyz_1_secret@testerp.pearnly.com") == ("", "")


def _member(store, token, site, account=r"S:\\EXP68\\ACME"):
    cfg = store.new_runtime_config()
    cfg.update(backend_url=site, bearer_token=token, account_dir=account, account_set=account,
               account_company="Acme Co.", express_root=r"S:\\EXP68", authorized_roots=[r"S:\\EXP68"],
               pearnly_account="owner@example.com", paired=True)
    store.save_profile(cfg)
    return cfg.profile_id


def test_each_member_keeps_its_own_site(tmp_path):
    store = ProfileStore.load(tmp_path / "companion.json", protector=_Protector())
    cowork = _member(store, COWORK_TOKEN, "https://testcowork.pearnly.com")
    erp = _member(store, ERP_TOKEN, "https://testerp.pearnly.com")
    again = ProfileStore.load(store.path, protector=_Protector())
    sites = {cfg.profile_id: (cfg.backend_url, cfg.channel) for cfg in again.runtime_configs()}
    assert sites == {cowork: ("https://testcowork.pearnly.com", "cowork"), erp: ("https://testerp.pearnly.com", "core")}


def test_an_old_file_takes_the_global_site_for_its_members(tmp_path):
    store = ProfileStore.load(tmp_path / "companion.json", protector=_Protector())
    member = _member(store, COWORK_TOKEN, "https://testcowork.pearnly.com")
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    for profile in raw["profiles"]:  # 1.10 及更早写的文件：成员没有自己的站点与通道
        profile.pop("backend_url")
        profile.pop("channel")
    raw["global"]["backend_url"] = "https://testcowork.pearnly.com"
    store.path.write_text(json.dumps(raw), encoding="utf-8")
    again = ProfileStore.load(store.path, protector=_Protector())
    cfg = again.runtime_config(member)
    assert (cfg.backend_url, cfg.channel) == ("https://testcowork.pearnly.com", "cowork")


def _settings(store, **kwargs):
    kwargs.setdefault("settle_old", lambda url, token, **kw: "")
    return DesktopSettings(load_store=lambda: ProfileStore.load(store.path, protector=_Protector()), **kwargs)


def test_an_erp_site_joins_next_to_the_cowork_site(tmp_path, monkeypatch):
    monkeypatch.setenv("PEARNLY_FOLDER_STATE_DIR", str(tmp_path / "folders"))
    store = ProfileStore.load(tmp_path / "companion.json", protector=_Protector())
    _member(store, COWORK_TOKEN, "https://testcowork.pearnly.com")
    settings = _settings(store)
    paired = []
    monkeypatch.setattr(DesktopSettings, "_pair", lambda self, store, code, lang, profile_id: paired.append(code)
                        or {"ok": True})
    # 换成另一个 Cowork 站点：要用户点「改连」
    assert settings.pair("exp_endpoint-9_other@othercowork.pearnly.com", "zh") == {
        "ok": False, "other_site": "testcowork.pearnly.com"}
    # ERP 站点（另一种通道）：直接配，不动 Cowork 那个成员
    assert settings.pair(f"{ERP_TOKEN}@testerp.pearnly.com", "zh") == {"ok": True}
    assert paired == [f"{ERP_TOKEN}@testerp.pearnly.com"]
    _member(store, ERP_TOKEN, "https://testerp.pearnly.com")
    snap = settings.snapshot()
    assert snap["site"] == "testcowork.pearnly.com"
    assert snap["sites"] == ["testcowork.pearnly.com", "testerp.pearnly.com"]
    assert sorted((m["site"], m["channel"]) for m in snap["members"]) == [
        ("testcowork.pearnly.com", "cowork"), ("testerp.pearnly.com", "core")]
    # 公司文件夹只放在 Cowork 的成员下
    assert desktop_settings.main_member(ProfileStore.load(store.path, protector=_Protector())).channel == "cowork"


def test_a_new_device_on_the_same_erp_site_replaces_the_old_member(tmp_path, monkeypatch):
    monkeypatch.setenv("PEARNLY_FOLDER_STATE_DIR", str(tmp_path / "folders"))
    store = ProfileStore.load(tmp_path / "companion.json", protector=_Protector())
    cowork = _member(store, COWORK_TOKEN, "https://testcowork.pearnly.com")
    old = _member(store, ERP_TOKEN, "https://testerp.pearnly.com")
    left = []
    settings = _settings(store, leave=lambda url, token: left.append((url, token)) or True)
    fresh = "pcc_PCD-ffffffffffffffffffff_" + "t" * 43

    def paired(self, store, code, lang, profile_id):
        _member(store, fresh, "https://testerp.pearnly.com")
        return {"ok": True, "profiles_changed": True}

    monkeypatch.setattr(DesktopSettings, "_pair", paired)
    # 这台电脑的设备密钥换过：对话页登记出一台新设备，同一站点同一种通道只留新配的这个，旧的吊销
    assert settings.pair(f"{fresh}@testerp.pearnly.com", "zh")["ok"] is True
    again = ProfileStore.load(store.path, protector=_Protector())
    members = {cfg.profile_id: cfg.bearer_token for cfg in again.runtime_configs()}
    assert old not in members and cowork in members and fresh in members.values()
    assert left == [("https://testerp.pearnly.com", ERP_TOKEN)]


def test_a_second_writer_of_the_same_account_set_is_turned_away(tmp_path):
    with held(str(tmp_path)):
        assert (tmp_path / LOCK_NAME).exists()
        with pytest.raises(Busy):
            with held(str(tmp_path)):
                pass
    with held(str(tmp_path)):  # 放开后照常能锁
        pass


def test_the_worker_reports_waiting_when_another_computer_writes(monkeypatch):
    acks = []
    qc = types.SimpleNamespace(ack_failure=lambda *a, **kw: acks.append(kw), ack_success=lambda *a, **kw: None)
    settings = types.SimpleNamespace(account_dir=r"S:\\EXP68\\ACME", account_set=r"S:\\EXP68\\ACME",
                                     account_company="Acme Co.", has_selection=lambda: True)
    monkeypatch.setattr(worker, "_task_settings", lambda cfg, payload: settings)
    monkeypatch.setattr(worker, "uid_mismatch", lambda uid, path: False)

    class _Taken:
        def __enter__(self):
            raise Busy("taken")

        def __exit__(self, *exc):
            return False

    task = types.SimpleNamespace(id="job-1", payload={"direction": "purchase", "payload_version": 1}, invoice_no="X")
    with mock.patch("companion.account_write_lock.held", lambda path: _Taken()), \
            mock.patch("companion.dbf_writer.write_purchase", side_effect=AssertionError("must not write")):
        outcome = worker._handle_dbf_task(types.SimpleNamespace(default_depcod="", ui_lang="en"), qc, task)
    assert outcome == "waiting_lock"
    assert [(item["error_code"], item["outcome"]) for item in acks] == [("ACCOUNT_WRITER_BUSY", "waiting_lock")]


def test_an_unfit_stock_account_group_is_reported_for_a_choice():
    # 10-23 验收第 3 轮：新建库存品挂的存货科目组过不了判据 → 单独回报码、直接转人工，产品端让人选一次组
    from companion import dbf_writer, purchase_lines, stock_template

    try:
        try:
            raise stock_template.InventoryAccountUnfit("ST01 不像存货科目")
        except stock_template.InventoryAccountUnfit as cause:
            raise purchase_lines.StockLineError(str(cause)) from cause
    except purchase_lines.StockLineError as wrapped:
        assert dbf_writer._error_code(wrapped) == stock_template.ERR_STOCK_GROUP == "STOCK_ACC_GROUP_UNFIT"
    assert dbf_writer._error_code(purchase_lines.StockLineError("混行")) == dbf_writer.ERR_STOCK_IN
    assert worker._outcome_for("STOCK_ACC_GROUP_UNFIT") == "needs_review"


def test_an_erp_member_reports_only_the_chosen_roots():
    # 1.12.0：选了哪些报哪些（用户 10-24）；同税号在没选的根里的账套也不报（白天服务器上线后会把 S 盘真账带进 ERP）
    rows = [{"root": r"S:\EXP69", "tax_id": "0105551234567", "path": r"S:\EXP69\NIPA"},
            {"root": r"S:\EXP68", "tax_id": "0105551234567", "path": r"S:\EXP68\NIPA"},
            {"root": r"S:\EXP69", "tax_id": "", "path": r"S:\EXP69\NOTAX"},
            {"root": r"S:\EXP68", "tax_id": "0105559999999", "path": r"S:\EXP68\OTHER"}]
    erp = types.SimpleNamespace(channel=channel.CORE, erp_roots=(r"s:\exp69",))
    assert [row["path"] for row in worker.erp_scope(erp, rows)] == [r"S:\EXP69\NIPA", r"S:\EXP69\NOTAX"]
    assert worker.erp_scope(types.SimpleNamespace(channel=channel.CORE, erp_roots=()), rows) == []  # 默认一份都不报
    assert worker.erp_scope(types.SimpleNamespace(channel=channel.COWORK), rows) == rows
    assert worker._outcome_for("PERIOD_LOCKED") == "needs_review"


def test_a_ticked_place_covers_the_year_folders_below_it_including_new_ones():
    # 主会话 10-24：勾「C:\\PearnlyExpress」这一级，明年新建的 71EXP（心跳 discover 自动找进来）照样报；勾位置以外的不报
    from companion.express_machine import erp_places, under

    assert erp_places([r"C:\PearnlyExpress\70EXP", r"S:\2569\EXP69", r"\\accserver\d$\ACCOUNT\69EXP"]) == [
        r"C:\PearnlyExpress", r"S:\2569", "S:\\", r"\\accserver\d$\ACCOUNT"]
    assert under(r"C:\PearnlyExpress\71EXP", r"C:\PearnlyExpress") and not under(r"C:\PearnlyExpress2\71EXP", r"C:\PearnlyExpress")
    rows = [{"root": r"C:\PearnlyExpress\70EXP", "path": "a"}, {"root": r"C:\PearnlyExpress\71EXP", "path": "b"},
            {"root": r"S:\2569\EXP69", "path": "c"}]
    erp = types.SimpleNamespace(channel=channel.CORE, erp_roots=(r"C:\PearnlyExpress",))
    assert [row["path"] for row in worker.erp_scope(erp, rows)] == ["a", "b"]
    only_year = types.SimpleNamespace(channel=channel.CORE, erp_roots=(r"C:\PearnlyExpress\70EXP",))
    assert [row["path"] for row in worker.erp_scope(only_year, rows)] == ["a"]  # 只开年度文件夹：只报它


def test_a_year_folder_found_by_one_member_reaches_every_member_on_the_computer(tmp_path):
    # 10-24 验收问题 16：Cowork 成员「重新读取」找到 71EXP，勾了 C:\PearnlyExpress 的 ERP 成员也要有（数据根是整台电脑的）；
    # 每天的自动查找是同一条路（找新年度的计时整台电脑一份，哪个成员先扫到算哪个）
    store = ProfileStore.load(tmp_path / "companion.json", protector=_Protector())
    cowork_id = _member(store, COWORK_TOKEN, "https://testcowork.pearnly.com", account=r"C:\PearnlyExpress\70EXP\T9")
    erp_id = _member(store, ERP_TOKEN, "https://testerp.pearnly.com", account=r"C:\PearnlyExpress\70EXP\T9")
    store.set_erp_roots(erp_id, [r"C:\PearnlyExpress"])
    cowork, erp = store.runtime_config(cowork_id), store.runtime_config(erp_id)  # 取任务线程里各成员各持一份副本

    worker._remember_roots(cowork, list(cowork.authorized_roots), [r"S:\EXP68", r"C:\PearnlyExpress\71EXP"])

    again = ProfileStore.load(store.path, protector=_Protector())
    assert {record.profile_id: list(record.authorized_roots) for record in again.profiles} == {
        cowork_id: [r"S:\\EXP68", r"C:\PearnlyExpress\71EXP"], erp_id: [r"S:\\EXP68", r"C:\PearnlyExpress\71EXP"]}
    # ERP 成员手里的旧副本下一拍心跳前补上（不补的话它下次存盘会把记录里的 71EXP 盖掉）
    assert list(erp.authorized_roots) == [r"S:\\EXP68"]
    worker._adopt_machine_roots(erp)
    assert list(erp.authorized_roots) == [r"S:\\EXP68", r"C:\PearnlyExpress\71EXP"]
    assert [row["path"] for row in worker.erp_scope(erp, [{"root": r"C:\PearnlyExpress\71EXP", "path": "T0HENG"}])] == ["T0HENG"]
    reloaded = ProfileStore.load(store.path, protector=_Protector())
    assert list(reloaded.runtime_config(erp_id).erp_roots) == [r"C:\PearnlyExpress"]


def test_a_year_folder_already_kept_by_one_member_only_is_added_to_the_others(tmp_path):
    # 10-24 复测：1.12.0 前找到的 71EXP 只记在 Cowork 成员里，已经分开的电脑也要补上（重启 CC 后照样补）
    store = ProfileStore.load(tmp_path / "companion.json", protector=_Protector())
    cowork_id = _member(store, COWORK_TOKEN, "https://testcowork.pearnly.com", account=r"C:\PearnlyExpress\70EXP\T9")
    erp_id = _member(store, ERP_TOKEN, "https://testerp.pearnly.com", account=r"C:\PearnlyExpress\70EXP\T9")
    store.save_profile(store.runtime_config(cowork_id).update(authorized_roots=[r"S:\\EXP68", r"C:\PearnlyExpress\71EXP"]))
    started = ProfileStore.load(store.path, protector=_Protector())  # 重启 CC
    erp = started.runtime_config(erp_id)
    worker._adopt_machine_roots(erp)
    worker._adopt_machine_roots(erp)  # 再一拍不重复加
    assert list(erp.authorized_roots) == [r"S:\\EXP68", r"C:\PearnlyExpress\71EXP"]
    again = ProfileStore.load(store.path, protector=_Protector())
    assert list(again.runtime_config(erp_id).authorized_roots) == [r"S:\\EXP68", r"C:\PearnlyExpress\71EXP"]
    assert list(again.runtime_config(cowork_id).authorized_roots) == [r"S:\\EXP68", r"C:\PearnlyExpress\71EXP"]
