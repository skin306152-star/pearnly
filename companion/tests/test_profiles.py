"""F2 multi-Profile config, migration and token protection."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from src.companion.config import CompanionConfig
from src.companion.profile_store import ProfileStore, ProfileStoreError


class FakeProtector:
    prefix = "test-protected:v1:"

    def protect(self, value: str) -> str:
        return self.prefix + base64.b64encode(value.encode()).decode()

    def unprotect(self, value: str) -> str:
        if not value.startswith(self.prefix):
            raise ProfileStoreError("bad test ciphertext")
        return base64.b64decode(value[len(self.prefix) :]).decode()


class BrokenProtector:
    def protect(self, value: str) -> str:
        raise ProfileStoreError("protection failed")

    def unprotect(self, value: str) -> str:
        raise ProfileStoreError("protection failed")


TOKEN_A = "exp_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa_secret-A"
TOKEN_B = "exp_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb_secret-B"


def _legacy(path: Path, account_dir: str, token: str = TOKEN_A) -> bytes:
    cfg = CompanionConfig(
        backend_url="https://pearnly.com",
        bearer_token=token,
        companion_id="comp-stable",
        account_dir=account_dir,
        account_set=account_dir,
        account_company="Firm A",
        revenue_acc="4100",
        fallback_acc="5100",
        paired=True,
        ui_lang="zh",
    )
    raw = json.dumps(
        {
            field: value
            for field, value in cfg.__dict__.items()
            if not field.startswith("_")
        }
    )
    path.write_text(raw, encoding="utf-8")
    return path.read_bytes()


def _runtime(store: ProfileStore, token: str, account_dir: str, company: str):
    cfg = store.new_runtime_config()
    cfg.update(
        bearer_token=token,
        account_dir=account_dir,
        account_set=account_dir,
        account_company=company,
        paired=True,
    )
    return cfg


def test_new_companion_config_starts_versioned_without_plaintext_token(tmp_path):
    path = tmp_path / "companion.json"
    cfg = CompanionConfig.load(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document == {
        "schema_version": 4,
        "global": document["global"],
        "profiles": [],
    }
    assert "bearer_token" not in path.read_text(encoding="utf-8")
    cfg.update(debug_mode=True, poll_interval_ms=750).save(path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 4 and saved["profiles"] == []
    assert saved["global"]["debug_mode"] is True
    assert saved["global"]["poll_interval_ms"] == 750


def test_flat_migration_encrypts_token_and_is_idempotent(tmp_path):
    path = tmp_path / "companion.json"
    _legacy(path, str(tmp_path / "book-a"))

    store = ProfileStore.load(path, protector=FakeProtector())
    on_disk = path.read_text(encoding="utf-8")
    assert TOKEN_A not in on_disk
    assert json.loads(on_disk)["schema_version"] == 4
    assert store.profiles[0].profile_id.startswith("profile-")
    runtime = store.active_runtime_configs()[0]
    assert runtime.bearer_token == TOKEN_A
    assert runtime.companion_id == "comp-stable"
    assert runtime.revenue_acc == "4100"

    first = path.read_bytes()
    again = ProfileStore.load(path, protector=FakeProtector())
    assert path.read_bytes() == first
    assert again.profiles[0].profile_id == store.profiles[0].profile_id


def test_migration_protection_failure_preserves_flat_file(tmp_path):
    path = tmp_path / "companion.json"
    original = _legacy(path, str(tmp_path / "book-a"))
    with pytest.raises(ProfileStoreError, match="protection failed"):
        ProfileStore.load(path, protector=BrokenProtector())
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".companion.json.*.tmp"))


def test_migration_replace_failure_preserves_flat_file(tmp_path, monkeypatch):
    path = tmp_path / "companion.json"
    original = _legacy(path, str(tmp_path / "book-a"))

    def fail_replace(source, target):
        raise OSError("disk busy")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk busy"):
        ProfileStore.load(path, protector=FakeProtector())
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".companion.json.*.tmp"))


def test_non_windows_default_never_falls_back_to_plaintext(tmp_path):
    path = tmp_path / "companion.json"
    original = _legacy(path, str(tmp_path / "book-a"))
    if os.name == "nt":
        pytest.skip("non-Windows fail-closed contract")
    with pytest.raises(ProfileStoreError, match="DPAPI"):
        ProfileStore.load(path)
    assert path.read_bytes() == original
    assert TOKEN_A in path.read_text(
        encoding="utf-8"
    )  # original was not rewritten or disguised


def test_same_directory_can_serve_independent_profiles(tmp_path):
    real = tmp_path / "real-book"
    real.mkdir()
    alias = tmp_path / "book-alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlink unavailable")
    path = tmp_path / "companion.json"
    store = ProfileStore.load(path, protector=FakeProtector())
    first = _runtime(store, TOKEN_A, str(real), "Firm A")
    first.save()
    second = _runtime(store, TOKEN_B, str(alias), "Firm B")
    second.save()

    reloaded = ProfileStore.load(path, protector=FakeProtector())
    configs = reloaded.active_runtime_configs()
    assert [cfg.bearer_token for cfg in configs] == [TOKEN_A, TOKEN_B]
    assert [cfg.account_company for cfg in configs] == ["Firm A", "Firm B"]


def test_v2_profiles_upgrade_identity_fields_without_exposing_tokens(tmp_path):
    path = tmp_path / "companion.json"
    store = ProfileStore.load(path, protector=FakeProtector())
    cfg = _runtime(store, TOKEN_A, str(tmp_path / "book-a"), "Firm A")
    cfg.save()
    document = json.loads(path.read_text(encoding="utf-8"))
    document["schema_version"] = 2
    for key in ("endpoint_id", "endpoint_name", "pearnly_account"):
        document["profiles"][0].pop(key, None)
    path.write_text(json.dumps(document), encoding="utf-8")

    upgraded = ProfileStore.load(path, protector=FakeProtector())

    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 4
    assert upgraded.profiles[0].endpoint_id == ""
    assert upgraded.profiles[0].authorized_roots == ()
    assert TOKEN_A not in path.read_text(encoding="utf-8")


def test_v3_profiles_upgrade_with_current_root_only(tmp_path):
    path = tmp_path / "companion.json"
    store = ProfileStore.load(path, protector=FakeProtector())
    root = str(tmp_path / "year-69")
    cfg = _runtime(store, TOKEN_A, str(tmp_path / "book-a"), "Firm A")
    cfg.update(express_root=root).save()
    document = json.loads(path.read_text(encoding="utf-8"))
    document["schema_version"] = 3
    document["profiles"][0].pop("authorized_roots", None)
    path.write_text(json.dumps(document), encoding="utf-8")

    upgraded = ProfileStore.load(path, protector=FakeProtector())

    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 4
    assert upgraded.profiles[0].express_root == root
    assert upgraded.profiles[0].authorized_roots == ()


def test_connection_identity_round_trips_with_encrypted_token(tmp_path):
    path = tmp_path / "companion.json"
    store = ProfileStore.load(path, protector=FakeProtector())
    cfg = _runtime(store, TOKEN_A, str(tmp_path / "book-a"), "Firm A")
    cfg.update(
        endpoint_id="endpoint-a",
        endpoint_name="Cowork Express",
        pearnly_account="owner@example.com",
    ).save()

    runtime = ProfileStore.load(path, protector=FakeProtector()).runtime_config(
        cfg.profile_id
    )
    assert runtime.endpoint_id == "endpoint-a"
    assert runtime.endpoint_name == "Cowork Express"
    assert runtime.pearnly_account == "owner@example.com"
    assert runtime.bearer_token == TOKEN_A
    assert TOKEN_A not in path.read_text(encoding="utf-8")


def test_incomplete_profile_and_duplicate_identity_fail_closed(tmp_path):
    path = tmp_path / "companion.json"
    store = ProfileStore.load(path, protector=FakeProtector())
    incomplete = _runtime(store, TOKEN_A, "", "Firm A")
    with pytest.raises(ProfileStoreError, match="selection is incomplete"):
        incomplete.save()

    first = _runtime(store, TOKEN_A, str(tmp_path / "book-a"), "Firm A")
    first.save()
    document = json.loads(path.read_text(encoding="utf-8"))
    duplicate = dict(document["profiles"][0])
    duplicate["account_dir"] = str(tmp_path / "book-b")
    duplicate["account_set"] = duplicate["account_dir"]
    document["profiles"].append(duplicate)
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ProfileStoreError, match="duplicate profile identity"):
        ProfileStore.load(path, protector=FakeProtector())


def test_active_configs_pause_and_runtime_save_callback(tmp_path):
    path = tmp_path / "companion.json"
    store = ProfileStore.load(path, protector=FakeProtector())
    first = _runtime(store, TOKEN_A, str(tmp_path / "book-a"), "Firm A")
    second = _runtime(store, TOKEN_B, str(tmp_path / "book-b"), "Firm B")
    first.save()
    second.save()
    assert [cfg.profile_id for cfg in store.active_runtime_configs()] == [
        first.profile_id,
        second.profile_id,
    ]

    store.set_paused(first.profile_id, True)
    active = store.active_runtime_configs()
    assert [cfg.profile_id for cfg in active] == [second.profile_id]
    active[0].update(account_company="Firm B Updated").save()

    reloaded = ProfileStore.load(path, protector=FakeProtector())
    all_profiles = reloaded.runtime_configs(include_paused=True)
    assert [cfg.paused for cfg in all_profiles] == [True, False]
    assert all_profiles[1].account_company == "Firm B Updated"
    disk = path.read_text(encoding="utf-8")
    assert TOKEN_A not in disk and TOKEN_B not in disk


def test_direct_config_stays_bound_to_the_store_after_first_save(tmp_path):
    path = tmp_path / "companion.json"
    store = ProfileStore.load(path, protector=FakeProtector())
    cfg = CompanionConfig(
        bearer_token=TOKEN_A,
        account_dir=str(tmp_path / "book-a"),
        account_set=str(tmp_path / "book-a"),
        account_company="Firm A",
        paired=True,
    )

    store.save_profile(cfg)
    cfg.update(account_company="Firm A Updated").save()

    reloaded = ProfileStore.load(path, protector=FakeProtector())
    assert reloaded.profiles[0].account_company == "Firm A Updated"


def test_companion_config_reads_v2_primary_profile_with_injected_platform_guard(
    tmp_path, monkeypatch
):
    path = tmp_path / "companion.json"
    store = ProfileStore.load(path, protector=FakeProtector())
    cfg = _runtime(store, TOKEN_A, str(tmp_path / "book-a"), "Firm A")
    cfg.save()

    # CompanionConfig.load has no test-only protector argument; patch only the factory seam.
    import src.companion.profile_store as profile_store

    monkeypatch.setattr(profile_store, "default_protector", FakeProtector)
    loaded = CompanionConfig.load(path)
    assert loaded.profile_id == cfg.profile_id
    assert loaded.bearer_token == TOKEN_A
    loaded.update(account_company="Reloaded").save()
    persisted = ProfileStore.load(path, protector=FakeProtector()).profiles[0]
    assert persisted.account_company == "Reloaded"
