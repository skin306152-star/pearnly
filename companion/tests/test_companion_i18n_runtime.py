"""Focused contracts for the Companion Chinese/Thai runtime language."""

from __future__ import annotations

import base64
import json
from string import Formatter

from src.companion.config import CompanionConfig
from src.companion.i18n import MessageSpec, _M, persist_ui_lang
from src.companion.profile_store import ProfileStore, ProfileStoreError


class FakeProtector:
    prefix = "test-protected:v1:"

    def protect(self, value: str) -> str:
        return self.prefix + base64.b64encode(value.encode()).decode()

    def unprotect(self, value: str) -> str:
        if not value.startswith(self.prefix):
            raise ProfileStoreError("bad test ciphertext")
        return base64.b64decode(value[len(self.prefix) :]).decode()


def _fields(template: str) -> set[str]:
    return {
        name
        for _literal, name, _format, _conversion in Formatter().parse(template)
        if name
    }


def test_every_translation_keeps_the_same_placeholders():
    mismatched = {
        key: (_fields(chinese), _fields(thai))
        for key, (chinese, thai) in _M.items()
        if _fields(chinese) != _fields(thai)
    }
    assert mismatched == {}


def test_nested_failure_status_retranslates_without_losing_its_reason():
    status = MessageSpec(
        "pair_fail",
        {"err": MessageSpec("pair_err_network")},
    )

    assert status.render("zh") == "配对失败：无法连接 Pearnly，请检查网络后重试。"
    assert "เชื่อมต่อ Pearnly ไม่ได้" in status.render("th")


def test_language_persists_globally_without_mutating_profiles(tmp_path):
    path = tmp_path / "companion.json"
    store = ProfileStore.load(path, protector=FakeProtector())
    profile = store.new_runtime_config()
    profile.update(
        bearer_token="exp_profile_secret",
        account_dir=r"S:\\EXP68\\ACME",
        account_set=r"S:\\EXP68\\ACME",
        account_company="Acme",
        paired=True,
    )
    store.save_profile(profile)
    before = json.loads(path.read_text(encoding="utf-8"))["profiles"]

    assert persist_ui_lang(store, "th") == "th"

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["global"]["ui_lang"] == "th"
    assert document["profiles"] == before
    runtime = store.runtime_configs(include_paused=True)[0]
    assert runtime.ui_lang == "th"
    assert runtime.account_dir == profile.account_dir


def test_new_runtime_language_save_never_requires_a_profile(tmp_path):
    path = tmp_path / "companion.json"
    store = ProfileStore.load(path, protector=FakeProtector())

    persist_ui_lang(store, "zh")

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["global"]["ui_lang"] == "zh"
    assert document["profiles"] == []
    assert CompanionConfig(**document["global"]).ui_lang == "zh"
