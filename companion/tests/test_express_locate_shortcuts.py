# -*- coding: utf-8 -*-
"""Windows Express shortcut discovery stays bounded and keeps SCCOMP as the trust gate."""

import json
import subprocess
from types import SimpleNamespace

from src.companion import express_locate


def test_powershell_51_materializes_json_array_before_wrapping():
    script = express_locate._RESOLVE_SHORTCUTS_PS

    assert "$decoded = $env:PEARNLY_EXPRESS_SHORTCUTS_JSON | ConvertFrom-Json" in script
    assert "$paths = @($decoded)" in script
    assert (
        "$paths = @($env:PEARNLY_EXPRESS_SHORTCUTS_JSON | ConvertFrom-Json)"
        not in script
    )
    assert "$shell.CreateShortcut([string]($paths[$i]))" in script


def test_windows_shortcut_dirs_cover_user_and_common_locations():
    env = {
        "USERPROFILE": r"C:\Users\accountant",
        "PUBLIC": r"C:\Users\Public",
        "APPDATA": r"C:\Users\accountant\AppData\Roaming",
        "PROGRAMDATA": r"C:\ProgramData",
        "OneDrive": r"C:\Users\accountant\OneDrive",
    }

    roots = express_locate._windows_shortcut_dirs(env)

    assert r"C:\Users\accountant\Desktop" in roots
    assert r"C:\Users\Public\Desktop" in roots
    assert (
        r"C:\Users\accountant\AppData\Roaming\Microsoft\Windows\Start Menu\Programs"
        in roots
    )
    assert r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs" in roots
    assert r"C:\Users\accountant\OneDrive\Desktop" in roots


def test_express_shortcuts_only_walk_supplied_roots(tmp_path):
    desktop = tmp_path / "Desktop"
    nested = desktop / "Accounting"
    nested.mkdir(parents=True)
    wanted = nested / "Express Accounting.lnk"
    wanted.touch()
    (desktop / "Other ERP.lnk").touch()

    assert express_locate._express_shortcuts([str(desktop)]) == [str(wanted)]


def test_resolve_shortcuts_uses_bounded_non_shell_powershell(monkeypatch):
    shortcuts = [r"C:\Users\Public\Desktop\Express.lnk"]
    output = [
        {
            "index": 0,
            "target": r"\\Accserver\d$\ACCOUNT\69EXP\ExpressI.exe",
            "working_dir": r"\\Accserver\d$\ACCOUNT\69EXP",
        }
    ]
    called = {}

    def fake_run(command, **kwargs):
        called["command"] = command
        called["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=json.dumps(output))

    monkeypatch.setattr(
        express_locate, "_powershell_executable", lambda: "powershell.exe"
    )
    monkeypatch.setattr(express_locate.subprocess, "run", fake_run)

    resolved = express_locate._resolve_shortcuts(shortcuts, timeout=1.25)

    assert resolved == [
        (
            r"\\Accserver\d$\ACCOUNT\69EXP\ExpressI.exe",
            r"\\Accserver\d$\ACCOUNT\69EXP",
        )
    ]
    assert called["command"][-1] == express_locate._RESOLVE_SHORTCUTS_PS
    assert (
        json.loads(called["kwargs"]["env"]["PEARNLY_EXPRESS_SHORTCUTS_JSON"])
        == shortcuts
    )
    assert called["kwargs"]["shell"] is False
    assert called["kwargs"]["timeout"] == 1.25


def test_resolve_shortcuts_timeout_is_non_blocking_and_sanitized(monkeypatch, caplog):
    secretish_path = r"C:\Users\private-user\Desktop\Express token-secret.lnk"

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("powershell.exe", 0.1)

    monkeypatch.setattr(
        express_locate, "_powershell_executable", lambda: "powershell.exe"
    )
    monkeypatch.setattr(express_locate.subprocess, "run", timeout)

    assert express_locate._resolve_shortcuts([secretish_path], timeout=0.1) == []
    assert "private-user" not in caplog.text
    assert "token-secret" not in caplog.text


def test_shortcut_target_adds_express_root_and_one_scan_parent(monkeypatch):
    target = r"\\Accserver\d$\ACCOUNT\69EXP\ExpressI.exe"
    root_69 = r"\\Accserver\d$\ACCOUNT\69EXP"
    root_70 = r"\\Accserver\d$\ACCOUNT\70EXP"
    account_parent = r"\\Accserver\d$\ACCOUNT"
    scanned = []

    monkeypatch.setattr(express_locate.os, "name", "nt")
    monkeypatch.setattr(express_locate, "_windows_shortcut_dirs", lambda: ["desktop"])
    monkeypatch.setattr(
        express_locate, "_express_shortcuts", lambda _dirs: ["Express.lnk"]
    )
    monkeypatch.setattr(
        express_locate, "_resolve_shortcuts", lambda _links: [(target, root_69)]
    )

    def fake_scan(parents):
        scanned.extend(parents)
        return [root_70]

    monkeypatch.setattr(express_locate, "_scan_exp_roots", fake_scan)

    roots = express_locate._shortcut_candidate_roots()

    assert root_69 in roots
    assert root_70 in roots
    assert account_parent in scanned
    assert all("ExpressI.exe" not in root for root in roots)


def test_candidate_roots_includes_shortcut_discovery(monkeypatch):
    shortcut_root = r"\\Accserver\d$\ACCOUNT\69EXP"
    monkeypatch.delenv("PEARNLY_EXPRESS_ROOT", raising=False)
    monkeypatch.setattr(express_locate, "_registry_install_dirs", lambda: [])
    monkeypatch.setattr(
        express_locate, "_shortcut_candidate_roots", lambda: [shortcut_root]
    )
    monkeypatch.setattr(express_locate, "_mounted_drive_roots", lambda: [])
    monkeypatch.setattr(express_locate, "_scan_exp_roots", lambda _parents: [])

    assert express_locate.candidate_roots() == [shortcut_root]


def test_non_windows_shortcut_discovery_is_noop():
    if express_locate.os.name != "nt":
        assert express_locate._shortcut_candidate_roots() == []
