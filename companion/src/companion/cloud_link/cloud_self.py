# -*- coding: utf-8 -*-
r"""云端那份 CC 管自己的安装（段 ③ 里不依赖 V1 的两件，2026-11-25 主会话批）：

- **自升级**（:func:`upgrade`）：桥每次起宿主都 ``cloud_package.refresh``——发布的云端包版本变了就重放隧道盘 ``cc\``
  （``CC-Cloud.zip`` + ``cloud.json``）。云端这边每次看 ``ctl``（:func:`cloud_side.check_ctl`）顺带读 ``cc\cloud.json``：
  版本比自己新、自己是装在 ``%LOCALAPPDATA%\Pearnly\CC-Cloud\<版本>\companion.exe`` 的那份，就把包拷到本机、核 sha256、
  解压到 ``CC-Cloud\<新版本>``（与在跑的不是同一个目录，不怕占用），再起一个隐藏的 PowerShell 小进程：等本进程退出 →
  启动文件夹里**指向我们目录的** ``CC.lnk`` 改指新版本 → 启动新版本 ``companion.exe --cloud``；本进程随即退出（:attr:`CloudLink.quit`）。
  旧版本目录留着（回退用），不删。失败只记日志，同一版本不反复试（直到 ``cloud.json`` 换了版本）。
- **断开连带删**（:func:`handle_uninstall`）：桥「断开」时经 ``ctl\uninstall.json``（当前会话加密，``ctl_files``）请云端删掉我们装的
  东西。只删两样，删前各自校验：
  1. 安装目录：路径**写死**为 ``%LOCALAPPDATA%\Pearnly\CC-Cloud``，且本进程的 exe 就在它下面一层的版本目录里，且目录里有我们的
     标记文件 ``.pearnly-cc-cloud``（内容固定）——三条都对才删；
  2. 启动文件夹的 ``CC.lnk``：只有指向上面那个目录的才删（同 ``cloud_install.ps1`` 配对失败时的判法）。
  别的（配置、凭据、日志以外的目录、别人的快捷方式）一概不碰。删前回读一次（回执里写目录与快捷方式在不在），删要等本进程退出后由
  小进程做，删后小进程再回读一次，结果写一行到 ``%LOCALAPPDATA%\Pearnly\cc-cloud-uninstall.txt``（在被删目录外，夜间真机核对用）。
  回执 ``ctl\uninstalled.json``（同一编号）写完本进程退出。

本模块不导入 Qt、不导入 ``cloud_package``（云端精简包里都没有）；Windows 以外只做到「备好命令」为止（测试注入 ``spawn``）。
日志只有事件名、版本与原因码。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Callable, Mapping, Optional

from ..logger import get_logger

log = get_logger(__name__)

CC_DIR = "cc"                      # 同 cloud_side.CC_DIR / cloud_package.CC_DIR
MANIFEST = "cloud.json"            # 同 cloud_package.MANIFEST
PACKAGE = "CC-Cloud.zip"           # 同 cloud_package.PACKAGE
EXE = "companion.exe"
LINK = "CC.lnk"
MARKER = ".pearnly-cc-cloud"
MARKER_TEXT = "pearnly-cc-cloud v1\n"
TRACE = "cc-cloud-uninstall.txt"
UPGRADE_TRACE = "cc-cloud-upgrade.txt"
_VERSION = re.compile(r"^\d{1,4}(\.\d{1,4}){2,3}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_PACKAGE = 200 * 1024 * 1024

# 结果码
NONE = "none"                      # 不用升级（没放包 / 不比自己新 / 这一版试过失败）
NOT_INSTALLED = "not_installed"    # 本进程不是装在我们目录里的那份（开发机、手动跑），不动
STAGED = "staged"                  # 新版本已解压、小进程已起，本进程要退出
CHECKSUM = "checksum"
BAD_PACKAGE = "bad_package"
FAILED = "failed"
SCHEDULED = "scheduled"            # 删的小进程已起，本进程要退出
NOT_OURS = "not_ours"              # 目录 / 标记对不上，什么都没删


# ── 我们的目录 ────────────────────────────────────────────────────────────────

def install_base(env: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    r"""写死的安装目录 ``%LOCALAPPDATA%\Pearnly\CC-Cloud``；没有 ``LOCALAPPDATA``（不是 Windows 用户会话）回 None。"""
    env = os.environ if env is None else env
    local = str(env.get("LOCALAPPDATA") or "").strip()
    return Path(local) / "Pearnly" / "CC-Cloud" if local else None


def _norm(path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def installed_dir(base: Optional[Path], exe=None) -> Optional[Path]:
    r"""本进程的 exe 是 ``<base>\<版本>\companion.exe`` 时回那个版本目录，否则 None（开发机、别处手动跑的不算我们装的）。"""
    if base is None:
        return None
    exe = Path(exe if exe is not None else sys.executable)
    folder = exe.parent
    if exe.name.lower() != EXE or not _VERSION.match(folder.name) or _norm(folder.parent) != _norm(base):
        return None
    return folder


def ensure_marker(base: Optional[Path], exe=None) -> bool:
    """装在我们目录里的那份启动时补标记文件（11-25 之前装的没有）；写不进只记日志。回标记是否就位。"""
    if installed_dir(base, exe) is None:
        return False
    marker = Path(base) / MARKER
    try:
        if _marker_ok(marker.read_text(encoding="utf-8-sig")):
            return True
    except (OSError, UnicodeDecodeError):
        pass
    try:
        marker.write_text(MARKER_TEXT, encoding="utf-8")
        return True
    except OSError as exc:
        log.warning("云端安装标记写入失败 · 原因=%s", type(exc).__name__)
        return False


def ours(base: Optional[Path], exe=None) -> bool:
    """可以删：路径写死对得上、本进程在它下面、标记文件内容对。"""
    if installed_dir(base, exe) is None:
        return False
    try:
        return _marker_ok((Path(base) / MARKER).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError):
        return False


def _marker_ok(text: str) -> bool:
    return str(text).strip() == MARKER_TEXT.strip()


# ── 自升级 ────────────────────────────────────────────────────────────────────

def version_key(text: str) -> tuple:
    return tuple(int(part) for part in str(text).split("."))


def placed_manifest(root: Path) -> Optional[dict]:
    r"""桥放进隧道盘的 ``cc\cloud.json`` → ``{version, sha256, size}``；没有或不合规回 None。"""
    try:
        with open(Path(root) / CC_DIR / MANIFEST, "rb") as handle:
            data = json.loads(handle.read(64 * 1024).decode("utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    version = str(data.get("version") or "").strip()
    sha256 = str(data.get("sha256") or "").strip().lower()
    size = data.get("size")
    if not _VERSION.match(version) or not _SHA256.match(sha256):
        return None
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_PACKAGE:
        return None
    return {"version": version, "sha256": sha256, "size": size}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage(root: Path, base: Path, manifest: dict) -> Path:
    r"""把 ``cc\CC-Cloud.zip`` 拷到本机、核 sha256、解压到 ``<base>\<版本>``（已有 companion.exe 就不重解压）；回新 exe。
    校验不对抛 ``ValueError(CHECKSUM)``，包里没有 companion.exe 抛 ``ValueError(BAD_PACKAGE)``；临时文件一律收掉。"""
    base = Path(base)
    dest = base / manifest["version"]
    exe = dest / EXE
    token = uuid.uuid4().hex
    local = base / f"CC-Cloud-{token}.zip"
    staging = base / f"{manifest['version']}.partial-{token}"
    try:
        shutil.copyfile(Path(root) / CC_DIR / PACKAGE, local)
        if local.stat().st_size != manifest["size"] or _sha256(local) != manifest["sha256"]:
            raise ValueError(CHECKSUM)
        if not exe.is_file():
            with zipfile.ZipFile(local) as archive:
                archive.extractall(staging)   # extractall 会去掉绝对路径与 ..（不会写到 staging 外）
            if not (staging / EXE).is_file():
                raise ValueError(BAD_PACKAGE)
            if dest.exists():
                shutil.rmtree(dest)
            os.replace(staging, dest)
        return exe
    except zipfile.BadZipFile:
        raise ValueError(BAD_PACKAGE) from None
    finally:
        for path in (local, staging):
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
            except OSError:
                pass


def _ps(text: str) -> str:
    """PowerShell 单引号字面量。"""
    return "'" + str(text).replace("'", "''") + "'"


def _link_lines(base: Path, then: str) -> list:
    """启动文件夹的 CC.lnk 指向我们目录时执行 ``then``（拿 ``$l`` 当快捷方式对象）；别人的不碰。"""
    return [
        "$p = Join-Path ([Environment]::GetFolderPath('Startup')) " + _ps(LINK),
        "if (Test-Path -LiteralPath $p) {",
        "  $s = New-Object -ComObject WScript.Shell; $l = $s.CreateShortcut($p)",
        "  if ([string]$l.TargetPath -and ([string]$l.TargetPath).StartsWith(" + _ps(str(base) + "\\")
        + ", [System.StringComparison]::OrdinalIgnoreCase)) { " + then + " }",
        "}",
    ]


def _trace_line(trace: Path, text: str) -> str:
    """小进程往目录外的 trace 文件追一行（夜间真机核对：小进程起没起来、走到哪一步）。"""
    return "Add-Content -LiteralPath " + _ps(trace) + " -Value ((Get-Date).ToString('s') + ' " + text + "')"


def upgrade_script(pid: int, base: Path, exe: Path) -> str:
    """小进程：等 ``pid`` 退出 → 指向我们目录的 CC.lnk 改指 ``exe`` → 起 ``exe --cloud``；起止各写一行到目录外的 trace。"""
    folder = Path(exe).parent
    trace = Path(base).parent / UPGRADE_TRACE
    lines = [
        _trace_line(trace, "upgrade helper started pid=" + str(int(pid))),
        f"Wait-Process -Id {int(pid)} -ErrorAction SilentlyContinue",
        *_link_lines(base, "$l.TargetPath = " + _ps(exe) + "; $l.Arguments = '--cloud'; $l.WorkingDirectory = "
                     + _ps(folder) + "; $l.Save()"),
        "Start-Process -FilePath " + _ps(exe) + " -ArgumentList '--cloud' -WorkingDirectory " + _ps(folder),
        _trace_line(trace, "upgrade helper started the new version"),
    ]
    return "; ".join(lines)


def uninstall_script(pid: int, base: Path) -> str:
    """小进程：等 ``pid`` 退出 → 删指向我们目录的 CC.lnk → 标记文件还对才删 ``base`` → 回读一次，结果写一行到目录外的 trace。"""
    trace = Path(base).parent / TRACE
    lines = [
        "Set-Location -LiteralPath " + _ps(Path(base).parent),   # 小进程自己不待在要删的目录里
        _trace_line(trace, "uninstall helper started pid=" + str(int(pid))),
        f"Wait-Process -Id {int(pid)} -ErrorAction SilentlyContinue",
        "$before = Test-Path -LiteralPath " + _ps(base),
        *_link_lines(base, "Remove-Item -LiteralPath $p -Force"),
        "$m = Join-Path " + _ps(base) + " " + _ps(MARKER),
        "$ok = (Test-Path -LiteralPath $m) -and ([string](Get-Content -LiteralPath $m -Raw)).Trim() -eq " + _ps(MARKER_TEXT.strip()),
        "if ($ok) { Remove-Item -LiteralPath " + _ps(base) + " -Recurse -Force -ErrorAction SilentlyContinue }",
        "$after = Test-Path -LiteralPath " + _ps(base),
        "$lnk = Test-Path -LiteralPath $p",
        "Add-Content -LiteralPath " + _ps(trace) + " -Value ((Get-Date).ToString('s') + ' marker=' + $ok + ' before=' + $before"
        " + ' after=' + $after + ' link=' + $lnk)",
    ]
    return "; ".join(lines)


def helper_command(script: str) -> list:
    """一次性 PowerShell（``-Command`` 内联，不跑脚本文件）；窗口由 :func:`spawn_detached` 的 CREATE_NO_WINDOW 藏，
    不再用 ``-WindowStyle Hidden``（11-28 真机：小进程起来后什么都没做）。"""
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]


# CREATE_NO_WINDOW：给小进程一个看不见的控制台（PowerShell 要控制台；以前的 DETACHED_PROCESS 没有控制台）；
# CREATE_NEW_PROCESS_GROUP；CREATE_BREAKAWAY_FROM_JOB：本进程若在作业对象里，小进程脱出去，本进程退出时不被一起结束
# （11-28 真机：自升级与卸载的小进程在本进程退出后都没做事，同一段脚本手动跑能成）
_CREATE_NO_WINDOW, _NEW_GROUP, _BREAKAWAY = 0x08000000, 0x00000200, 0x01000000


def spawn_detached(command: list, cwd: Optional[Path] = None, *, popen=None) -> None:
    """起一个不随本进程退出的小进程（Windows：CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB；
    作业不许脱出时去掉最后一项再起一次）。工作目录放在安装目录外（``cwd``，默认 ``%LOCALAPPDATA%\\Pearnly``）：本进程是在
    版本目录里起的，小进程继承它就删不掉那个目录。"""
    popen = popen or subprocess.Popen
    if cwd is None:
        base = install_base()
        cwd = base.parent if base is not None else None
    options = dict(close_fds=True, cwd=str(cwd) if cwd is not None else None,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.name != "nt":
        popen(command, **options)  # noqa: S603
        return
    try:
        popen(command, creationflags=_CREATE_NO_WINDOW | _NEW_GROUP | _BREAKAWAY, **options)  # noqa: S603
    except OSError:   # 作业对象不许脱出（拒绝访问）：照常起
        log.info("小进程不能脱出作业，照常起")
        popen(command, creationflags=_CREATE_NO_WINDOW | _NEW_GROUP, **options)  # noqa: S603


def upgrade(link, *, running: Optional[str] = None, base: Optional[Path] = None, exe=None,
            spawn: Callable[[list], None] = spawn_detached, pid: Optional[int] = None) -> str:
    """看隧道盘 ``cc\\`` 里有没有比自己新的云端包，有就备好新版本、起小进程、请本进程退出（:attr:`CloudLink.quit`）。回结果码。"""
    if running is None:
        from ..version import VERSION as running
    base = install_base() if base is None else base
    current = installed_dir(base, exe)
    if current is None:
        return NOT_INSTALLED
    manifest = placed_manifest(link.root)
    # 也比本进程所在的版本目录名：包里程序自报的版本比清单旧时（发布出错），新进程不会对着同一份包一直「升级」重启
    have = max(version_key(running), version_key(current.name))
    if manifest is None or version_key(manifest["version"]) <= have:
        return NONE
    if manifest["version"] in link.upgrade_failed:
        return NONE
    try:
        new_exe = stage(link.root, base, manifest)
        spawn(helper_command(upgrade_script(os.getpid() if pid is None else pid, base, new_exe)))
    except ValueError as error:
        code = str(error) if str(error) in (CHECKSUM, BAD_PACKAGE) else FAILED
        link.upgrade_failed.add(manifest["version"])
        log.warning("云端自升级失败 · 版本=%s 原因=%s", manifest["version"], code)
        return code
    except Exception as exc:  # noqa: BLE001 - OSError、zipfile 的 NotImplementedError / RuntimeError 等：记下这一版、不再反复试
        link.upgrade_failed.add(manifest["version"])
        log.warning("云端自升级失败 · 版本=%s 原因=%s", manifest["version"], type(exc).__name__)
        return FAILED
    ensure_marker(base, exe)
    log.info("云端自升级 · 从=%s 到=%s 结果=%s", running, manifest["version"], STAGED)
    link.quit.set()
    link.bump()
    return STAGED


# ── 断开连带删 ────────────────────────────────────────────────────────────────

def _link_points_into(base: Path, read_target: Optional[Callable[[], str]]) -> Optional[bool]:
    """启动文件夹 CC.lnk 是否指向我们目录（只为回执；读不了回 None）。"""
    if read_target is None:
        return None
    try:
        target = str(read_target() or "")
    except Exception:  # noqa: BLE001 - 只为回执
        return None
    return bool(target) and _norm(target).startswith(_norm(base) + os.sep)


def uninstall(*, base: Optional[Path] = None, exe=None, spawn: Callable[[list], None] = spawn_detached,
              pid: Optional[int] = None, read_link_target: Optional[Callable[[], str]] = None) -> dict:
    """校验后起删的小进程；回 ``{code, dir, link}``（``dir`` / ``link`` 是删前回读：在不在 / 指不指向我们目录）。"""
    base = install_base() if base is None else base
    before = {"dir": bool(base is not None and Path(base).is_dir()), "link": _link_points_into(base, read_link_target)
              if base is not None else None}
    if not ours(base, exe):
        log.info("云端卸载 · 结果=%s 目录在=%s", NOT_OURS, before["dir"])
        return {"code": NOT_OURS, **before}
    try:
        spawn(helper_command(uninstall_script(os.getpid() if pid is None else pid, base)))
    except OSError as exc:
        log.warning("云端卸载失败 · 原因=%s", type(exc).__name__)
        return {"code": FAILED, **before}
    log.info("云端卸载 · 结果=%s 目录在=%s 快捷方式指向本目录=%s", SCHEDULED, before["dir"], before["link"])
    return {"code": SCHEDULED, **before}


def handle_uninstall(link, *, run: Optional[Callable[[], dict]] = None) -> Optional[str]:
    r"""``ctl\uninstall.json`` 在就读（读后删除）、用当前会话解开、核编号没做过，校验后起删的小进程，写回执
    ``ctl\uninstalled.json``（同一编号、``{code, dir, link}``）；要删就请本进程退出。回结果码；没有文件回 None。"""
    from . import cloud_side
    from . import cred_handoff as handoff
    from . import ctl_files

    data = handoff.take(link.ctl_dir / ctl_files.UNINSTALL_FILE, 16 * 1024)
    if data is None:
        return None
    try:
        item_id, _payload = cloud_side.open_ctl(link, data, "uninstall")
    except ctl_files.CtlError as error:
        log.warning("云端卸载文件不对 · 原因=%s", error.code)
        return error.code
    if item_id in link.commands_seen:
        return None
    link.commands_seen.add(item_id)
    result = (run or uninstall)()
    try:
        handoff.write_atomic(link.ctl_dir / ctl_files.UNINSTALLED_FILE,
                             ctl_files.seal(link.session, "uninstalled", dict(result), item_id=item_id))
    except OSError as exc:
        log.warning("云端卸载回执写入失败 · 原因=%s", type(exc).__name__)
    if result.get("code") == SCHEDULED:
        link.quit.set()
        link.bump()
    return str(result.get("code") or "")


__all__ = ["BAD_PACKAGE", "CHECKSUM", "FAILED", "MARKER", "MARKER_TEXT", "NONE", "NOT_INSTALLED", "NOT_OURS", "SCHEDULED",
           "STAGED", "ensure_marker", "handle_uninstall", "helper_command", "install_base", "installed_dir", "ours",
           "placed_manifest", "spawn_detached", "stage", "uninstall", "uninstall_script", "upgrade", "upgrade_script",
           "version_key"]
