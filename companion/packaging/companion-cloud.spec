# -*- mode: python ; coding: utf-8 -*-
"""云端精简包:云端 Express 主机上那份 CC(``companion.exe --cloud``,runtime_mode.py)· onedir · 无控制台窗。

云端那份由桥(客户自己的 CC 电脑)经远程桌面映射盘拷上云端主机(实测约 0.6–1.9 MB/s);正式包整个程序文件夹约 586 MB,
大头是 PySide6 / QtWebEngine。云端模式不起托盘、窗口、网页内核、系统通知(docs/work/2026-11-11-express-cloud-assessment.md
第 4.2 节),这份只放云端模式用得到的:直写循环、按需请求、代录 Express(只用 ctypes)、后台 CDX 索引(cdx_reindex_runner.exe 由
release.ps1 -Cloud 拷到 companion.exe 旁边)。云端路径不依赖下面排除的包由 tests/test_cloud_package.py 守住(那条测试读本文件的
_CLOUD_EXCLUDES / _OPERATOR_MODULES)。

入口是只走云端的 launcher_cloud.py(不管带不带 --cloud 都按云端跑):包里没有 Qt,托盘 / 窗口那几条路一进就是 ImportError。
输出 dist/companion-cloud/,程序名仍叫 companion.exe。从 companion/ 跑:python -m PyInstaller packaging/companion-cloud.spec
"""

import os  # noqa: E402

# 混淆(PEARNLY_OBF_DIR)同 companion.spec:按源码分析,分析后把 src.companion.* 的字节码换成混淆版。
_obf = os.environ.get("PEARNLY_OBF_DIR", "")
_use_obf = bool(_obf) and os.path.isdir(os.path.join(_obf, "src"))
_pathex = [".."]

# 代录 Express(按需导入,静态分析看得见函数里的导入,这里逐个列一遍兜底;新增模块要补进来,测试会核对目录)
_OPERATOR_MODULES = (
    "batch", "bg_desk", "book", "contract", "credentials", "desk", "draft", "engine", "evidence", "helper", "host",
    "login_dialog", "masters", "menu", "precheck", "presence", "probe", "recipe", "scheduler", "session", "win32_desk",
)
_hidden = ["httpx", "dbf"] + [f"src.companion.express_operator.{name}" for name in _OPERATOR_MODULES]
# 只留代录 Express 的配方;MR.ERP 代录在云端不起(runtime_mode / payload_contract.mrerp_operator_enabled),配方不带
_datas = [("../src/companion/express_operator/recipes/*.json", "src/companion/express_operator/recipes")]

# 云端用不上的:Qt 全家(托盘、窗口、网页内核)、Windows 通知、Store 版开机自启;以及只有界面用的本仓模块与 MR.ERP 代录包
_CLOUD_EXCLUDES = [
    "PySide6", "shiboken6", "windows_toasts", "winrt",
    "pywinauto", "comtypes", "cv2", "numpy", "pyautogui", "pytesseract", "mss", "tkinter",
    "src.companion.gui_font", "src.companion.gui_icon", "src.companion.gui_pairing_support", "src.companion.gui_quick",
    "src.companion.gui_tomo", "src.companion.gui_tray", "src.companion.gui_tray_workers", "src.companion.instance_channel",
    "src.companion.render_safety", "src.companion.tomo_bridge", "src.companion.tomo_hotkey",
    "src.companion.visibility_watch", "src.companion.window_link", "src.companion.window_main",
    "src.companion.mrerp_operator",
    # 云端 Express 桥侧（连接窗口、控件宿主、非脚本设置、宿主、设置页动作）只在客户自己的电脑上跑，云端那份用不上，
    # 且会尝试导入 Qt / comtypes（comtypes 已在上面排除）
    "src.companion.cloud_link.host", "src.companion.cloud_link.window", "src.companion.cloud_link.ax_host",
    "src.companion.cloud_link.nonscriptable", "src.companion.cloud_link.settings_link",
]

a = Analysis(
    ["launcher_cloud.py"],
    pathex=_pathex,
    binaries=[],
    datas=_datas,
    hiddenimports=_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_CLOUD_EXCLUDES,
    noarchive=False,
)

if _use_obf:
    _patched = []
    for _m in a.pure:
        _name = _m[0]
        if _name == "src" or _name.startswith("src."):
            _parts = _name.split(".")
            _cand = os.path.join(_obf, *_parts) + ".py"
            _pkg = os.path.join(_obf, *_parts, "__init__.py")
            _osrc = _cand if os.path.exists(_cand) else (_pkg if os.path.exists(_pkg) else None)
            if _osrc:
                _patched.append((_name, _osrc, "PYMODULE"))
                continue
        _patched.append(_m)
    a.pure = _patched
    a.datas += Tree(os.path.join(_obf, "pyarmor_runtime_000000"), prefix="pyarmor_runtime_000000")

# 程序信息同正式包(任务管理器里显示「CC」;exe 文件名不改)
import re as _re  # noqa: E402
from PyInstaller.utils.win32.versioninfo import (  # noqa: E402
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

with open(os.path.join(SPECPATH, "..", "src", "companion", "version.py"), encoding="utf-8") as _f:
    _ver = _re.search(r'VERSION = "([0-9.]+)"', _f.read()).group(1)
_nums = tuple(int(x) for x in (_ver.split(".") + ["0", "0", "0"])[:4])
_version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_nums, prodvers=_nums),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "Pearnly"),
            StringStruct("FileDescription", "CC"),
            StringStruct("FileVersion", _ver),
            StringStruct("InternalName", "CC"),
            StringStruct("OriginalFilename", "companion.exe"),
            StringStruct("ProductName", "CC"),
            StringStruct("ProductVersion", _ver),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="companion",
    icon="../assets/cc.ico",
    version=_version_info,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="companion-cloud",
)
