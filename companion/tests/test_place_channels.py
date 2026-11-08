# -*- coding: utf-8 -*-
"""每个「Express 位置」给哪几种成员读（11-07 共用电脑隔离评估，做法 B）。

一台电脑同时连事务所的 Cowork 和某个客户的 ERP（ADR-025）时，位置整机一份，ERP 成员原来会把位置下事务所全部客户的账套
报给这个客户的 ERP（ERP 照着自动建公司）。现在：

- 有 Cowork 成员的电脑，位置默认只给 Cowork（升级时已有的、之后新加的都一样），ERP 要在设置里勾；
- 只有 ERP 成员的电脑两种都给（界面不变；以后再连 Cowork 也读得到）；
- 心跳只扫、只报勾给这个成员的位置，一个都没勾就什么都不报；所选账套不在范围里时不报它的名字与目录；
- 设置页同时有两种成员时每个位置后面摆两个勾；勾掉某种成员仅剩的那个位置不行。
"""

from __future__ import annotations

import base64
import json
import types

from companion import channel, desktop_settings, worker
from companion.desktop_settings import DesktopSettings
from companion.express_places import places_for, settle_channels, shows_channels
from companion.profile_store import ProfileStore, ProfileStoreError

COWORK_TOKEN = "exp_endpoint-1_secret"
ERP_TOKEN = "pcc_PCD-0123456789abcdef0123_" + "s" * 43
FIRM = r"S:\ACCOUNT"
CLIENT = r"C:\PearnlyExpress"


class _Protector:
    prefix = "test:"

    def protect(self, value):
        return self.prefix + base64.b64encode(value.encode()).decode()

    def unprotect(self, value):
        if not value.startswith(self.prefix):
            raise ProfileStoreError("bad test ciphertext")
        return base64.b64decode(value[len(self.prefix):]).decode()


def _load(path):
    return ProfileStore.load(path, protector=_Protector())


def _member(store, token, site, root=FIRM + r"\70EXP"):
    cfg = store.new_runtime_config()
    cfg.update(backend_url=site, bearer_token=token, account_dir=root + r"\ACME", account_set=root + r"\ACME",
               account_company="Acme Co.", express_root=root, authorized_roots=[root], pearnly_account="o@example.com",
               paired=True)
    store.save_profile(cfg)
    return cfg.profile_id


def _settings(store):
    return DesktopSettings(load_store=lambda: _load(store.path), settle_old=lambda url, token, **kw: "")


def test_the_rule_for_a_place_nobody_has_ticked_yet():
    places = [FIRM, CLIENT]
    assert settle_channels(places, (), []) == ()   # 一个 Express 成员都没有：先不补
    assert settle_channels(places, (), ["cowork"]) == ((FIRM, ("cowork",)), (CLIENT, ("cowork",)))
    assert settle_channels(places, (), ["cowork", "core"]) == ((FIRM, ("cowork",)), (CLIENT, ("cowork",)))
    assert settle_channels(places, (), ["core"]) == ((FIRM, ("cowork", "core")), (CLIENT, ("cowork", "core")))
    # 已登记的照旧，移除了的位置去掉，写法不同算同一个
    kept = settle_channels([CLIENT], [[FIRM, ["cowork"]], [CLIENT.lower() + "\\", ["core", "cowork"]]], ["cowork"])
    assert kept == ((CLIENT, ("cowork", "core")),)
    assert places_for(places, ((FIRM, ("cowork",)), (CLIENT, ("cowork", "core"))), "core") == [CLIENT]
    assert places_for(places, (), "core") == places   # 没登记（测试里直接构造的配置）：两种都给，同以前
    assert shows_channels(places, ((FIRM, ("cowork",)),), ["cowork"]) is False   # 只有一种成员、位置都给它
    assert shows_channels(places, (), ["cowork", "core"]) is True
    assert shows_channels(places, ((FIRM, ("cowork",)), (CLIENT, ("cowork",))), ["core"]) is True   # ERP 缺位置


def test_an_erp_member_joining_a_firm_computer_reads_nothing_until_ticked(tmp_path):
    store = _load(tmp_path / "companion.json")
    _member(store, COWORK_TOKEN, "https://testcowork.pearnly.com")
    store.set_places([FIRM])
    assert _load(store.path).global_settings.place_channels == ((FIRM, ("cowork",)),)
    erp = _member(store, ERP_TOKEN, "https://testerp.pearnly.com")
    again = _load(store.path)
    assert again.global_settings.place_channels == ((FIRM, ("cowork",)),)
    cfg = again.runtime_config(erp)
    assert worker.member_places(cfg) == []
    assert worker.scan_roots(cfg, [FIRM + r"\70EXP"]) == []
    assert worker.places_scope(cfg, [{"root": FIRM + r"\70EXP", "path": "a"}]) == []
    # 之后新加的位置也只给 Cowork
    again.set_places([FIRM, CLIENT])
    assert _load(store.path).global_settings.place_channels == ((FIRM, ("cowork",)), (CLIENT, ("cowork",)))


