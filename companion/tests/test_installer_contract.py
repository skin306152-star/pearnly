from pathlib import Path

INSTALLER_SCRIPT = Path(__file__).resolve().parents[1] / "packaging" / "installer.iss"


def test_silent_update_restarts_companion_as_original_user():
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")
    run_section = script.split("[Run]", 1)[1].split("[Code]", 1)[0].lower()
    companion_entry = next(
        line
        for line in run_section.splitlines()
        if 'filename: "{app}\\companion.exe"' in line
    )

    assert "skipifsilent" not in companion_entry
    assert "runasoriginaluser" in companion_entry
    assert "nowait" in companion_entry


def test_the_running_helper_is_only_closed_once_installing_starts():
    # 2026-09-28 录制：向导一打开就关小助手，点「取消」后小助手已停又不会重启
    code = INSTALLER_SCRIPT.read_text(encoding="utf-8").split("[Code]", 1)[1]
    assert "function InitializeSetup" not in code
    prepare = code.split("function PrepareToInstall", 1)[1].split("end;", 1)[0]
    assert "KillRunning" in prepare


def test_installs_per_user_so_silent_updates_never_ask_for_admin_rights():
    # 2026-10-08：装在 Program Files 时每次自动更新都弹「用户账户控制」授权窗口，打断正在用电脑的人。
    setup = INSTALLER_SCRIPT.read_text(encoding="utf-8").split("[Setup]", 1)[1].split("[Files]", 1)[0]
    lines = {line.split("=", 1)[0].strip().lower(): line.split("=", 1)[1].strip()
             for line in setup.splitlines() if "=" in line and not line.strip().startswith(";")}
    assert lines["privilegesrequired"].lower() == "lowest"
    assert lines["defaultdirname"].startswith(("{autopf}", "{userpf}", "{localappdata}"))
    assert lines["usepreviousappdir"].lower() == "no"  # 不沿用旧版的 Program Files 目录
    assert "{commondesktop}" not in INSTALLER_SCRIPT.read_text(encoding="utf-8").lower()
