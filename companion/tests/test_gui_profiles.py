import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from src.companion import gui_profiles  # noqa: E402
from src.companion.gui_profiles import (  # noqa: E402
    ProfileManagerDialog,
    masked_token,
    profile_status_key,
)
from src.companion.profile_store import ProfileStore, ProfileStoreError  # noqa: E402


class _Protector:
    prefix = "test:"

    def protect(self, value: str) -> str:
        return self.prefix + base64.b64encode(value.encode()).decode()

    def unprotect(self, value: str) -> str:
        if not value.startswith(self.prefix):
            raise ProfileStoreError("bad test ciphertext")
        return base64.b64decode(value[len(self.prefix):]).decode()


def _store_with_one_connection(tmp_path, monkeypatch):
    monkeypatch.setenv("PEARNLY_FOLDER_STATE_DIR", str(tmp_path / "folders"))
    store = ProfileStore.load(tmp_path / "companion.json", protector=_Protector())
    cfg = store.new_runtime_config()
    cfg.update(
        backend_url="https://testcowork.pearnly.com",
        bearer_token="exp_endpoint-1_secret",
        account_dir=r"S:\\EXP68\\ACME",
        account_set=r"S:\\EXP68\\ACME",
        account_company="Acme Co.",
        express_root=r"S:\\EXP68",
        authorized_roots=[r"S:\\EXP68", r"S:\\EXP69"],
        pearnly_account="owner@example.com",
        paired=True,
        ui_lang="zh",
    )
    store.save_profile(cfg)
    return store


def _assert_free_window(window):
    # 普通窗口：最小化、最大化、关闭都有，能拖边框调大小（用户 2026-10-08）
    from PySide6.QtCore import Qt

    flags = window.windowFlags()
    for hint in (Qt.WindowMinimizeButtonHint, Qt.WindowMaximizeButtonHint, Qt.WindowCloseButtonHint):
        assert flags & hint
    assert window.isSizeGripEnabled()
    assert window.minimumWidth() < window.maximumWidth()


def _main_window(store, monkeypatch, states=None, detected=()):
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(gui_profiles.autostart, "is_enabled", lambda: True)
    monkeypatch.setattr(gui_profiles, "detect_roots", lambda _store: list(detected))
    dialog = ProfileManagerDialog(lang="zh", profile_store=store, profile_states=states or {})
    dialog._live_refresh.stop()  # 测试里不去读本机真实配置
    return dialog


def test_main_window_shows_each_connection_with_status_and_its_folders(tmp_path, monkeypatch):
    # 2026-10-08：连接、网页可选的文件夹、开机启动在同一个窗口；卡片标题只写 Pearnly 账号（不显示账套）。
    store = _store_with_one_connection(tmp_path, monkeypatch)
    dialog = _main_window(store, monkeypatch, {store.profiles[0].profile_id: ("idle", "")})

    # 这台电脑：Express 账套是全部成员数据根的并集，已有成员时不再检测
    assert dialog.machine_box.title() == "这台电脑"
    assert dialog.express_value.text() == r"S:\\EXP68 等 2 个年度"
    assert dialog.add_btn.text() == "添加成员" and dialog.add_btn.isEnabled()

    [card] = dialog._cards.values()
    assert card["box"].title() == "owner@example.com"
    assert card["status"].text() == "Pearnly 已连接"
    assert dialog.empty.isHidden()
    assert dialog.autostart.isChecked()
    assert dialog.lang_btn.text() == "中文 / ไทย"
    _assert_free_window(dialog)

    from src.companion.gui_folders import FolderRootsBox

    [folders] = card["box"].findChildren(FolderRootsBox)
    assert folders.list.isHidden() and folders.remove_btn.isHidden()  # 没有文件夹不摆空框
    assert folders._save([str(tmp_path / "invoices")])  # 添加当场保存，不再有「保存」按钮
    assert folders.paths() == [str(tmp_path / "invoices")]
    assert not folders.list.isHidden()