def test_an_erp_only_computer_keeps_reading_everything_and_a_later_cowork_member_too(tmp_path):
    store = _load(tmp_path / "companion.json")
    erp = _member(store, ERP_TOKEN, "https://testerp.pearnly.com", root=CLIENT + r"\70EXP")
    store.set_places([CLIENT])
    cowork = _member(store, COWORK_TOKEN, "https://testcowork.pearnly.com", root=CLIENT + r"\70EXP")
    again = _load(store.path)
    assert again.global_settings.place_channels == ((CLIENT, ("cowork", "core")),)
    for member in (erp, cowork):
        assert worker.member_places(again.runtime_config(member)) == [CLIENT]


def test_upgrading_a_computer_that_already_has_both_members_unticks_erp(tmp_path):
    store = _load(tmp_path / "companion.json")
    _member(store, COWORK_TOKEN, "https://testcowork.pearnly.com")
    erp = _member(store, ERP_TOKEN, "https://testerp.pearnly.com")
    store.set_places([FIRM])
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw["global"].pop("place_channels")   # 1.12.16 及更早写的文件：没有这一键
    store.path.write_text(json.dumps(raw), encoding="utf-8")
    again = _load(store.path)
    assert again.global_settings.place_channels == ((FIRM, ("cowork",)),)
    assert json.loads(store.path.read_text(encoding="utf-8"))["global"]["place_channels"] == [[FIRM, ["cowork"]]]
    assert worker.member_places(again.runtime_config(erp)) == []


def test_ticking_erp_in_settings_and_the_last_place_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("PEARNLY_FOLDER_STATE_DIR", str(tmp_path / "folders"))
    monkeypatch.setattr(desktop_settings.master_refresh, "rescan_soon", lambda: None)
    store = _load(tmp_path / "companion.json")
    _member(store, COWORK_TOKEN, "https://testcowork.pearnly.com")
    erp = _member(store, ERP_TOKEN, "https://testerp.pearnly.com")
    store.set_places([FIRM, CLIENT])
    settings = _settings(store)

    snap = settings.snapshot()["place_channels"]
    assert snap == {"show": True, "members": ["cowork", "core"], "defaults": ["cowork"], "places": [
        {"path": FIRM, "channels": ["cowork"]}, {"path": CLIENT, "channels": ["cowork"]}]}

    assert settings.set_place_channels(CLIENT.lower(), ["cowork", "core"]) == {"ok": True, "profiles_changed": True}
    assert worker.member_places(_load(store.path).runtime_config(erp)) == [CLIENT]
    assert settings.set_place_channels(CLIENT, ["cowork", "core"]) == {"ok": True}   # 没变
    # 共用电脑上的 ERP 默认不读：仅剩的这个位置也能勾回去（回到 0 个位置，同初始状态，11-08）
    assert settings.set_place_channels(CLIENT, ["cowork"]) == {"ok": True, "profiles_changed": True}
    assert worker.member_places(_load(store.path).runtime_config(erp)) == []
    assert settings.set_place_channels(CLIENT, ["cowork", "core"]) == {"ok": True, "profiles_changed": True}
    # Cowork 还有别的位置时可以只给 ERP
    assert settings.set_place_channels(CLIENT, ["core"]) == {"ok": True, "profiles_changed": True}
    assert settings.set_place_channels(FIRM, []) == {"ok": False}   # Cowork 仅剩的
    assert settings.set_place_channels(r"Z:\NOPE", ["core"]) == {"ok": False}
    assert [row["channels"] for row in settings.snapshot()["place_channels"]["places"]] == [["cowork"], ["core"]]


def test_a_cowork_only_computer_shows_no_ticks(tmp_path, monkeypatch):
    monkeypatch.setenv("PEARNLY_FOLDER_STATE_DIR", str(tmp_path / "folders"))
    store = _load(tmp_path / "companion.json")
    _member(store, COWORK_TOKEN, "https://testcowork.pearnly.com")
    store.set_places([FIRM])
    assert _settings(store).snapshot()["place_channels"]["show"] is False


