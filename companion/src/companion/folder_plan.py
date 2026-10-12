# -*- coding: utf-8 -*-
"""文件夹自动录入的纯规则（不联网、不碰 Qt、不写账套）：什么时候该跑、哪些文件算新的、结果表格放哪。

口径与云端 `apps/pearnly_cowork/pearnly_cowork/folder_intake/contracts.py` 一致：
- 每月几号（1–31，当月没有这一天就在当月最后一天；0 = 月末）几点，按曼谷时间（UTC+7，泰国不用夏令时）；
- 到点时电脑关着 / 小助手没开，下次启动补跑一次（错过几次也只补最近那一次）；
- 新任务（云端下发 ``existing`` 为 1，2026-10 起新建或改过文件夹的）处理文件夹里所有本任务还没处理过的文件，
  包括建任务前就在的（「处理过」只看本机台账：路径 + 大小 + 修改时间）；老任务只收规则启用之后放进文件夹的文件
  （启用时按文件在本机的创建时间拍底，先用云端时间校正本机时钟）；
- 只收 PDF / 图片；结果文件夹（`RESULT_FOLDERS`）里是系统生成的结果表格，不扫描；
- 文件 2 分钟内没再变动才算写完（扫描仪、复制还没完成的先不传）；
- 文件夹现有几份（`survey`，给网页卡片与任务列表）与 `scan` 同一判据，只列目录、限时限量，数不完就标「不全」。
"""

from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import ntpath
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

BANGKOK = dt.timezone(dt.timedelta(hours=7))
RESULT_FOLDER = "_Pearnly结果"
# 结果文件夹按建规则那个人的网页语言起名（与云端 contracts.RESULT_FOLDERS 同一份约定）；
# 云端随表格告诉用哪一个，扫描时这几个名字一律跳过。旧规则与旧云端用 RESULT_FOLDER。
RESULT_FOLDERS = (RESULT_FOLDER, "_ผลลัพธ์ Pearnly", "_Pearnly Results", "_Pearnly結果")
_RESULT_NAMES = frozenset(name.casefold() for name in RESULT_FOLDERS)
EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".heic", ".heif")
STABLE_SECONDS = 120
# 按时开跑后这么久之内算「按时」，再晚就是「开机补跑」。
ON_TIME_SECONDS = 2 * 3600
MAX_FOLDERS = 2000
FOLDER_DEPTH = 3
# 文件夹计数（survey）：一次上报所有根目录合计最多看这么久（秒）、这么多目录项，用完就停、标「不全」（网页显示 N+）。
SURVEY_SECONDS = 5.0
SURVEY_ENTRIES = 200_000


def root_id(path: str) -> str:
    """本机根目录的编号（只报编号与显示名给云端，不报本机绝对路径以外的东西）。"""
    return hashlib.sha256(os.path.normcase(os.path.normpath(path)).encode("utf-8")).hexdigest()[:16]


def _slot(year: int, month: int, day: int, at: str) -> dt.datetime:
    last = calendar.monthrange(year, month)[1]
    hour, minute = (int(part) for part in at.split(":"))
    return dt.datetime(year, month, last if day == 0 else min(day, last), hour, minute, tzinfo=BANGKOK)


def latest_slot(now: float, day: int, at: str) -> float:
    """不晚于 ``now`` 的最近一次按时开跑时刻（epoch 秒）。"""
    local = dt.datetime.fromtimestamp(now, BANGKOK)
    current = _slot(local.year, local.month, day, at)
    if current.timestamp() <= now:
        return current.timestamp()
    year, month = (local.year, local.month - 1) if local.month > 1 else (local.year - 1, 12)
    return _slot(year, month, day, at).timestamp()


@dataclass(frozen=True)
class Due:
    trigger: str  # schedule / manual / catch_up
    slot: Optional[float] = None
    request: Optional[int] = None


def due(rule: dict, state: dict, now: float) -> Optional[Due]:
    """这条规则现在该不该跑：立即运行的请求优先，其次是到点（或错过后补跑一次）。

    ``state``：本机记下的 ``last_slot``（处理过的最近一次按时时刻）与 ``last_request``（处理过的立即运行请求号）。
    规则启用之前的时刻不算（``active_since``）。"""
    request = int(rule.get("run_request") or 0)
    if request > int(state.get("last_request") or 0):
        return Due("manual", request=request)
    slot = latest_slot(now, int(rule.get("run_day") or 0), str(rule.get("run_time") or "22:00"))
    floor = max(float(state.get("last_slot") or 0), float(rule.get("active_since") or 0))
    if slot > floor:
        return Due("schedule" if now - slot <= ON_TIME_SECONDS else "catch_up", slot=slot)
    return None


