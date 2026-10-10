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
    return DesktopSettings(load_store=lambda: ProfileStore.load(store.path, protector=_Protector()), **kwargs)


def test_snapshot_lists_members_with_status_folders_and_the_machine_roots(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    member = store.profiles[0].profile_id
    monkeypatch.setattr(desktop_settings.autostart, "is_enabled", lambda: True)
    settings = _settings(store, states=lambda: {member: ("idle", "")}, hotkey=lambda: True,
                         update_status=lambda: ("found", "1.4.1"))

    snap = settings.snapshot()

    assert snap["express_roots"] == [r"S:\\EXP68", r"S:\\EXP69"]
    assert snap["members"] == [{"id": member, "name": "owner@example.com", "kind": "express", "paused": False,
                                "status": "online", "folders": []}]
    assert snap["autostart"] is True and snap["hotkey"] is True
    assert snap["update"] == {"state": "found", "version": "1.4.1"}


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
    assert applied == [(store.path, [r"T:\\EXP70", r"T:\\EXP69"])]


def test_a_folder_that_is_not_express_changes_nothing(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    before = store.path.read_bytes()
    monkeypatch.setattr(desktop_settings, "roots_for_pick", lambda path: [])

    result = _settings(store).change_roots(str(tmp_path), "zh")

    assert result == {"ok": False, "message": t("status_reg_error", "zh")}
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
