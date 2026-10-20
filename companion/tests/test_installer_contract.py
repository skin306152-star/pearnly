import hashlib
import re
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

COMPANION = Path(__file__).resolve().parents[1]
PACKAGING = COMPANION / "packaging"
INSTALLER_SCRIPT = PACKAGING / "installer.iss"


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
    # 自动更新时安装包是 companion.exe 拉起的子进程：按进程树结束（/T）会连安装包一起杀掉
    kill = code.split("procedure KillRunning", 1)[1].split("end;", 1)[0]
    assert "/IM companion.exe" in kill and "/T" not in kill


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


def test_the_old_folder_name_is_never_shown_and_manual_installs_open_cc():
    # 2026-10-13：选目录页、Ready 页的目标位置、重装时的「文件夹已存在」都会把目录亮给用户。
    # 2026-10-20 起目录本身也叫 CC；一键安装没有完成页，手动安装装完直接打开 CC。
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")
    setup = script.split("[Setup]", 1)[1].split("[InstallDelete]", 1)[0]
    lines = {line.split("=", 1)[0].strip().lower(): line.split("=", 1)[1].strip()
             for line in setup.splitlines() if "=" in line and not line.strip().startswith(";")}
    assert lines["disabledirpage"].lower() == "yes"
    assert "alwaysshowdironreadypage" not in lines  # 默认 no：不选目录时 Ready 页不列目录
    assert lines["defaultdirname"] == "{autopf}\\CC"  # 老目录「Pearnly Companion」由 [Code] 迁移（见下）
    run = script.split("[Run]", 1)[1].split("[Code]", 1)[0]
    manual = next(line for line in run.splitlines() if "skipifsilent" in line)
    assert 'Filename: "{app}\\companion.exe"' in manual and "postinstall" not in manual  # 没有完成页时不等勾选
    assert "nowait" in manual and "Parameters" not in manual  # 不带 --background：打开 CC 窗口
    # 用户看得到的名字（程序名、卸载项、版本信息、快捷方式、任务、完成页）里没有旧称
    shown = [lines[key] for key in ("appname", "uninstalldisplayname", "versioninfoproductname",
                                    "versioninfodescription", "defaultgroupname")]
    for section in ("[Icons]", "[Tasks]", "[Run]"):
        body = script.split(section, 1)[1].split("\n[", 1)[0]
        shown += re.findall(r'(?<![A-Za-z])(?:Name|Description|GroupDescription): "([^"]*)"', body)
    assert shown and not [text for text in shown if re.search(r"(?i)companion|helper|小助手|ผู้ช่วย", text)]


def test_the_app_and_its_installer_are_named_cc():
    # 2026-10-12 小助手统一改名 CC：任务管理器 / 开机启动 / 文件属性显示「CC」，安装包叫 CC-Setup.exe；
    # exe 文件名不改；安装目录 2026-10-20 起也叫 CC（见 test_upgrades_move_the_old_folder_to_cc）
    spec = (INSTALLER_SCRIPT.parent / "companion.spec").read_text(encoding="utf-8")
    assert 'StringStruct("FileDescription", "CC")' in spec and 'StringStruct("ProductName", "CC")' in spec
    assert "version=_version_info" in spec
    iss = INSTALLER_SCRIPT.read_text(encoding="utf-8")
    assert "OutputBaseFilename=CC-Setup" in iss and "VersionInfoProductName=CC" in iss



def _section(script: str, name: str) -> str:
    return script.split(name, 1)[1].split("\n[", 1)[0]


def _png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


def test_upgrades_move_the_old_folder_to_cc():
    # 教程问题第 8 条（2026-10-20）：安装进度页显示「…\Programs\Pearnly Companion\_internal\…」。目录改叫 CC，
    # AppId 不变（同一个卸载项、同一套升级）；老目录里的整份程序在装好新版后删掉，开机自启与任务栏固定的快捷方式改指新位置。
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")
    assert "AppId={{8F3C2A91-4E7B-4D2A-9C11-PEARNLYCOMP01}" in script
    code = script.split("[Code]", 1)[1]
    assert "LegacyDirName = 'Pearnly Companion';" in code
    assert "ExpandConstant('{userpf}\\') + LegacyDirName" in code  # 2026-10-08 起的按用户安装目录
    post = code.split("procedure CurStepChanged", 1)[1].split("end;\n\n", 1)[0]
    assert "ssPostInstall" in post and "RepointAutostart" in post and "RemoveLegacyDir" in post
    remove = code.split("procedure RemoveLegacyDir", 1)[1].split("procedure CurStepChanged", 1)[0]
    assert "DelTree(LegacyDir, True, True, True)" in remove
    assert "CompareText(LegacyDir, ExpandConstant('{app}')) = 0" in remove  # 万一新旧是同一个目录，不删
    # 自动更新（updater.run_installer_silent）不带 /DIR：静默升级装到 DefaultDirName，也就是新目录
    updater = (COMPANION / "src" / "companion" / "updater.py").read_text(encoding="utf-8")
    assert '"/VERYSILENT"' in updater and "/DIR" not in updater


