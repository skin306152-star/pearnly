"""Tomo 设置页「此电脑」背后的本机操作（2026-10-10 起取代原小助手主窗口与配对窗）：
成员与状态、网页可选的文件夹、开机启动、Express 数据根、配对流程（默认账套与科目全自动）。"""
import base64
from types import SimpleNamespace

from src.companion import desktop_settings, pairing_flow
from src.companion.account_probe import AccountSet
from src.companion.desktop_settings import DesktopSettings, status_key
from src.companion.i18n import t
from src.companion.profile_store import ProfileStore, ProfileStoreError


class _Protector:
    prefix = "test:"

    def protect(self, value: str) -> str:
        return self.prefix + base64.b64encode(value.encode()).decode()

    def unprotect(self, value: str) -> str:
        if not value.startswith(self.prefix):
            raise ProfileStoreError("bad test ciphertext")
        return base64.b64decode(value[len(self.prefix):]).decode()


def _store(tmp_path, monkeypatch, with_member=True):
    monkeypatch.setenv("PEARNLY_FOLDER_STATE_DIR", str(tmp_path / "folders"))
    store = ProfileStore.load(tmp_path / "companion.json", protector=_Protector())
    if with_member:
        cfg = store.new_runtime_config()
        cfg.update(backend_url="https://testcowork.pearnly.com", bearer_token="exp_endpoint-1_secret",
                   account_dir=r"S:\\EXP68\\ACME", account_set=r"S:\\EXP68\\ACME", account_company="Acme Co.",
                   express_root=r"S:\\EXP68", authorized_roots=[r"S:\\EXP68", r"S:\\EXP69"],
                   pearnly_account="owner@example.com", paired=True, ui_lang="zh")
        store.save_profile(cfg)
    return store


def _settings(store, **kwargs):
    # 换掉成员旧密钥前的认电脑（device_link.settle_old_key）会联网：测试里默认不发
    kwargs.setdefault("settle_old", lambda url, token, **kw: "")
    return DesktopSettings(load_store=lambda: ProfileStore.load(store.path, protector=_Protector()), **kwargs)