def is_result_folder(name: str) -> bool:
    return name.casefold() in _RESULT_NAMES


def result_folder(value) -> str:
    """云端给的结果文件夹名；不是约定里的名字（旧云端没给）就用 RESULT_FOLDER。"""
    name = str(value or "")
    return name if name.casefold() in _RESULT_NAMES else RESULT_FOLDER


def share_path(path: str, mapped: Optional[dict] = None) -> str:
    """授权文件夹若在网络共享上,换成网络路径(\\\\服务器\\共享\\…);本机盘返回空串。

    云端拿它跨电脑比对规则文件夹是否重叠(同一个共享在两台电脑上各建规则会把同一份文件推两次)。映射盘按当前用户的
    持久映射(注册表 HKCU\\Network\\<盘符>\\RemotePath)换算;非持久映射认不出,当本机盘。"""
    text = ntpath.normpath(str(path or "").strip())  # 按 Windows 路径规整(在别的系统上跑测试也一样)
    if text.startswith("\\\\"):
        return text
    drive = text[:2].upper()
    if len(drive) == 2 and drive[1] == ":":
        remote = (mapped if mapped is not None else _mapped_drives()).get(drive[0])
        if remote:
            return ntpath.normpath(remote.rstrip("\\") + text[2:])
    return ""


