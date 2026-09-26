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
from pathlib import Path
from typing import List, Mapping, Optional, Sequence, Tuple

# Express 年度数据根文件夹惯例:68EXP / EXP68 / 70exp …(Windows glob 大小写不敏感)。
_EXP_GLOB = "*EXP*"
# Windows glob 本身不分大小写;第二个模式让非 Windows 的测试/调试行为一致。
_EXP_GLOBS = (_EXP_GLOB, "*exp*")
# 直连账套服务器共享(未映射盘符时仍可命中);映射成 P:/S: 的场景由挂载盘扫描覆盖。
_SHARE_PARENTS = (r"\\accserver\ACCOUNT",)
_SHORTCUT_TIMEOUT_SECONDS = 3.0

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

        t = dbf.Table(str(p), codepage="cp874")
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
    parents = _mounted_drive_roots() + list(_SHARE_PARENTS)
    for loc in install_dirs:  # 安装目录及其父目录当扫描父(年度根常与 exe 同级/邻级)
        parents.append(loc)
        parents.append(str(Path(loc).parent))
    cands.extend(_scan_exp_roots(parents))
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