def test_autostart_value_is_the_one_the_app_writes():
    # 教程问题第 6 条：开机自启项由程序写（autostart.py），卸载程序以前不知道它，卸载后还指向已删的 companion.exe
    sys.path.insert(0, str(COMPANION))
    from src.companion import autostart

    code = INSTALLER_SCRIPT.read_text(encoding="utf-8").split("[Code]", 1)[1]
    assert f"RunKey = '{autostart._RUN_KEY}';" in code
    assert f"RunValue = '{autostart._VALUE_NAME}';" in code
    # 安装包改指的命令与程序自己写的同一写法（autostart.repoint 才不会又改一遍）
    assert "Result := '\"' + ExpandConstant('{app}\\companion.exe') + '\" --background';" in code


def test_uninstall_removes_autostart_and_the_empty_local_folder_but_keeps_user_data():
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")
    code = script.split("[Code]", 1)[1]
    uninstall = code.split("procedure CurUninstallStepChanged", 1)[1]
    assert "usUninstall" in uninstall and "KillRunning" in uninstall  # 先关掉在跑的 CC，companion.exe 才删得掉
    assert "usPostUninstall" in uninstall and "RegDeleteValue(HKCU, RunKey, RunValue)" in uninstall
    assert "not FileExists(Target)" in uninstall  # 1.9.1 及以前卸载留下、指向已删程序的那一项也清掉
    removed = _section(script, "[UninstallDelete]")
    entries = [line for line in removed.splitlines() if line.startswith("Type:")]
    assert entries == ['Type: dirifempty; Name: "{localappdata}\\Pearnly\\write-backups"',
                       'Type: dirifempty; Name: "{localappdata}\\Pearnly"']
    # 配置、日志、配对（%APPDATA%\Pearnly）与设备密钥备份（HKCU\Software\Pearnly）照旧不删
    assert "{userappdata}\\Pearnly" not in script and "{appdata}\\Pearnly" not in script
    assert "Software\\Pearnly" not in code


# 原样拷进来的 Inno Setup 译文（标签 is-6_7_1）：换文件时一并更新这里
LANGUAGE_FILES = {
    "Thai.isl": "cdc0a1af65ac503ee1f0b5ef9d14d81b8c173ba80a3ce78ba86e15e285a149b3",
    "ChineseSimplified.isl": "7d544b9bb1d142cfa11f2e5d3cc8abe2e55f8e066c5124e3772675aa236e1278",
}


def test_installer_follows_the_windows_language_with_english_as_fallback():
    # 教程问题第 7 条：泰文系统上向导全是英文
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")
    setup = _section(script, "[Setup]")
    assert "ShowLanguageDialog=no" in setup and "LanguageDetectionMethod=uilanguage" in setup
    languages = [line for line in _section(script, "[Languages]").splitlines() if line.startswith("Name:")]
    names = [re.search(r'Name: "([^"]+)"', line).group(1) for line in languages]
    assert names == ["english", "thai", "chinesesimplified"]  # 认不出的语言用第一个
    assert 'MessagesFile: "compiler:Default.isl"' in languages[0]
    for name, digest in LANGUAGE_FILES.items():
        path = PACKAGING / "languages" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, f"{name} changed: update the source note"
        assert f'MessagesFile: "languages\\{name}"' in script
    # 看得到的字只有 Inno 译文自带的（取消、确认、出错）；窗口标题只写产品名，没有自写的多语言文案
    assert "[CustomMessages]" not in script and "{cm:" not in script
    assert [line for line in _section(script, "[Messages]").splitlines() if "=" in line] == ["SetupWindowTitle=%1"]
    assert "AppVerName={#AppName}" in setup


def test_one_click_install_shows_only_the_icon_a_progress_bar_and_cancel():
    # 2026-10-20 用户：「谁家产品安装的时候把 logo 放右上角？」照主流桌面产品一键安装：不出向导页，只有一个小窗口
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")
    setup = _section(script, "[Setup]")
    for page in ("Welcome", "Dir", "ProgramGroup", "Ready", "Finished"):
        assert f"Disable{page}Page=yes" in setup, page
    assert "WizardImageFile" not in setup and "WizardSmallImageFile" not in setup
    code = script.split("[Code]", 1)[1]
    skip = code.split("function ShouldSkipPage", 1)[1].split("end;", 1)[0]
    assert "wpSelectTasks" in skip  # 附加任务页不出，沿用上次的选择
    wizard = code.split("procedure WizardCompact", 1)[1].split("end;", 1)[0]
    for hidden in ("MainPanel", "Bevel1", "Bevel", "OuterNotebook", "NextButton", "BackButton"):
        assert f"WizardForm.{hidden}.Visible := False;" in wizard, hidden  # 页头小图、文件名、「正在提取文件」都在里面
    layout = code.split("function CompactLayout", 1)[1].split("\nend;", 1)[0]
    assert "InitializeBitmapImageFromIcon(Icon, IconFile, clWhite" in layout  # 按 DPI 从 cc.ico 取一档，不拉伸
    assert "Bar.Parent := Form;" in layout and "Cancel.Left" in layout and "Form.ClientHeight" in layout
    assert "Stretch" not in layout
    init = code.split("procedure InitializeWizard", 1)[1].split("end;", 1)[0]
    assert "ExtractTemporaryFile('cc.ico')" in init and "WizardCompact" in init
    files = [line for line in _section(script, "[Files]").splitlines() if line.startswith("Source:")]
    assert files[0].startswith('Source: "..\\assets\\cc.ico"')  # 固实压缩：放第一个，窗口一打开就解得出来
    # 要人选择的「准备安装」页一出现就恢复官方布局
    page = code.split("procedure CurPageChanged", 1)[1].split("\nend;", 1)[0]
    assert "wpPreparing" in page and "WizardForm.OuterNotebook.Visible := True;" in page
    # 卸载：系统的确认框 + 同样的小窗口进度
    uninstall = code.split("procedure InitializeUninstallProgressForm", 1)[1].split("\nend;", 1)[0]
    assert "UninstallProgressForm.OuterNotebook.Visible := False;" in uninstall
    assert "CompactLayout(UninstallProgressForm, UninstallProgressForm.ProgressBar" in uninstall
    assert (COMPANION / "assets" / "cc.ico").read_bytes()[:4] == b"\x00\x00\x01\x00"