def test_folder_overlap_is_refused_and_nothing_is_saved(tmp_path, monkeypatch):
    store = _store_with_one_connection(tmp_path, monkeypatch)
    dialog = _main_window(store, monkeypatch)
    from src.companion.gui_folders import FolderRootsBox

    [folders] = next(iter(dialog._cards.values()))["box"].findChildren(FolderRootsBox)
    warnings = []
    monkeypatch.setattr(gui_profiles.QMessageBox, "warning", lambda *args: warnings.append(args))
    from src.companion import gui_folders

    monkeypatch.setattr(gui_folders.QMessageBox, "warning", lambda *args: warnings.append(args))
    root = tmp_path / "invoices"
    assert not folders._save([str(root), str(root / "2026")])
    assert warnings and folders.paths() == []


def test_main_window_without_connections_says_how_to_add_one(tmp_path, monkeypatch):
    monkeypatch.setenv("PEARNLY_FOLDER_STATE_DIR", str(tmp_path / "folders"))
    store = ProfileStore.load(tmp_path / "companion.json", protector=_Protector())
    dialog = _main_window(store, monkeypatch, detected=[r"S:\\EXP68"])

    assert dialog._cards == {}
    assert not dialog.empty.isHidden()
    assert "添加成员" in dialog.empty.text()
    # 还没有成员：后台自动找 Express，找完之前不能添加成员
    assert not dialog.add_btn.isEnabled()
    _wait_until(lambda: dialog._roots_state == "ready")
    assert dialog._roots == [r"S:\\EXP68"]
    assert dialog.express_value.text() == r"S:\\EXP68"
    assert dialog.add_btn.isEnabled()


def test_without_express_the_machine_row_says_to_pick_it(tmp_path, monkeypatch):
    monkeypatch.setenv("PEARNLY_FOLDER_STATE_DIR", str(tmp_path / "folders"))
    store = ProfileStore.load(tmp_path / "companion.json", protector=_Protector())
    dialog = _main_window(store, monkeypatch)
    _wait_until(lambda: dialog._roots_state == "ready")

    assert "更换" in dialog.express_value.text()
    assert not dialog.add_btn.isEnabled() and dialog.change_btn.isEnabled()


def _wait_until(condition, seconds=5):
    import time

    from PySide6.QtWidgets import QApplication

    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert condition()


def test_masked_token_never_exposes_the_middle():
    assert masked_token("exp_1234567890abcd") == "exp***abcd"
    assert masked_token("short") == "***"


def test_profile_status_maps_worker_snapshot_and_paused_wins():
    active = SimpleNamespace(paused=False)
    paused = SimpleNamespace(paused=True)

    assert profile_status_key(active, ("idle", "")) == "profiles_online"
    assert profile_status_key(active, ("working", "INV-1")) == "profiles_processing"
    assert profile_status_key(active, ("indexing", "")) == "profiles_processing"
    assert profile_status_key(active, ("waiting_lock", "")) == "profiles_attention"
    assert profile_status_key(active, ("error", "")) == "profiles_attention"
    assert profile_status_key(active, ("offline", "")) == "profiles_offline"
    assert profile_status_key(active, None) == "profiles_connecting"
    assert profile_status_key(paused, ("idle", "")) == "profiles_paused"


def test_successful_profile_change_notifies_runtime_and_keeps_close_fallback():
    calls = []
    dialog = ProfileManagerDialog.__new__(ProfileManagerDialog)
    dialog.changed = False
    dialog._on_profiles_changed = lambda: calls.append("reload")

    dialog._profiles_changed()

    assert dialog.changed is True
    assert calls == ["reload"]


