import re
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


def test_tomo_installs_as_a_folder_and_silent_updates_restart_in_the_background():
    # 2026-10-10：并入 Tomo 桌面端：打成文件夹（带网页内核）；自动更新后在后台重启，不弹 Tomo 窗口打断人
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")
    assert 'Source: "..\\dist\\companion\\*"' in script and "recursesubdirs" in script
    assert 'Type: filesandordirs; Name: "{app}\\_internal"' in script
    run = script.split("[Run]", 1)[1].split("[Code]", 1)[0]
    silent = next(line for line in run.splitlines() if "WizardSilent" in line)
    assert 'Parameters: "--background"' in silent and "not WizardSilent" not in silent
    spec = (INSTALLER_SCRIPT.parent / "companion.spec").read_text(encoding="utf-8")
    assert "COLLECT(" in spec and "exclude_binaries=True" in spec
    assert '"PySide6.QtWebEngineCore"' not in spec and '"PySide6.QtNetwork"' not in spec


def test_the_old_folder_name_is_never_shown_and_finishing_offers_launch_cc():
    # 2026-10-13：目录名为原地升级沿用「Pearnly Companion」，但选目录页、Ready 页的目标位置、重装时的
    # 「文件夹已存在」都会把它亮给用户；完成页的勾选框原来是默认的「Run companion.exe」。
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")
    setup = script.split("[Setup]", 1)[1].split("[InstallDelete]", 1)[0]
    lines = {line.split("=", 1)[0].strip().lower(): line.split("=", 1)[1].strip()
             for line in setup.splitlines() if "=" in line and not line.strip().startswith(";")}
    assert lines["disabledirpage"].lower() == "yes"
    assert "alwaysshowdironreadypage" not in lines  # 默认 no：不选目录时 Ready 页不列目录
    assert lines["defaultdirname"] == "{autopf}\\Pearnly Companion"  # 目录本身不改：已装的电脑原地升级
    run = script.split("[Run]", 1)[1].split("[Code]", 1)[0]
    manual = next(line for line in run.splitlines() if "skipifsilent" in line)
    assert 'Description: "{cm:LaunchProgram,CC}"' in manual
    # 用户看得到的名字（程序名、卸载项、版本信息、快捷方式、任务、完成页）里没有旧称
    shown = [lines[key] for key in ("appname", "uninstalldisplayname", "versioninfoproductname",
                                    "versioninfodescription", "defaultgroupname")]
    for section in ("[Icons]", "[Tasks]", "[Run]"):
        body = script.split(section, 1)[1].split("\n[", 1)[0]
        shown += re.findall(r'(?<![A-Za-z])(?:Name|Description|GroupDescription): "([^"]*)"', body)
    assert shown and not [text for text in shown if re.search(r"(?i)companion|helper|小助手|ผู้ช่วย", text)]


def test_the_app_and_its_installer_are_named_cc():
    # 2026-10-12 小助手统一改名 CC：任务管理器 / 开机启动 / 文件属性显示「CC」，安装包叫 CC-Setup.exe；
    # exe 文件名与安装目录不改（已装的电脑原地升级）
    spec = (INSTALLER_SCRIPT.parent / "companion.spec").read_text(encoding="utf-8")
    assert 'StringStruct("FileDescription", "CC")' in spec and 'StringStruct("ProductName", "CC")' in spec
    assert "version=_version_info" in spec
    iss = INSTALLER_SCRIPT.read_text(encoding="utf-8")
    assert "OutputBaseFilename=CC-Setup" in iss and "VersionInfoProductName=CC" in iss

