# -*- coding: utf-8 -*-
"""自动定位 Express 数据根目录(含 `secure/SCCOMP.DBF` 公司登记表 · 驱动登录 grid)。

配对窗不再逼客户敲网络路径。按兜底链自动找:① 环境变量 PEARNLY_EXPRESS_ROOT ② 注册表
Express 安装位置 ③ 桌面/开始菜单快捷方式 ④ 有界扫已挂载盘符与账套服务器共享。
判据 = 该根下 `secure/SCCOMP.DBF` 含 ≥1 家登记公司;多个候选命中时选**登记公司最多**的
那套(活跃数据根;旧/空模板让位)。全找不到才让 GUI 浏览兜底。纯只读。

★为什么不写死文件夹名(原 bug):Express 每个会计年度一套数据根,文件夹按泰历年命名且各事务所
写法不一——`70EXP`/`68EXP`/`EXP68`,还可能压在年份子目录 `2568\\EXP68` 下,盘符也各异
(本地 C:/D: · 映射盘 P:/S: · UNC 共享)。写死 `70EXP` 只认得一家一年的约定,换事务所/跨年
(69/71EXP…)即失灵。**唯一不变量 = `secure\\SCCOMP.DBF`**(Express 自己建登录 grid 读的同一
张表)→ 改为在候选父目录下按 `*EXP*` 通配(Windows glob 不分大小写)深度≤2 扫这张表,任意年度/
文件夹名/盘符自动命中。年度歧义(同机多套年度根)由「选登记最多」+ GUI 常驻换根 + 配置粘住托底。
"""

from __future__ import annotations

import ctypes
import glob
import json
import logging
import ntpath
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import List, Mapping, Optional, Sequence, Tuple

# Express 年度数据根文件夹惯例:68EXP / EXP68 / 70exp …(Windows glob 大小写不敏感)。
_EXP_GLOB = "*EXP*"
# Windows glob 本身不分大小写;第二个模式让非 Windows 的测试/调试行为一致。
_EXP_GLOBS = (_EXP_GLOB, "*exp*")
# 直连账套服务器共享(未映射盘符时仍可命中);映射成 P:/S: 的场景由挂载盘扫描覆盖。
_SHARE_PARENTS = (r"\\accserver\ACCOUNT",)
_SHORTCUT_TIMEOUT_SECONDS = 3.0
# 深层扫描（2026-10-08，换事务所 / 局域网布局不漏扫）：不认文件夹名，只认 `secure\SCCOMP.DBF`；
# 在各个起点下最多往下 3 层，遇到账套文件夹（直接放着 .DBF）与系统目录不再往下钻；
# 每个起点单独限时、限目录数，起点并行扫，整体也限时 —— 够不到的网络盘只会让那一个起点超时，不拖住别的。
_DEEP_DEPTH = 3
_DEEP_SECONDS_PER_PARENT = 8.0
_DEEP_MAX_DIRS_PER_PARENT = 5000
_DEEP_TOTAL_SECONDS = 20.0
_SKIP_DIR_NAMES = frozenset({
    "windows", "program files", "program files (x86)", "programdata", "$recycle.bin",
    "system volume information", "recovery", "perflogs", "msocache", "appdata", "node_modules",
    "$windows.~bt", "$windows.~ws", "windowsapps", "secure",
})

log = logging.getLogger("pearnly_companion.express_locate")


def _safe_path_label(path: str | Path) -> str:
    """日志只留最后一段,不把用户目录、UNC 服务器或快捷方式参数写进技术日志。"""
    clean = str(path or "").strip().rstrip("\\/")
    return ntpath.basename(clean) or "<root>"


def sccomp_path(root: str | Path) -> Path:
    """该数据根的公司登记表路径:`<root>/secure/SCCOMP.DBF`。"""
    return Path(root) / "secure" / "SCCOMP.DBF"