def test_the_selected_book_outside_the_member_places_is_not_named():
    cfg = types.SimpleNamespace(channel=channel.CORE, express_places=(FIRM, CLIENT),
                                place_channels=((FIRM, ("cowork",)), (CLIENT, ("cowork", "core"))))
    assert worker._in_member_places(cfg, CLIENT + r"\70EXP\T9") is True
    assert worker._in_member_places(cfg, FIRM + r"\70EXP\ACME") is False
    assert worker._member_or_all_places(cfg) == [CLIENT]
    assert worker._in_member_places(types.SimpleNamespace(), FIRM) is True   # 还没有位置清单：同以前


def test_erp_ticked_by_hand_on_a_shared_computer_can_be_unticked_again(tmp_path, monkeypatch):
    # 11-08 测试机：共用电脑只有 70EXP 一个位置，默认只给 Cowork；有人在设置页勾上 ERP 后，ERP 只剩这一个位置，按旧规则
    # 「仅剩的位置不能勾掉」再也勾不回去（两个勾都灰），ERP 一直读事务所的账套。回到「ERP 0 个位置」就是初始状态，要能勾掉；
    # 默认就该读这个位置的成员（这里是 Cowork）仅剩的位置照旧不能勾掉（勾掉它就什么都读不到了）。
    monkeypatch.setenv("PEARNLY_FOLDER_STATE_DIR", str(tmp_path / "folders"))
    monkeypatch.setattr(desktop_settings.master_refresh, "rescan_soon", lambda: None)
    store = _load(tmp_path / "companion.json")
    _member(store, COWORK_TOKEN, "https://testcowork.pearnly.com")
    erp = _member(store, ERP_TOKEN, "https://testerp.pearnly.com")
    store.set_places([FIRM])
    settings = _settings(store)
    snap = settings.snapshot()["place_channels"]
    assert snap["places"] == [{"path": FIRM, "channels": ["cowork"]}]
    assert snap["defaults"] == ["cowork"]   # 页面据此只锁默认就读的成员仅剩的位置

    assert settings.set_place_channels(FIRM, ["cowork", "core"]) == {"ok": True, "profiles_changed": True}
    assert worker.member_places(_load(store.path).runtime_config(erp)) == [FIRM]
    # 勾回去：ERP 回到 0 个位置（同初始状态），不再读这个位置
    assert settings.set_place_channels(FIRM, ["cowork"]) == {"ok": True, "profiles_changed": True}
    assert worker.member_places(_load(store.path).runtime_config(erp)) == []
    assert _load(store.path).global_settings.place_channels == ((FIRM, ("cowork",)),)
    # Cowork 默认就读、仅剩这一个位置：照旧不能勾掉
    assert settings.set_place_channels(FIRM, []) == {"ok": False}
    assert settings.set_place_channels(FIRM, ["core"]) == {"ok": False}


def test_an_erp_only_computer_still_keeps_its_last_place(tmp_path, monkeypatch):
    # 只有 ERP 成员的电脑：位置默认两种都给，ERP 就是默认读的，仅剩的位置照旧不能勾掉
    monkeypatch.setenv("PEARNLY_FOLDER_STATE_DIR", str(tmp_path / "folders"))
    monkeypatch.setattr(desktop_settings.master_refresh, "rescan_soon", lambda: None)
    store = _load(tmp_path / "companion.json")
    _member(store, ERP_TOKEN, "https://testerp.pearnly.com", root=CLIENT + r"\70EXP")
    store.set_places([CLIENT])
    settings = _settings(store)
    assert settings.snapshot()["place_channels"]["defaults"] == ["cowork", "core"]
    assert settings.set_place_channels(CLIENT, ["cowork"]) == {"ok": False}


def test_changing_a_place_tick_writes_who_changed_what(tmp_path, monkeypatch, caplog):
    # 11-08 测试机：ERP 被谁、什么时候勾上的只能靠文件时间猜；改勾记一行日志（位置、改前 → 改后、入口与 Windows 登录名）
    import logging

    from companion import profile_store

    monkeypatch.setenv("PEARNLY_FOLDER_STATE_DIR", str(tmp_path / "folders"))
    monkeypatch.setattr(desktop_settings.master_refresh, "rescan_soon", lambda: None)
    monkeypatch.setattr(profile_store, "_login_name", lambda: "skin3")
    store = _load(tmp_path / "companion.json")
    _member(store, COWORK_TOKEN, "https://testcowork.pearnly.com")
    _member(store, ERP_TOKEN, "https://testerp.pearnly.com")
    store.set_places([FIRM])
    settings = _settings(store)
    with caplog.at_level(logging.INFO, logger="companion.profile_store"):
        settings.set_place_channels(FIRM, ["cowork", "core"])
        settings.set_place_channels(FIRM, ["cowork", "core"])   # 没变不记
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Express 位置 " + FIRM)]
    assert lines == [f"Express 位置 {FIRM} 给哪几种成员：['cowork'] → ['cowork', 'core']（设置页，skin3）"]

