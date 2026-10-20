# -*- coding: utf-8 -*-
"""把 release.ps1 打出来的程序文件夹再打成 Microsoft Store 用的 MSIX（不签名：商店审核通过后由微软重新签名）。

    python packaging/msix/build_msix.py              # 在 companion/ 下跑，先跑过 release.ps1 -BuildOnly（要 dist\\companion）
    python packaging/msix/build_msix.py --layout-only   # 只摆好目录、填好清单，不调 MakeAppx（本机 / 非 Windows 检查用）

产物 dist\\CC.msix。包标识三项从环境变量取，照抄 Partner Center「产品管理 → 产品标识」给的值：
``MSIX_IDENTITY_NAME``（Package/Identity/Name）、``MSIX_PUBLISHER``（Package/Identity/Publisher，CN=…）、
``MSIX_PUBLISHER_DISPLAY_NAME``（Package/Properties/PublisherDisplayName）。没设时用占位值：包照样能打出来、过 MakeAppx 的
清单校验，但上传会被 Partner Center 拒（标识不符）。

不签名的包不能直接双击安装（Windows 只装签过名、且信任其证书的包），所以它只用来上传商店；真机验证走商店的私密发布
（package flight），见 docs/work/2026-10-20-cc-installer-fixes.md。现有 Inno 安装包（CC-Setup.exe）仍是主发布方式，不受影响。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPANION = HERE.parents[1]
DIST = COMPANION / "dist"
LAYOUT = DIST / "msix-layout"
OUTPUT = DIST / "CC.msix"
TEMPLATE = HERE / "AppxManifest.xml"

PLACEHOLDER_IDENTITY = {
    "IDENTITY_NAME": "Pearnly.CC",
    "PUBLISHER": "CN=MR. ERP COMPANY LIMITED",
    "PUBLISHER_DISPLAY_NAME": "MR. ERP COMPANY LIMITED",
}
ENV_KEYS = {"IDENTITY_NAME": "MSIX_IDENTITY_NAME", "PUBLISHER": "MSIX_PUBLISHER",
            "PUBLISHER_DISPLAY_NAME": "MSIX_PUBLISHER_DISPLAY_NAME"}


def package_version(version: str) -> str:
    """商店的包版本是四段数字、第四段必须是 0：1.9.1 → 1.9.1.0；1.9.1.3（内部四段版本）→ 1.9.1.0。"""
    parts = [int(part) for part in str(version).strip().split(".") if part.strip() != ""]
    if not parts or len(parts) > 4:
        raise ValueError(f"version {version!r}")
    parts = (parts + [0, 0, 0])[:3]
    return ".".join(str(part) for part in parts) + ".0"


def _xml_attr(value: str) -> str:
    return (str(value).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;"))


def render_manifest(version: str, environ=None) -> tuple[str, bool]:
    """填好的清单文本，以及标识是不是占位值（占位值打出的包不能上传）。"""
    environ = os.environ if environ is None else environ
    values = {key: (environ.get(env) or "").strip() for key, env in ENV_KEYS.items()}
    placeholder = not all(values.values())
    if placeholder:
        values = dict(PLACEHOLDER_IDENTITY)
    values["VERSION"] = package_version(version)
    text = TEMPLATE.read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{%s}}" % key, _xml_attr(value))
    left = re.findall(r"\{\{[A-Z_]+\}\}", text)
    if left:
        raise ValueError(f"unfilled manifest fields: {left}")
    return text, placeholder


def build_layout(version: str) -> bool:
    """dist\\msix-layout：程序文件夹 + cdx_reindex_runner.exe + cc.ico + Assets + AppxManifest.xml。回标识是不是占位值。"""
    program = DIST / "companion"
    if not (program / "companion.exe").is_file():
        raise SystemExit(f"missing {program / 'companion.exe'}: run packaging\\release.ps1 -BuildOnly first")
    if LAYOUT.exists():
        shutil.rmtree(LAYOUT)
    shutil.copytree(program, LAYOUT)
    runner = DIST / "cdx_reindex_runner.exe"
    if not runner.is_file():
        raise SystemExit(f"missing {runner}")
    shutil.copy2(runner, LAYOUT / runner.name)
    shutil.copy2(COMPANION / "assets" / "cc.ico", LAYOUT / "cc.ico")
    shutil.copytree(HERE / "Assets", LAYOUT / "Assets")
    manifest, placeholder = render_manifest(version)
    (LAYOUT / "AppxManifest.xml").write_text(manifest, encoding="utf-8")
    return placeholder


def find_makeappx() -> Path:
    """Windows SDK 里最新的 x64 MakeAppx.exe（GitHub 的 windows-latest 自带 SDK）。"""
    override = os.environ.get("MAKEAPPX")
    if override:
        return Path(override)
    root = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits" / "10" / "bin"
    found = sorted(root.glob("10.*/x64/makeappx.exe"), key=lambda path: [int(n) for n in path.parent.parent.name.split(".")])
    if not found:
        raise SystemExit(f"MakeAppx.exe not found under {root}")
    return found[-1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--layout-only", action="store_true")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(COMPANION))
    from src.companion.version import VERSION

    placeholder = build_layout(VERSION)
    print(f"layout: {LAYOUT} (version {package_version(VERSION)})")
    if placeholder:
        print("package identity: placeholder values (set MSIX_IDENTITY_NAME / MSIX_PUBLISHER / "
              "MSIX_PUBLISHER_DISPLAY_NAME from Partner Center before uploading)")
    if args.layout_only:
        return 0
    makeappx = find_makeappx()
    OUTPUT.unlink(missing_ok=True)
    # /o 覆盖；MakeAppx 打包时按架构校验清单（schema、引用的文件存在），不合规直接失败
    completed = subprocess.run([str(makeappx), "pack", "/d", str(LAYOUT), "/p", str(OUTPUT), "/o"])
    if completed.returncode != 0:
        return completed.returncode
    print(f"package: {OUTPUT} ({OUTPUT.stat().st_size / 1024 / 1024:.1f} MB, unsigned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