def sccomp_company_count(root: str | Path) -> int:
    """root 的 SCCOMP 里非删除公司数;缺/坏/读不到返 0(不抛 · 仅供打分选根)。"""
    p = sccomp_path(root)
    try:
        if not p.exists():
            log.debug(
                "Express candidate rejected: SCCOMP missing (%s)",
                _safe_path_label(root),
            )
            return 0
        import dbf

        t = dbf.Table(str(p), codepage="cp874", unicode_errors="replace")
        t.open(dbf.READ_ONLY)
        try:
            return sum(1 for r in t if not dbf.is_deleted(r))
        finally:
            t.close()
    except Exception as exc:
        log.debug(
            "Express candidate rejected: SCCOMP unreadable (%s, %s)",
            _safe_path_label(root),
            type(exc).__name__,
        )
        return 0


def _windows_shortcut_dirs(environ: Optional[Mapping[str, str]] = None) -> List[str]:
    """Windows 用户/公用桌面与开始菜单目录(去重保序)。"""
    env = environ if environ is not None else os.environ
    roots: List[str] = []

    def add(base_key: str, *parts: str) -> None:
        base = (env.get(base_key) or "").strip()
        if base:
            roots.append(ntpath.join(base, *parts))

    add("USERPROFILE", "Desktop")
    add("PUBLIC", "Desktop")
    add("APPDATA", "Microsoft", "Windows", "Start Menu", "Programs")
    add("PROGRAMDATA", "Microsoft", "Windows", "Start Menu", "Programs")
    for key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        add(key, "Desktop")

    seen, ordered = set(), []
    for root in roots:
        key = ntpath.normcase(ntpath.normpath(root))
        if key not in seen:
            seen.add(key)
            ordered.append(root)
    return ordered


def _express_shortcuts(search_dirs: Sequence[str]) -> List[str]:
    """只遍历已知桌面/开始菜单目录,绝不从盘符根递归找快捷方式。"""
    links: List[str] = []

    def walk_error(exc: OSError) -> None:
        log.debug("Express shortcut walk failed (%s)", type(exc).__name__)

    for root in search_dirs:
        try:
            if not os.path.isdir(root):
                continue
            for current, _dirs, files in os.walk(
                root, followlinks=False, onerror=walk_error
            ):
                links.extend(
                    os.path.join(current, name)
                    for name in files
                    if name.casefold().endswith(".lnk") and "express" in name.casefold()
                )
        except OSError as exc:
            log.debug(
                "Express shortcut directory unavailable (%s, %s)",
                _safe_path_label(root),
                type(exc).__name__,
            )
    return links


def _powershell_executable() -> Optional[str]:
    for name in ("powershell.exe", "pwsh.exe"):
        found = shutil.which(name)
        if found:
            return found
    system_root = (os.environ.get("SystemRoot") or "").strip()
    if system_root:
        fallback = ntpath.join(
            system_root,
            "System32",
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe",
        )
        if os.path.isfile(fallback):
            return fallback
    return None


