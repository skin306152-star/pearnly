# -*- mode: python ; coding: utf-8 -*-
"""64 位主程序:PySide6 托盘 + Tomo 窗口(QtWebEngine)+ DBF 直写 + queue(httpx)· onedir · 无控制台窗。

2026-10-10 起打成文件夹(onedir):Tomo 窗口带网页内核(QtWebEngineProcess + 资源约两百 MB),
onefile 每次启动都要先解包到临时目录,打开慢;装进程序目录后直接启动。

DBF 直写 + 后台 CDX 索引重建即全部录入路径,无 GUI 自动化 → exclude pywinauto/comtypes
(也免 64 位未装的 warn)。从 repo 根跑:python -m PyInstaller packaging/companion.spec
"""

import os  # noqa: E402

# T7 防逆向:PEARNLY_OBF_DIR 指向 PyArmor 混淆输出(含 src/ + pyarmor_runtime)。
# 关键:源码 ".." 排在前 → PyInstaller 按【源码】静态分析抓全依赖(stdlib/PySide6/httpx/...);
# _obf 仅供 pyarmor_runtime。分析后再把 src.companion.* 的字节码换成混淆版(见下 swap)。
# 未设 → 纯源码构建(默认·单测/调试)。
_obf = os.environ.get("PEARNLY_OBF_DIR", "")
_use_obf = bool(_obf) and os.path.isdir(os.path.join(_obf, "src"))
_pathex = [".."]  # 始终纯【源码】分析(抓全依赖);混淆只在分析后替换字节码(见下 swap),
_hidden = ["httpx", "dbf"]  # 故不把 _obf 放 pathex(否则分析的是看不见 import 的混淆码)。

a = Analysis(
    ["launcher_companion.py"],
    pathex=_pathex,
    binaries=[],
    datas=[("../assets/cc.png", "assets"), ("../assets/pearnly.png", "assets")],  # 托盘/窗口图标(spec 相对路径=packaging/·故 ..)
    hiddenimports=_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pywinauto", "comtypes",  # 无 GUI 自动化(DBF 直写 + 后台 CDX 索引)
        # T6 瘦身:cv2/numpy(~60MB)+ 纯模拟录入(RPA)/校准依赖 —— 直录(DBF)主路不用,
        # 已懒加载、启动不触发(故可排除);RPA 上线用另一套 build profile 重新 include。
        "cv2", "numpy", "pyautogui", "pytesseract", "mss",
        # 未用 Qt 模块(用到 QtCore/QtGui/QtWidgets、QtNetwork 本地管道,以及 Tomo 窗口的 QtWebEngine 及其依赖)
        "PySide6.Qt3DCore",
        "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
        "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtBluetooth", "PySide6.QtSensors",
        "PySide6.QtSerialPort", "PySide6.QtSvg", "PySide6.QtDesigner",
    ],
    noarchive=False,
)

if _use_obf:
    # 源码分析已抓全依赖;此处只把 src.companion.* 的源码字节码替换成 PyArmor 混淆版。
    # 模块名 src.companion.X 本身含 src → 路径基准用 _obf(非 _obf/src),否则 src/src 双重错位不命中。
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
    # 分析未见 pyarmor_runtime(混淆码后注入)→ 手动把运行时包打进 _MEIPASS(可 import 解密)。
    a.datas += Tree(os.path.join(_obf, "pyarmor_runtime_000000"), prefix="pyarmor_runtime_000000")

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="companion",
    icon="../assets/cc.ico",  # 桌面/任务栏图标 = CC（小尺寸去胡须）
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
    name="companion",
)
