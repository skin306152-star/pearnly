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