_RESOLVE_SHORTCUTS_PS = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$shell = New-Object -ComObject WScript.Shell
$decoded = $env:PEARNLY_EXPRESS_SHORTCUTS_JSON | ConvertFrom-Json
$paths = @($decoded)
$items = @()
for ($i = 0; $i -lt $paths.Count; $i++) {
    try {
        $shortcut = $shell.CreateShortcut([string]($paths[$i]))
        $items += [PSCustomObject]@{
            index = $i
            target = [string]$shortcut.TargetPath
            working_dir = [string]$shortcut.WorkingDirectory
        }
    } catch {
        $items += [PSCustomObject]@{ index = $i; error = 'unreadable' }
    }
}
ConvertTo-Json -Compress -InputObject @($items)
""".strip()


def _resolve_shortcuts(
    shortcuts: Sequence[str], timeout: float = _SHORTCUT_TIMEOUT_SECONDS
) -> List[Tuple[str, str]]:
    """在一个有超时的 Windows COM 子进程内解析 .lnk 目标与工作目录。"""
    if not shortcuts:
        return []
    powershell = _powershell_executable()
    if not powershell:
        log.debug("Express shortcut resolver unavailable (PowerShell missing)")
        return []
    child_env = os.environ.copy()
    child_env["PEARNLY_EXPRESS_SHORTCUTS_JSON"] = json.dumps(
        list(shortcuts), ensure_ascii=False
    )
    try:
        completed = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                _RESOLVE_SHORTCUTS_PS,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
            env=child_env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        log.warning("Express shortcut inspection timed out after %.1fs", timeout)
        return []
    except OSError as exc:
        log.debug("Express shortcut resolver failed (%s)", type(exc).__name__)
        return []
    if completed.returncode != 0:
        log.debug("Express shortcut resolver exited with code %s", completed.returncode)
        return []
    try:
        decoded = json.loads(completed.stdout.lstrip("\ufeff") or "[]")
    except (TypeError, json.JSONDecodeError):
        log.debug("Express shortcut resolver returned invalid output")
        return []
    if not isinstance(decoded, list):
        decoded = [decoded]
    resolved: List[Tuple[str, str]] = []
    for item in decoded:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index", -1))
            if index < 0 or index >= len(shortcuts):
                continue
            shortcut = shortcuts[index]
        except (IndexError, TypeError, ValueError):
            continue
        if item.get("error"):
            log.debug("Express shortcut unreadable (%s)", _safe_path_label(shortcut))
            continue
        target = str(item.get("target") or "").strip().strip('"')
        working_dir = str(item.get("working_dir") or "").strip().strip('"')
        if not target and not working_dir:
            log.debug("Express shortcut has no target (%s)", _safe_path_label(shortcut))
            continue
        resolved.append((target, working_dir))
    return resolved


def _shortcut_candidate_roots() -> List[str]:
    """从 Express 快捷方式得到直接数据根,并仅对其上一级做有界扫描。"""
    if os.name != "nt":
        return []
    shortcuts = _express_shortcuts(_windows_shortcut_dirs())
    direct: List[str] = []
    scan_parents: List[str] = []
    for target, working_dir in _resolve_shortcuts(shortcuts):
        roots: List[str] = []
        if working_dir:
            roots.append(ntpath.normpath(os.path.expandvars(working_dir)))
        if target:
            target = ntpath.normpath(os.path.expandvars(target))
            suffix = ntpath.splitext(ntpath.basename(target))[1]
            roots.append(ntpath.dirname(target) if suffix else target)
        for root in roots:
            if not root:
                continue
            direct.append(root)
            parent = ntpath.dirname(root)
            if parent and parent != root:
                scan_parents.append(parent)

    def dedupe(paths: Sequence[str]) -> List[str]:
        seen, ordered = set(), []
        for path in paths:
            key = ntpath.normcase(ntpath.normpath(path))
            if key not in seen:
                seen.add(key)
                ordered.append(path)
        return ordered

    direct = dedupe(direct)
    direct.extend(_scan_exp_roots(dedupe(scan_parents)))
    return dedupe(direct)


def _registry_install_dirs() -> List[str]:
    """从注册表读 Express 安装位置(原样目录 · 供既当根又当扫描父)。非 Windows/读不到返空。"""
    out: List[str] = []
    try:
        import winreg
    except ImportError:
        return out
    # ExpressSoft 装机键:HKLM\SOFTWARE\[WOW6432Node\]Express Accounting\<x>\InstallLocation
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Express Accounting"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Express Accounting"),
    ]
    for hive, base in keys:
        try:
            k = winreg.OpenKey(hive, base)
        except OSError:
            continue
        try:
            for i in range(winreg.QueryInfoKey(k)[0]):
                try:
                    sub = winreg.OpenKey(k, winreg.EnumKey(k, i))
                    loc = str(winreg.QueryValueEx(sub, "InstallLocation")[0]).strip()
                    if loc:
                        out.append(loc)
                except OSError:
                    continue
        finally:
            k.Close()
    return out


def _mounted_drive_roots() -> List[str]:
    """当前已挂载盘符根(如 C:\\、P:\\)。只列存在的位掩码盘 → 不去 probe 断开的映射盘(防卡死)。"""
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return [r"C:\\", r"D:\\"]  # 非 Windows/取不到 → 最小兜底
    return [f"{chr(65 + i)}:\\" for i in range(26) if bitmask & (1 << i)]


def _is_data_root(path: str) -> bool:
    try:
        return os.path.isfile(os.path.join(path, "secure", "SCCOMP.DBF"))
    except OSError:
        return False


def _walk_data_roots(parent: str, *, depth: int = _DEEP_DEPTH, seconds: float = _DEEP_SECONDS_PER_PARENT,
                     max_dirs: int = _DEEP_MAX_DIRS_PER_PARENT) -> List[str]:
    """从 parent 往下最多 depth 层找数据根（任何文件夹名）。找到的数据根、账套文件夹、系统目录都不再往下。"""
    found: List[str] = []
    deadline = time.monotonic() + seconds
    queue = deque([(parent, 0)])
    listed = 0
    while queue and listed < max_dirs and time.monotonic() < deadline:
        current, level = queue.popleft()
        try:
            with os.scandir(current) as entries:
                children = list(entries)
        except OSError:
            continue
        listed += 1
        if level and any(entry.name.casefold().endswith(".dbf") and _plain_file(entry) for entry in children):
            continue  # 账套文件夹（一堆 .DBF）：里面不会再套一个数据根
        for entry in children:
            if not _plain_dir(entry) or entry.name.casefold() in _SKIP_DIR_NAMES or entry.name.startswith((".", "~")):
                continue
            if _is_data_root(entry.path):
                found.append(entry.path)
            elif level + 1 < depth:
                queue.append((entry.path, level + 1))
    return found


def _plain_dir(entry) -> bool:
    """真目录（不跟符号链接、不进 NTFS 联接点，免得绕圈）。"""
    try:
        if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
            return False
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return False


def _plain_file(entry) -> bool:
    try:
        return entry.is_file(follow_symlinks=False)
    except OSError:
        return False


def _deep_scan(parents: Sequence[str], total_seconds: float = _DEEP_TOTAL_SECONDS, **limits) -> List[str]:
    """各起点并行做有界深层扫描，整体限时；超时的起点结果丢掉。用后台守护线程：卡在够不到的网络盘上的线程
    不会拖住小助手退出（线程池在解释器退出时会等它）。"""
    starts, seen = [], set()
    for parent in parents:
        key = ntpath.normcase(ntpath.normpath(str(parent or "")))
        if parent and key not in seen:
            seen.add(key)
            starts.append(str(parent))
    results: dict = {}
    from . import scan_pace

    low = scan_pace.is_low_priority()  # 后台扫账套清单时找新年度文件夹：查找线程也降一档（界面上人等着找的不降）

    def run(index: int, start: str) -> None:
        if low:
            scan_pace.low_priority()
        try:
            results[index] = _walk_data_roots(start, **limits)
        except Exception as exc:  # noqa: BLE001 - 某个起点读不了就跳过
            log.debug("Express deep scan failed (%s, %s)", _safe_path_label(start), type(exc).__name__)

    workers = [threading.Thread(target=run, args=(index, start), name="pearnly-express-scan", daemon=True)
               for index, start in enumerate(starts)]
    deadline = time.monotonic() + total_seconds
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(max(0.0, deadline - time.monotonic()))
    found: List[str] = []
    for index in range(len(starts)):  # 按起点顺序，结果稳定
        found.extend(results.get(index, []))
    return found


def _network_share_roots() -> List[str]:
    """这台电脑登记过的网络共享：映射过的网络盘的真实地址（断开的也在）与「映射网络驱动器」用过的地址。"""
    out: List[str] = []
    try:
        import winreg
    except ImportError:
        return out

    def values(path: str):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                index = 0
                while True:
                    try:
                        name, value, _kind = winreg.EnumValue(key, index)
                    except OSError:
                        return
                    index += 1
                    yield name, value
        except OSError:
            return

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Network") as network:
            index = 0
            while True:
                try:
                    letter = winreg.EnumKey(network, index)
                except OSError:
                    break
                index += 1
                for name, value in values("Network\\" + letter):
                    if name == "RemotePath":
                        out.append(str(value))
    except OSError:
        pass
    for name, value in values(r"Software\Microsoft\Windows\CurrentVersion\Explorer\Map Network Drive MRU"):
        if name != "MRUList":
            out.append(str(value))
    seen, ordered = set(), []
    for path in out:
        clean = path.strip().rstrip("\\/")
        if clean.startswith("\\\\") and clean.casefold() not in seen:
            seen.add(clean.casefold())
            ordered.append(clean)
    return ordered


def _user_folders(environ: Optional[Mapping[str, str]] = None) -> List[str]:
    """小办公室常把账套放在个人文件夹（桌面、文档）下，那里比盘符根深一层，单独作为起点。"""
    env = environ if environ is not None else os.environ
    home = (env.get("USERPROFILE") or "").strip()
    if not home:
        return []
    return [home, ntpath.join(home, "Desktop"), ntpath.join(home, "Documents")]


def _scan_exp_roots(parents: List[str]) -> List[str]:
    """在候选父目录下按 `*EXP*/secure/SCCOMP.DBF` 找数据根,兼顾年份子目录(深度≤2)。

    覆盖 `<盘>\\68EXP`、`<盘>\\EXP68`、`<盘>\\2568\\EXP68` 等任意年度/命名;每步 guard,
    断开的盘/无权限静默跳过。返回去掉 `secure\\SCCOMP.DBF` 尾部后的根目录列表。
    """
    found: List[str] = []
    for parent in parents:
        try:
            if not os.path.isdir(parent):
                continue
        except OSError:
            continue
        patterns = tuple(
            pattern
            for exp_glob in _EXP_GLOBS
            for pattern in (
                os.path.join(parent, exp_glob, "secure", "SCCOMP.DBF"),
                os.path.join(parent, "*", exp_glob, "secure", "SCCOMP.DBF"),
            )
        )
        for pat in patterns:
            try:
                for hit in glob.glob(pat):
                    found.append(str(Path(hit).parent.parent))
            except OSError:
                continue
    return found


def candidate_roots() -> List[str]:
    """按优先级返回候选根:环境变量 → 注册表 → Express 快捷方式 → 有界扫描。"""
    cands: List[str] = []
    env = (os.environ.get("PEARNLY_EXPRESS_ROOT") or "").strip()
    if env:
        cands.append(env)
    install_dirs = _registry_install_dirs()
    cands.extend(install_dirs)  # 安装目录本身也可能直接是数据根
    cands.extend(_shortcut_candidate_roots())
    shares = _network_share_roots()
    parents = _mounted_drive_roots() + list(_SHARE_PARENTS) + shares
    for loc in install_dirs:  # 安装目录及其父目录当扫描父(年度根常与 exe 同级/邻级)
        parents.append(loc)
        parents.append(str(Path(loc).parent))
    cands.extend(_scan_exp_roots(parents))
    # 再按「只认登记表、不认文件夹名」往深处找一遍：文件夹不叫 *EXP*、放得更深、在没映射成盘符的共享上的，
    # 都在这里补上（原来那一遍照旧在前，结果顺序不变）。
    cands.extend(_deep_scan([*parents, *_user_folders()]))
    seen, ordered = set(), []
    for c in cands:
        key = c.lower().rstrip("\\/")
        if key and key not in seen:
            seen.add(key)
            ordered.append(c)
    return ordered


def detect_express_root(candidates: Optional[List[str]] = None) -> Optional[str]:
    """返回候选里 SCCOMP 登记公司**最多**的数据根(活跃那套);全无 SCCOMP 返 None。

    同公司数按优先序保留先出现的(env > 注册表 > 常见)。
    """
    best, best_n = None, 0
    for c in candidates if candidates is not None else candidate_roots():
        n = sccomp_company_count(c)
        if n > best_n:  # 严格大于 → 同分保优先序
            best, best_n = c, n
    return best