def test_snapshot_lists_members_with_status_folders_and_the_machine_roots(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    member = store.profiles[0].profile_id
    monkeypatch.setattr(desktop_settings.autostart, "is_enabled", lambda: True)
    settings = _settings(store, states=lambda: {member: ("idle", "")}, hotkey=lambda: True,
                         update_status=lambda: ("found", "1.4.1"))

    snap = settings.snapshot()

    assert snap["express_roots"] == [r"S:\\EXP68", r"S:\\EXP69"]
    assert snap["site"] == "testcowork.pearnly.com"
    assert snap["members"] == [{"id": member, "name": "owner@example.com", "kind": "express", "paused": False,
                                "status": "online", "device_state": "", "folders": []}]
    assert snap["device"] == {}  # 没注入设备密钥（旧桥 / 取不到）：空对象，页面按旧版处理
    assert snap["autostart"] is True and snap["hotkey"] is True
    assert snap["update"] == {"state": "found", "version": "1.4.1"}


def test_a_blocked_update_reaches_the_page_and_download_goes_through_the_tray(tmp_path, monkeypatch):
    # 教程问题第 1 条：自动更新被 Windows 拦下 → 设置页「版本」一行显示被拦 +「下载新版」（托盘在系统浏览器打开官网下载）
    store = _store(tmp_path, monkeypatch)
    opened = []
    settings = _settings(store, update_status=lambda: ("blocked", "1.5.2"), on_download=lambda: opened.append(True))
    assert settings.snapshot()["update"] == {"state": "blocked", "version": "1.5.2"}
    assert settings.download() == {"ok": True}
    assert opened == [True]


def test_status_maps_worker_snapshot_and_paused_wins():
    active, paused = SimpleNamespace(paused=False), SimpleNamespace(paused=True)
    assert status_key(active, ("idle", "")) == "online"
    assert status_key(active, ("working", "INV-1")) == "processing"
    assert status_key(active, ("indexing", "")) == "processing"
    assert status_key(active, ("waiting_lock", "")) == "attention"
    assert status_key(active, ("error", "")) == "attention"
    assert status_key(active, ("offline", "")) == "offline"
    assert status_key(active, None) == "connecting"
    assert status_key(paused, ("idle", "")) == "paused"


def test_folders_save_at_once_and_overlap_is_refused(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    member = store.profiles[0].profile_id
    started = []
    settings = _settings(store, on_folders_changed=lambda: started.append(True))
    root = tmp_path / "invoices"

    assert settings.set_folders(member, [str(root)], "zh") == {"ok": True}
    assert settings.snapshot()["members"][0]["folders"] == [str(root)] and started == [True]
    refused = settings.set_folders(member, [str(root), str(root / "2026")], "zh")
    assert refused["ok"] is False and refused["message"]
    assert settings.snapshot()["members"][0]["folders"] == [str(root)]


def test_pause_and_remove_reload_the_worker(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    member = store.profiles[0].profile_id
    reloads = []
    settings = _settings(store, on_profiles_changed=lambda: reloads.append(True))

    settings.set_paused(member, True)
    assert settings.snapshot()["members"][0]["status"] == "paused"
    settings.remove(member)
    assert settings.snapshot()["members"] == [] and reloads == [True, True]


def test_start_with_windows_is_one_setting_and_never_touches_members(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    before = store.profiles
    calls = []
    monkeypatch.setattr(desktop_settings.autostart, "disable", lambda: calls.append("off"))
    monkeypatch.setattr(desktop_settings.autostart, "enable", lambda: calls.append("on"))

    _settings(store).set_autostart(False)

    reloaded = ProfileStore.load(store.path, protector=_Protector())
    assert calls == ["off"] and reloaded.global_settings.autostart is False
    assert reloaded.profiles == before


def test_changing_the_express_folder_writes_every_member_and_asks_for_a_reload(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    applied = []
    monkeypatch.setattr(desktop_settings, "roots_for_pick", lambda path: [path, r"T:\\EXP69"])
    monkeypatch.setattr(desktop_settings, "apply_roots", lambda fresh, roots: applied.append((fresh.path, roots)))

    result = _settings(store).change_roots(r"T:\\EXP70", "zh")

    assert result == {"ok": True, "profiles_changed": True}   # 重启取任务线程由桥回到界面线程做
    # 选到的排前面，原来已授权的（成员原有的 S: 年度）接在后面：合并、不覆盖
    # 选的路径按 Windows 写法存（Qt 给的是正斜杠时也一样）
    assert applied == [(store.path, [r"T:\EXP70", r"T:\\EXP69", r"S:\\EXP68", r"S:\\EXP69"])]


def test_a_folder_that_is_not_express_changes_nothing(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    before = store.path.read_bytes()
    monkeypatch.setattr(desktop_settings, "roots_for_pick", lambda path: [])

    result = _settings(store).change_roots(str(tmp_path), "zh")

    assert result == {"ok": False, "message": t("express_not_found", "zh")}
    assert store.path.read_bytes() == before


def test_without_members_the_detected_express_is_used_for_the_first_pairing(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch, with_member=False)
    monkeypatch.setattr(desktop_settings, "detect_roots", lambda _store: [r"S:\\EXP68"])
    seen = {}
    monkeypatch.setattr(pairing_flow, "run", lambda _store, code, roots, profile_id, lang: seen.update(roots=roots) or
                        SimpleNamespace(ok=True, error_key="", error_fmt={}))
    settings = _settings(store)

    assert settings.detect() == [r"S:\\EXP68"]
    assert settings.snapshot()["express_roots"] == [r"S:\\EXP68"]
    assert settings.pair(" exp_e_s@testcowork.pearnly.com ", "zh") == {"ok": True, "profiles_changed": True}
    assert seen["roots"] == [r"S:\\EXP68"]


def test_pairing_errors_come_back_as_customer_text(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch, with_member=False)
    monkeypatch.setattr(pairing_flow, "run", lambda *a, **k: SimpleNamespace(ok=False, error_key="pair_err_token_format",
                                                                           error_fmt={}))
    result = _settings(store).pair("bad", "zh")
    assert result["ok"] is False and t("pair_err_token_format", "zh") in result["message"]
    monkeypatch.setattr(pairing_flow, "run", lambda *a, **k: SimpleNamespace(ok=False, error_key=pairing_flow.NEED_ROOTS,
                                                                           error_fmt={}))
    assert _settings(store).pair("exp_x", "zh") == {"ok": False, "message": t("browse_hint", "zh")}


def test_one_computer_one_company_another_company_needs_an_explicit_switch(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    paired = []
    monkeypatch.setattr(pairing_flow, "run", lambda _store, code, roots, profile_id, lang: paired.append(
        [p.profile_id for p in _store.profiles]) or SimpleNamespace(ok=True, error_key="", error_fmt={}))
    settings = _settings(store)

    assert settings.pair("exp_c_s@other.pearnly.com", "zh") == {"ok": False, "other_site": "testcowork.pearnly.com"}
    assert paired == [] and len(settings.snapshot()["members"]) == 1
    # 同一家公司再连一条（例如 MR.ERP）照常添加
    assert settings.pair("exp_m_s@TestCowork.pearnly.com", "zh")["ok"] is True
    assert paired[-1] == [store.profiles[0].profile_id]
    # 用户点了「改连本公司」：原来的成员全部断开后再配对；配对失败也要重启取任务线程
    monkeypatch.setattr(pairing_flow, "run", lambda _store, code, roots, profile_id, lang: paired.append(
        [p.profile_id for p in _store.profiles]) or SimpleNamespace(ok=False, error_key="pair_err_token_format",
                                                                     error_fmt={}))
    result = settings.pair("exp_c_s@other.pearnly.com", "zh", switch=True)
    assert result["ok"] is False and result["profiles_changed"] is True
    assert paired[-1] == [] and settings.snapshot()["members"] == [] and settings.snapshot()["site"] == ""


def test_reconnecting_the_same_connection_replaces_its_member(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    member = store.profiles[0]
    cfg = store.runtime_config(member.profile_id)
    cfg.update(endpoint_id="CWEC-1")
    store.save_profile(cfg)
    seen = []
    monkeypatch.setattr(pairing_flow, "run", lambda _store, code, roots, profile_id, lang: seen.append(profile_id) or
                        SimpleNamespace(ok=True, error_key="", error_fmt={}))
    settings = _settings(store)

    settings.pair("exp_CWEC-1_new@testcowork.pearnly.com", "zh")
    settings.pair("exp_CWEC-2_new@testcowork.pearnly.com", "zh")
    assert seen == [member.profile_id, None]


# ── 配对流程（原配对窗里的逻辑） ──

def _acc(path, name="A"):
    return AccountSet(code="A", name=name, company=f"{name} Co.", path=path, tax_id="", writable=True, row=2)


def _cfg(account_set=""):
    return SimpleNamespace(account_set=account_set, account_dir="", revenue_acc="4999", ar_acc="",
                           vat_output_acc="", fallback_acc="", ap_acc="", vat_input_acc="")


def test_default_account_is_picked_without_asking():
    sets = [_acc(r"S:\\EXP68\\A"), _acc(r"S:\\EXP68\\B")]
    assert pairing_flow.prefill_account_index(_cfg(), sets, False) == 0
    assert pairing_flow.prefill_account_index(_cfg(r"S:\\EXP68\\B"), sets, True) == 1
    assert pairing_flow.prefill_account_index(_cfg(r"S:\\GONE"), sets, True) == 0
    assert pairing_flow.prefill_account_index(_cfg(), [], True) == -1


def test_changing_the_key_keeps_hand_edited_accounts_of_the_same_account_set():
    cfg = _cfg(r"S:\\EXP68\\B")
    same = pairing_flow.auto_mapping(cfg, _acc(r"S:\\EXP68\\B"), {"revenue_acc": "4100"}, True)
    other = pairing_flow.auto_mapping(cfg, _acc(r"S:\\EXP68\\A"), {"revenue_acc": "4100"}, True)
    assert same["revenue_acc"] == "4999" and other["revenue_acc"] == "4100"


def test_pairing_uses_the_machine_roots_and_reports_every_account_set(monkeypatch):
    cfg = SimpleNamespace(**vars(_cfg(r"S:\\EXP68\\B")), paired=True)
    store = SimpleNamespace(global_settings=SimpleNamespace(autostart=True))
    sets = [_acc(r"S:\\EXP68\\A"), _acc(r"S:\\EXP68\\B", "B")]
    calls = {}
    monkeypatch.setattr(pairing_flow, "config_for", lambda _store, _id: cfg)
    monkeypatch.setattr(pairing_flow.pairing, "connection_kind", lambda token, cfg: "express")
    monkeypatch.setattr(pairing_flow, "probe_account_sets", lambda root: calls.setdefault("probed", root) and sets)
    monkeypatch.setattr(pairing_flow, "probe_default_accounts", lambda path: {})
    monkeypatch.setattr(pairing_flow.pairing, "pair", lambda **kwargs: calls.setdefault("pair", kwargs))

    pairing_flow.run(store, "exp_e_s@testcowork.pearnly.com", roots=[r"S:\\EXP68", r"S:\\EXP69"], lang="zh")

    assert calls["probed"] == r"S:\\EXP68"
    pair = calls["pair"]
    assert pair["chosen"].path == r"S:\\EXP68\\B" and pair["all_sets"] == sets
    assert pair["express_root"] == r"S:\\EXP68"
    assert pair["authorized_roots"] == [r"S:\\EXP69", r"S:\\EXP68"]  # 与旧版一致：新年度在前
    assert pair["enable_autostart"] is True


def test_folder_picker_never_opens_in_the_program_folder(tmp_path):
    # 空值时 Qt 从工作目录（CC 的程序文件夹）打开；没加过文件夹就从「文档」打开，加过就从上次那个的上一级打开。
    documents = str(tmp_path / "Documents")
    assert desktop_settings.picker_start([], documents) == documents
    picked = tmp_path / "票据" / "十月"
    picked.mkdir(parents=True)
    assert desktop_settings.picker_start([str(picked)], documents) == str(tmp_path / "票据")
    assert desktop_settings.picker_start([str(tmp_path / "gone" / "x")], documents) == documents


def test_cc_program_folder_is_never_offered_or_added():
    program = "C:\\Users\\a\\AppData\\Local\\Programs\\Pearnly Companion"
    assert desktop_settings.inside(program + "\\_internal", program)
    assert desktop_settings.inside(program.upper(), program)
    assert not desktop_settings.inside("C:\\Users\\a\\AppData\\Local\\Programs\\Pearnly Companion2", program)
    assert not desktop_settings.inside("C:\\Users\\a", program)
    # 上次加的就是程序文件夹（旧版留下的）：不从它的上一级（还是程序文件夹）打开，改从「文档」打开。
    assert desktop_settings.picker_start([program + "\\_internal"], "DOCS", program) == "DOCS"


def test_find_again_authorizes_everything_found_or_says_none_was_found(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    applied = []
    monkeypatch.setattr(desktop_settings, "rescan_roots", lambda fresh: [r"T:\\EXP70", r"\\\\srv\\ACC\\EXP71"])
    monkeypatch.setattr(desktop_settings, "apply_roots", lambda fresh, roots: applied.append(roots))
    assert _settings(store).rescan("zh") == {"ok": True, "profiles_changed": True}
    assert applied == [[r"T:\\EXP70", r"\\\\srv\\ACC\\EXP71"]]
    monkeypatch.setattr(desktop_settings, "rescan_roots", lambda fresh: [])
    assert _settings(store).rescan("zh") == {"ok": False, "message": t("express_not_found", "zh")}


def test_an_old_always_on_setting_becomes_keep_awake_and_is_dropped_on_the_next_save(tmp_path, monkeypatch):
    # ADR-021 第 56、58 条（用户 10-16）：「常驻模式」拆开——替别人接活删掉、开机锁屏删掉；防睡眠照 Claude 改成「CC 开着时
    # 保持电脑唤醒」「用电池时也保持」，默认都开。1.4.2–1.6 写进 companion.json 的 always_on=True 迁成 keep_awake=True，
    # 下次写回时旧键丢掉
    import json

    store = _store(tmp_path, monkeypatch)
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw["global"]["always_on"] = True
    raw["global"].pop("keep_awake", None)
    raw["global"].pop("keep_awake_on_battery", None)
    store.path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = ProfileStore.load(store.path, protector=_Protector())
    assert not hasattr(loaded.global_settings, "always_on")
    assert loaded.global_settings.keep_awake is True and loaded.global_settings.keep_awake_on_battery is True
    assert all(not hasattr(cfg, "always_on") and cfg.keep_awake for cfg in loaded.runtime_configs(include_paused=True))
    monkeypatch.setattr(desktop_settings.autostart, "enable", lambda: None)
    monkeypatch.setattr(desktop_settings.autostart, "is_enabled", lambda: True)
    settings = _settings(loaded)
    snap = settings.snapshot()
    assert "always_on" not in snap and not hasattr(settings, "set_always_on")
    assert snap["keep_awake"] is True and snap["keep_awake_on_battery"] is True
    assert snap["autostart"] is True   # 开机自启照旧是默认
    assert settings.set_autostart(True) == {"ok": True}
    saved = json.loads(store.path.read_text(encoding="utf-8"))["global"]
    assert "always_on" not in saved and saved["keep_awake"] is True and saved["keep_awake_on_battery"] is True


def test_keep_awake_defaults_on_and_an_explicit_off_is_not_overridden_by_always_on(tmp_path, monkeypatch):
    import json

    store = _store(tmp_path, monkeypatch)
    assert store.global_settings.keep_awake is True and store.global_settings.keep_awake_on_battery is True
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw["global"].update(always_on=False)
    raw["global"].pop("keep_awake", None)
    store.path.write_text(json.dumps(raw), encoding="utf-8")
    assert ProfileStore.load(store.path, protector=_Protector()).global_settings.keep_awake is True   # 新默认：开
    raw["global"].update(always_on=True, keep_awake=False)
    store.path.write_text(json.dumps(raw), encoding="utf-8")
    assert ProfileStore.load(store.path, protector=_Protector()).global_settings.keep_awake is False  # 新键说了算


def test_the_two_keep_awake_switches_save_and_tell_the_tray(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    told = []
    settings = _settings(store, on_keep_awake=lambda enabled, on_battery: told.append((enabled, on_battery)))

    assert settings.set_keep_awake(on_battery=False) == {"ok": True}
    assert told[-1] == (True, False)
    assert settings.set_keep_awake(enabled=False) == {"ok": True}
    assert told[-1] == (False, False)
    reloaded = ProfileStore.load(store.path, protector=_Protector())
    assert (reloaded.global_settings.keep_awake, reloaded.global_settings.keep_awake_on_battery) == (False, False)
    assert all(not cfg.keep_awake for cfg in reloaded.runtime_configs(include_paused=True))
    assert reloaded.profiles == store.profiles   # 成员不动
    snap = settings.snapshot()
    assert (snap["keep_awake"], snap["keep_awake_on_battery"]) == (False, False)
    settings.set_keep_awake(enabled=True)
    settings.set_keep_awake(on_battery=True)
    assert told[-2:] == [(True, False), (True, True)]


def test_an_old_worker_copy_saving_on_a_heartbeat_does_not_put_the_switches_back(tmp_path, monkeypatch):
    # 10-16 审查：取任务线程启动时 copy() 了各成员的配置；设置里关掉开关后，心跳同步账套名 / 连接名 / 新年度文件夹时
    # 副本存盘曾把读到时的全局设置整份写回，开关又变成开、下次重读配置又拿住。现在副本只写它自己改过的项。
    from copy import copy

    from src.companion.i18n import persist_ui_lang

    store = _store(tmp_path, monkeypatch)
    tray_cfg = store.runtime_configs(include_paused=True)[0]
    worker_copy = copy(tray_cfg)   # gui_tray_workers.PollWorker 的做法
    settings = _settings(store, on_keep_awake=lambda enabled, on_battery: None)
    monkeypatch.setattr(desktop_settings.autostart, "disable", lambda: None)
    settings.set_keep_awake(enabled=False)
    settings.set_keep_awake(on_battery=False)
    settings.set_autostart(False)
    persist_ui_lang(ProfileStore.load(store.path, protector=_Protector()), "th")

    worker_copy.update(account_set="NEW").save()   # worker._sync_account_set

    reloaded = ProfileStore.load(store.path, protector=_Protector())
    g = reloaded.global_settings
    assert (g.keep_awake, g.keep_awake_on_battery, g.autostart, g.ui_lang) == (False, False, False, "th")
    assert reloaded.runtime_configs(include_paused=True)[0].account_set == "NEW"   # 副本自己改的照样存下
    assert worker_copy.keep_awake is False and worker_copy.ui_lang == "th"         # 存盘后副本跟上文件里的值
    assert settings.snapshot()["keep_awake"] is False
    tray_cfg.save()   # 托盘手上那份（同样读得早）存盘也一样
    g = ProfileStore.load(store.path, protector=_Protector()).global_settings
    assert (g.keep_awake, g.keep_awake_on_battery, g.autostart, g.ui_lang) == (False, False, False, "th")

    # 持有者自己改的全局项照样写（旧副本改语言 → 写下；其余仍以文件为准）
    tray_cfg.update(ui_lang="zh").save()
    g = ProfileStore.load(store.path, protector=_Protector()).global_settings
    assert (g.ui_lang, g.keep_awake, g.autostart) == ("zh", False, False)