def test_open_window_rebuilds_cards_only_when_connections_change(monkeypatch):
    old_store = SimpleNamespace(path=Path("profiles.json"), profiles=("old",))
    new_store = SimpleNamespace(path=old_store.path, profiles=("new",))
    dialog = ProfileManagerDialog.__new__(ProfileManagerDialog)
    dialog._store = old_store
    dialog._profile_states = {}
    dialog._profile_state_provider = lambda: {"profile-a": ("idle", "")}
    dialog._roots_state = "ready"
    dialog._paint_roots = lambda: None
    dialog._paint_update = lambda: None
    monkeypatch.setattr(gui_profiles, "machine_roots", lambda _store: [])
    calls = []
    dialog._refresh = lambda: calls.append("rebuild")
    dialog._paint_states = lambda: calls.append("states")
    monkeypatch.setattr(gui_profiles.ProfileStore, "load", lambda _path: new_store)

    dialog._refresh_runtime_snapshot()

    assert dialog._store is new_store
    assert dialog._profile_states == {"profile-a": ("idle", "")}
    assert calls == ["rebuild"]

    # 只是状态变了：只刷状态行，不重建卡片（正在点的文件夹 / 菜单不会被打断）
    dialog._profile_state_provider = lambda: {"profile-a": ("working", "INV-1")}
    dialog._refresh_runtime_snapshot()
    assert calls == ["rebuild", "states"]


def test_start_with_windows_is_one_setting_and_never_touches_members(tmp_path, monkeypatch):
    store = _store_with_one_connection(tmp_path, monkeypatch)
    before = store.profiles
    dialog = _main_window(store, monkeypatch)
    calls = []
    monkeypatch.setattr(gui_profiles.autostart, "disable", lambda: calls.append("off"))
    monkeypatch.setattr(gui_profiles.autostart, "enable", lambda: calls.append("on"))

    dialog.autostart.setChecked(False)

    reloaded = ProfileStore.load(store.path, protector=_Protector())
    assert calls == ["off"] and reloaded.global_settings.autostart is False
    assert reloaded.profiles == before


def test_change_writes_the_picked_root_to_every_member_and_the_window_shows_it(tmp_path, monkeypatch):
    store = _store_with_one_connection(tmp_path, monkeypatch)
    dialog = _main_window(store, monkeypatch, {store.profiles[0].profile_id: ("idle", "")})
    picked = r"T:\\EXP70"
    monkeypatch.setattr(gui_profiles.QFileDialog, "getExistingDirectory", lambda *_args: picked)
    monkeypatch.setattr(gui_profiles, "roots_for_pick", lambda path: [path, r"T:\\EXP69"])
    applied, reloads = [], []
    monkeypatch.setattr(gui_profiles, "apply_roots", lambda fresh, roots: applied.append((fresh.path, roots)))
    dialog._on_profiles_changed = lambda: reloads.append(True)

    dialog._change_roots()
    assert not dialog.change_btn.isEnabled() and not dialog.add_btn.isEnabled()  # 正在换，先不能添加成员
    _wait_until(lambda: dialog._roots_state == "ready")

    assert applied == [(store.path, [picked, r"T:\\EXP69"])]
    assert reloads == [True]  # 通知托盘重载成员、重启取任务线程
    assert dialog.express_value.text() == r"T:\\EXP70 等 2 个年度"


def test_picking_a_folder_that_is_not_express_changes_nothing(tmp_path, monkeypatch):
    store = _store_with_one_connection(tmp_path, monkeypatch)
    before = store.path.read_bytes()
    dialog = _main_window(store, monkeypatch)
    monkeypatch.setattr(gui_profiles.QFileDialog, "getExistingDirectory", lambda *_args: str(tmp_path))
    monkeypatch.setattr(gui_profiles, "roots_for_pick", lambda path: [])

    dialog._change_roots()
    _wait_until(lambda: dialog._roots_state == "ready")

    assert store.path.read_bytes() == before
    assert dialog._roots == [r"S:\\EXP68", r"S:\\EXP69"]
    assert dialog.express_value.text() == "未能读取 Express 公司列表,请确认 Express 路径。"


