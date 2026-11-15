# -*- coding: utf-8 -*-
"""云端精简包（packaging/companion-cloud.spec，入口 packaging/launcher_cloud.py）：包里不带 PySide6 / shiboken6 / Windows 通知 /
winrt，也不带只有界面用的本仓模块与 MR.ERP 代录包。这里在子进程里把 spec 排除的东西全挡掉（导入即 ImportError），证明：

1. 包里留下的每个 ``src.companion`` 模块（直写、按需请求、代录 Express、CDX 索引……）都导入得了；
2. 精简包的入口照用户的用法跑（不带参数也按 ``--cloud``）：日志有「云端模式」、没有成员以 2 退出，不碰挡掉的包。

打出来的包有多大、``companion.exe --cloud`` 能不能起，只能在 Windows 上打包后验（companion-build.yml 勾 cloud、smoke_launch.py --cloud-only）。
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]          # companion/
SPEC = ROOT / "packaging" / "companion-cloud.spec"
LAUNCHER = ROOT / "packaging" / "launcher_cloud.py"
PACKAGE = ROOT / "src" / "companion"
THIRD_PARTY = ("PySide6", "shiboken6", "windows_toasts", "winrt")


def _spec_values() -> dict:
    """spec 里顶层赋值的字面量（_CLOUD_EXCLUDES、_OPERATOR_MODULES、_datas）；spec 在 macOS 上不能真跑（PyInstaller 的 Analysis）。"""
    tree = ast.parse(SPEC.read_text(encoding="utf-8"), filename=str(SPEC))
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                values[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                continue
    return values


def _calls(name: str) -> list:
    tree = ast.parse(SPEC.read_text(encoding="utf-8"))
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call) and getattr(node.func, "id", "") == name]


def _keyword(call: ast.Call, name: str):
    return next(ast.literal_eval(k.value) for k in call.keywords if k.arg == name)


def _package_modules(excluded) -> list:
    names = []
    for path in sorted(PACKAGE.rglob("*.py")):
        name = ".".join(("src",) + path.relative_to(PACKAGE.parent).with_suffix("").parts)
        name = name[: -len(".__init__")] if name.endswith(".__init__") else name
        if not any(name == item or name.startswith(item + ".") for item in excluded):
            names.append(name)
    return names


def test_the_spec_excludes_qt_notifications_and_the_window_code():
    excludes = _spec_values()["_CLOUD_EXCLUDES"]
    for name in THIRD_PARTY + ("src.companion.gui_tray", "src.companion.window_main", "src.companion.mrerp_operator"):
        assert name in excludes
    (analysis,) = _calls("Analysis")
    assert ast.literal_eval(analysis.args[0]) == ["launcher_cloud.py"]


def test_the_spec_keeps_only_the_express_recipes_and_names_the_program_companion():
    datas = _spec_values()["_datas"]
    assert datas == [("../src/companion/express_operator/recipes/*.json", "src/companion/express_operator/recipes")]
    (exe,) = _calls("EXE")
    (collect,) = _calls("COLLECT")
    assert _keyword(exe, "name") == "companion"            # 桥用同一个名字启动
    assert _keyword(collect, "name") == "companion-cloud"  # dist/companion-cloud/


def test_every_express_operator_module_is_listed_in_the_spec():
    listed = set(_spec_values()["_OPERATOR_MODULES"])
    present = {p.stem for p in (PACKAGE / "express_operator").glob("*.py") if p.stem != "__init__"}
    assert listed == present


# 子进程：挡掉 spec 排除的一切 → 导入包里留下的每个模块 → 照精简包入口跑到「没有成员以 2 退出」
_CHILD = r"""
import importlib, importlib.abc, json, runpy, sys
blocked, modules, launcher, lock_name = json.loads(sys.argv[1])
hits = []

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if any(name == item or name.startswith(item + ".") for item in blocked):
            hits.append(name)
            raise ImportError("not in the cloud package: " + name)
        return None

sys.meta_path.insert(0, Block())
try:
    import PySide6  # noqa: F401
except ImportError:
    pass
else:
    raise SystemExit("blocker not active")
hits.clear()
for name in modules:
    importlib.import_module(name)
from src.companion import single_instance
_acquire = single_instance.acquire
single_instance.acquire = lambda name=lock_name: _acquire(lock_name)   # 不和这台电脑上正在跑的 CC 抢同一把锁
sys.argv = [launcher]
try:
    runpy.run_path(launcher, run_name="__main__")
    code = 0
except SystemExit as exc:
    code = exc.code
print("RESULT " + json.dumps({"code": code, "hits": hits,
                              "loaded": sorted(m for m in sys.modules if m.split(".")[0] in %r)}))
""" % (THIRD_PARTY,)


@pytest.fixture
def clean_home(tmp_path):
    env = dict(os.environ)
    for key in ("PEARNLY_CC_CLOUD", "PEARNLY_CLOUD_VIA", "PEARNLY_COMPANION_PORT", "QT_QPA_PLATFORM"):
        env.pop(key, None)
    env.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path), APPDATA=str(tmp_path / "Roaming"),
               LOCALAPPDATA=str(tmp_path / "Local"), XDG_CONFIG_HOME=str(tmp_path / "config"), PYTHONUTF8="1")
    return env, tmp_path


def _log_text(home: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in home.rglob("companion.log"))


def test_the_cloud_package_runs_without_qt_or_notifications(clean_home):
    env, home = clean_home
    blocked = list(THIRD_PARTY) + [name for name in _spec_values()["_CLOUD_EXCLUDES"] if name.startswith("src.")]
    modules = _package_modules(blocked)
    assert "src.companion.main" in modules and "src.companion.express_operator.host" in modules
    args = json.dumps([blocked, modules, str(LAUNCHER), f"PearnlyTest_CloudPackage_{os.getpid()}"])
    done = subprocess.run([sys.executable, "-c", _CHILD, args], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    line = next((x for x in done.stdout.splitlines() if x.startswith("RESULT ")), None)
    assert line, done.stdout + done.stderr
    result = json.loads(line[len("RESULT "):])
    assert result["code"] == 2, done.stdout + done.stderr   # 没有成员：同 --headless 以 2 退出
    assert result["hits"] == [] and result["loaded"] == []
    assert "云端模式" in _log_text(home)


@pytest.mark.parametrize("flag", ["--window", "--mrerp-desk"])
def test_the_cloud_package_refuses_the_window_processes(clean_home, flag):
    env, _home = clean_home
    code = (
        "import runpy, sys\n"
        f"sys.argv = [{str(LAUNCHER)!r}, {flag!r}]\n"
        f"runpy.run_path({str(LAUNCHER)!r}, run_name='__main__')\n"
    )
    done = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, timeout=60)
    assert done.returncode == 2