def test_store_package_manifest():
    # 教程问题第 1 条：商店重新签名的 MSIX。开机自启用 StartupTask 带 --background；全信任桌面程序。
    sys.path.insert(0, str(PACKAGING / "msix"))
    import build_msix
    from src.companion import package_identity

    assert build_msix.package_version("1.9.1") == "1.9.1.0"
    assert build_msix.package_version("1.9.1.3") == "1.9.1.0"  # 商店要求第四段是 0
    with pytest.raises(ValueError):
        build_msix.package_version("")
    text, placeholder = build_msix.render_manifest("1.9.1", environ={})
    assert placeholder
    text, placeholder = build_msix.render_manifest("1.9.1", environ={
        "MSIX_IDENTITY_NAME": "12345Pearnly.CC", "MSIX_PUBLISHER": "CN=ABCD-1234",
        "MSIX_PUBLISHER_DISPLAY_NAME": "MR. ERP COMPANY LIMITED"})
    assert not placeholder
    root = ET.fromstring(text)
    ns = {"m": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
          "uap": "http://schemas.microsoft.com/appx/manifest/uap/windows10",
          "uap10": "http://schemas.microsoft.com/appx/manifest/uap/windows10/10",
          "desktop": "http://schemas.microsoft.com/appx/manifest/desktop/windows10",
          "rescap": "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"}
    identity = root.find("m:Identity", ns)
    assert identity.get("Name") == "12345Pearnly.CC" and identity.get("Publisher") == "CN=ABCD-1234"
    assert identity.get("Version") == "1.9.1.0" and identity.get("ProcessorArchitecture") == "x64"
    assert root.find("m:Properties/m:DisplayName", ns).text == "CC"
    app = root.find("m:Applications/m:Application", ns)
    assert app.get("Executable") == "companion.exe" and app.get("EntryPoint") == "Windows.FullTrustApplication"
    startup = app.find("m:Extensions/desktop:Extension", ns)
    assert startup.get("Category") == "windows.startupTask"
    assert startup.get("{%s}Parameters" % ns["uap10"]) == "--background"
    assert startup.find("desktop:StartupTask", ns).get("TaskId") == package_identity.STARTUP_TASK_ID
    assert [c.get("Name") for c in root.findall("m:Capabilities/rescap:Capability", ns)] == ["runFullTrust"]
    assert root.find("m:Dependencies/m:TargetDeviceFamily", ns).get("MinVersion") == "10.0.19041.0"  # uap10:Parameters
    # 清单引用的图都在、尺寸对（packaging/msix/make_assets.py 生成）
    visual = app.find("uap:VisualElements", ns)
    refs = {visual.get("Square150x150Logo"): (150, 150), visual.get("Square44x44Logo"): (44, 44),
            visual.find("uap:DefaultTile", ns).get("Wide310x150Logo"): (310, 150),
            root.find("m:Properties/m:Logo", ns).text: (50, 50)}
    for ref, size in refs.items():
        assert _png_size(PACKAGING / "msix" / ref.replace("\\", "/")) == size, ref


def test_code_comments_do_not_nest_braces():
    # Pascal 的花括号注释不能嵌套：注释里写 {app} 会在第一个右括号处提前结束注释，编译报语法错（2026-10-20 CI 踩过）
    code = INSTALLER_SCRIPT.read_text(encoding="utf-8").split("[Code]", 1)[1]
    code = re.sub(r"'[^'\n]*'", "''", code)  # 字符串里的 {app} 是常量，不算
    depth, nested = 0, []
    for position, char in enumerate(code):
        if char == "{":
            depth += 1
            if depth > 1:
                nested.append(code[max(0, position - 40):position + 10])
        elif char == "}":
            depth = max(0, depth - 1)
    assert not nested