def _mapped_drives() -> dict:
    try:
        import winreg  # Windows 才有
    except ImportError:
        return {}
    found = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Network") as network:
            index = 0
            while True:
                try:
                    letter = winreg.EnumKey(network, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(network, letter) as key:
                        value, _kind = winreg.QueryValueEx(key, "RemotePath")
                except OSError:
                    continue
                if isinstance(value, str) and value.startswith("\\\\"):
                    found[letter.upper()[:1]] = value
    except OSError:
        return {}
    return found


def rule_folder(root: str, folder: str) -> Path:
    parts = [part for part in re.split(r"[\\/]+", folder or "") if part]
    if any(part in (".", "..") for part in parts):
        raise ValueError("invalid rule folder")
    return Path(root).joinpath(*parts)


def _skipped_name(name: str) -> bool:
    return name.startswith((".", "~$")) or name.lower().endswith((".tmp", ".part", ".crdownload"))


def created_at(stat: os.stat_result) -> float:
    """文件在本机的创建时间：Windows 是 st_ctime；macOS 有 st_birthtime；其它系统退回修改时间。"""
    if os.name == "nt":
        return stat.st_ctime
    return getattr(stat, "st_birthtime", None) or stat.st_mtime


@dataclass(frozen=True)
class Candidate:
    relpath: str  # 相对规则文件夹，`\\` 分隔
    path: Path
    size: int
    mtime: float
    created: float


def scan(folder: Path, errors: Optional[list] = None) -> Iterator[Candidate]:
    """规则文件夹（含下级子文件夹）里所有 PDF / 图片，跳过结果文件夹、隐藏与临时文件。

    ``errors``：给了就把读不了的文件夹 / 文件记进去（调用方据此判断这次扫描全不全，例如网络共享抖了一下）。"""
    if not folder.is_dir():
        return
    for current, dirs, files in os.walk(folder, onerror=None if errors is None else errors.append):
        dirs[:] = sorted(d for d in dirs if not is_result_folder(d) and not _skipped_name(d))
        for name in sorted(files):
            if _skipped_name(name) or not name.lower().endswith(EXTENSIONS):
                continue
            path = Path(current) / name
            try:
                stat = path.stat()
            except OSError as error:
                if errors is not None:
                    errors.append(error)
                continue
            if stat.st_size <= 0:
                continue
            relative = path.relative_to(folder).as_posix().replace("/", "\\")
            yield Candidate(relative, path, stat.st_size, stat.st_mtime, created_at(stat))


def is_new(candidate: Candidate, active_since: float, clock_offset: float) -> bool:
    """规则启用之后放进来的（本机时钟按云端校正；留 2 分钟余量，免得启用当下拷进来的被漏掉）。只给老任务拍底用。"""
    return candidate.created + clock_offset >= float(active_since or 0) - 120


def is_stable(candidate: Candidate, now: float) -> bool:
    return now - candidate.mtime >= STABLE_SECONDS


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def folders_of(root: Path) -> list[str]:
    """根目录下的文件夹名（给网页下拉选；只报名字、限层数与条数，不报文件）。"""
    found: list[str] = []
    if not root.is_dir():
        return found
    for current, dirs, _files in os.walk(root):
        depth = len(Path(current).relative_to(root).parts)
        dirs[:] = sorted(d for d in dirs if not is_result_folder(d) and not _skipped_name(d))
        if depth >= FOLDER_DEPTH:
            dirs[:] = []
        for name in dirs:
            found.append((Path(current) / name).relative_to(root).as_posix().replace("/", "\\"))
            if len(found) >= MAX_FOLDERS:
                return found
    return found


@dataclass(frozen=True)
class Seen:
    relpath: str  # 相对根目录（不是规则文件夹），`\\` 分隔
    size: int
    mtime: float


def survey(root: Path, *, deadline: float, budget: list, clock: Callable[[], float] = time.monotonic
           ) -> tuple[list[Seen], bool]:
    """根目录（含全部下级）里会被录入的文件，判据与 :func:`scan` 相同（结果文件夹、隐藏与临时文件、非 PDF / 图片、
    0 字节都不算；不进符号链接的文件夹）。只列目录、用列目录时带回的大小与修改时间（Windows 上不再逐个文件问一次），
    不打开文件。

    ``deadline``（``clock`` 的读数）到了、或 ``budget``（``[剩余目录项数]``，一次上报的几个根目录共用一份）用完就停，
    返回 ``(已看到的, False)``；看全了返回 ``(…, True)``。读不了的文件夹跳过（同 scan，那里的文件也不会被处理）。"""
    found: list[Seen] = []
    if not root.is_dir():
        return found, True
    stack = [(str(root), "")]
    while stack:
        current, prefix = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    budget[0] -= 1
                    if budget[0] < 0 or clock() > deadline:
                        return found, False
                    name = entry.name
                    if _skipped_name(name):
                        continue
                    relative = f"{prefix}\\{name}" if prefix else name
                    try:
                        if entry.is_dir():
                            if not is_result_folder(name) and not entry.is_symlink():
                                stack.append((entry.path, relative))
                            continue
                        if not name.lower().endswith(EXTENSIONS):
                            continue
                        stat = entry.stat()
                    except OSError:
                        continue
                    if stat.st_size > 0:
                        found.append(Seen(relative, stat.st_size, stat.st_mtime))
        except OSError:
            continue
    return found, True


def folder_counts(seen: Iterable[Seen], folders: Iterable[str]) -> dict[str, int]:
    """上报的每个文件夹（``""`` 是根目录本身）里含下级子文件夹会被录入的文件数，键用上报时的写法；
    没有文件的不列（云端：根目录行带了 ``counts`` 就是数过，不在里面的文件夹是 0）。"""
    wanted: dict[str, str] = {}
    for name in folders:
        wanted.setdefault(str(name).casefold(), str(name))
    counts: dict[str, int] = {}
    for item in seen:
        parts = item.relpath.casefold().split("\\")[:-1]
        for depth in range(min(len(parts), FOLDER_DEPTH) + 1):
            key = wanted.get("\\".join(parts[:depth]))
            if key is not None:
                counts[key] = counts.get(key, 0) + 1
    return counts


def pending_count(seen: Iterable[Seen], folder: str, known: Callable[[str, int, float], bool],
                  baseline: set) -> int:
    """一条规则还没处理的文件数：在规则文件夹（含下级）里、不在老任务的拍底里、本机台账也不认得的。

    ``known(相对规则文件夹的路径, 大小, 修改时间)`` 是台账的判断；``baseline`` 是拍底（相对规则文件夹、casefold）。"""
    prefix = [part.casefold() for part in re.split(r"[\\/]+", folder or "") if part]
    count = 0
    for item in seen:
        parts = item.relpath.split("\\")
        if len(parts) <= len(prefix) or [part.casefold() for part in parts[:len(prefix)]] != prefix:
            continue
        relpath = "\\".join(parts[len(prefix):])
        if relpath.casefold() in baseline or known(relpath, item.size, item.mtime):
            continue
        count += 1
    return count


def result_path(folder: Path, month: str, filename: str, taken: set[str], result: str = RESULT_FOLDER) -> Path:
    """结果表格放 ``<规则文件夹>\\<结果文件夹>\\YYYY-MM\\``；同名的（同号不同卖方以外的重名）加 (2)、(3)。"""
    target = folder / result_folder(result) / month
    stem, suffix = os.path.splitext(filename)
    name, index = filename, 2
    while name.casefold() in taken:
        name, index = f"{stem} ({index}){suffix}", index + 1
    return target / name


def month_of(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, BANGKOK).strftime("%Y-%m")