def test_footer_update_button_shows_the_real_step_and_asks_the_tray(tmp_path, monkeypatch):
    # 2026-10-08：版本号旁一个带下载图标的按钮——平时「检查更新」，有新版「立即更新」；下载 / 安装中只显示状态。
    store = _store_with_one_connection(tmp_path, monkeypatch)
    status = {"value": ("none", "")}
    clicks = []
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(gui_profiles.autostart, "is_enabled", lambda: True)
    dialog = ProfileManagerDialog(lang="zh", profile_store=store, update_status=lambda: status["value"],
                                  on_update=lambda: clicks.append(True))
    dialog._live_refresh.stop()

    assert (dialog.upd_btn.text(), dialog.upd_lbl.text()) == ("检查更新", "")
    assert not dialog.upd_btn.isHidden()
    dialog.upd_btn.click()
    assert clicks == [True]
    for state, label, button in (("latest", "已是最新版本", "检查更新"), ("found", "发现新版本 v1.4.0", "立即更新"),
                                 ("ready", "v1.4.0 已下载，手上的录入做完后自动安装", "立即更新")):
        status["value"] = (state, "1.4.0")
        dialog._paint_update()
        assert (dialog.upd_lbl.text(), dialog.upd_btn.text()) == (label, button)
    for state in ("checking", "downloading", "installing"):
        status["value"] = (state, "1.4.0")
        dialog._paint_update()
        assert dialog.upd_btn.isHidden()


def test_footer_has_no_update_button_without_the_tray(tmp_path, monkeypatch):
    store = _store_with_one_connection(tmp_path, monkeypatch)
    dialog = _main_window(store, monkeypatch)
    assert dialog.upd_btn.isHidden() and dialog.upd_lbl.isHidden()


def test_update_button_uses_the_windows_icons_refresh_then_download(tmp_path, monkeypatch):
    # 平时循环箭头（检查更新），有新版箭头落进托盘（立即更新），取 Windows 自带图标字体；没有这套字体就只显示文字。
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    store = _store_with_one_connection(tmp_path, monkeypatch)
    status = {"value": ("none", "")}
    monkeypatch.setattr(gui_profiles.autostart, "is_enabled", lambda: True)
    present = ["Helvetica", "Arial", gui_profiles.QFontDatabase.families()[0]]
    monkeypatch.setattr(gui_profiles, "ICON_FONTS", ("Segoe Fluent Icons", *present))
    dialog = ProfileManagerDialog(lang="zh", profile_store=store, update_status=lambda: status["value"],
                                  on_update=lambda: None)
    dialog._live_refresh.stop()

    assert dialog.upd_btn.property("glyph") == gui_profiles.GLYPH_REFRESH and not dialog.upd_btn.icon().isNull()
    status["value"] = ("found", "1.4.0")
    dialog._paint_update()
    assert dialog.upd_btn.property("glyph") == gui_profiles.GLYPH_DOWNLOAD

    monkeypatch.setattr(gui_profiles, "ICON_FONTS", ("Segoe Fluent Icons", "Segoe MDL2 Assets"))
    if not any(name in gui_profiles.QFontDatabase.families() for name in gui_profiles.ICON_FONTS):
        assert gui_profiles.glyph_icon(gui_profiles.GLYPH_REFRESH, "black").isNull()


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows 自带图标字体只在 Windows 上有")
def test_windows_has_the_icon_font_and_both_icons_draw_something():
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    assert any(name in gui_profiles.QFontDatabase.families() for name in gui_profiles.ICON_FONTS)
    for glyph in (gui_profiles.GLYPH_REFRESH, gui_profiles.GLYPH_DOWNLOAD):
        image = gui_profiles.glyph_icon(glyph, "black").pixmap(16, 16).toImage()
        assert any(image.pixelColor(x, y).alpha() for x in range(image.width()) for y in range(image.height()))
