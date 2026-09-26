# -*- coding: utf-8 -*-
"""PACK 前账套快照(只读复制选定账套整目录 · PACK 中途异常时的还原网)。

PACK 动整套 FoxPro 文件重整,中途断电/崩溃可能留下不一致文件。本模块在触发 PACK 前
对选定账套整目录做时间戳快照,并管理磁盘守卫 + 保留策略:
  · make_backup —— copytree 选定账套 → backup_root/<账套名>_<时间戳>(只读复制,绝不改源)。
  · has_enough_disk —— 备份前查可用空间 ≥ 账套体积×余量,不足则不备份(调用方据此跳过 PACK)。
  · prune —— 成功 PACK 后保留最近 K 份非保护快照,清更早的;protect 集合(PACK 失败快照)绝不删。
纯文件操作 · 不 import 任何 PACK/写盘逻辑(与 T9/T4 的 PACK 区解耦)。
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Iterable, List

DISK_MARGIN = 1.2  # 需 ≥ 账套体积 × 1.2 的可用空间才备份(留余量)


def dir_size(path: str | Path) -> int:
    """目录总字节(只读统计 · 坏档/无权限的文件跳过)。"""
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def has_enough_disk(dest_root: str | Path, needed_bytes: int, margin: float = DISK_MARGIN) -> bool:
    """dest_root 所在盘可用空间是否够放一份账套(× 余量)。查不到保守返 False。"""
    try:
        Path(dest_root).mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(str(dest_root)).free
    except OSError:
        return False
    return free >= int(needed_bytes * margin)


def make_backup(account_dir: str | Path, backup_root: str | Path) -> Path:
    """copytree 选定账套整目录 → backup_root/<账套名>_<时间戳>。返回快照路径(失败抛异常)。"""
    account_dir = Path(account_dir)
    backup_root = Path(backup_root)
    backup_root.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    dest = backup_root / f"{account_dir.name}_{ts}"
    n = 0
    while dest.exists():
        n += 1
        dest = backup_root / f"{account_dir.name}_{ts}_{n}"
    shutil.copytree(account_dir, dest)
    return dest


def restore(backup_dir: str | Path, account_dir: str | Path) -> int:
    """把快照整目录覆盖回账套(PACK 中途坏了的还原网)。逐文件覆盖,返回还原文件数。

    PACK 被打断(崩溃/断电/用户停小助手杀掉子进程)会留残索引;还原 pre-PACK 快照即回到
    一致状态(快照晚于写票 → 推进去的单据仍在)。绝不删账套里快照没有的文件(只覆盖)。
    """
    backup_dir = Path(backup_dir)
    account_dir = Path(account_dir)
    n = 0
    for root, _dirs, files in os.walk(backup_dir):
        rel = Path(root).relative_to(backup_dir)
        dest_root = account_dir / rel
        dest_root.mkdir(parents=True, exist_ok=True)
        for f in files:
            try:
                shutil.copy2(Path(root) / f, dest_root / f)
                n += 1
            except OSError:
                pass
    return n


def truncated_indexes(
    account_dir: str | Path, backup_dir: str | Path, ratio: float = 0.5
) -> List[str]:
    """对比快照,账套里明显被截断的 .CDX 列表(< 快照同名 × ratio)= PACK 中途坏的索引。

    PACK 正常重整 CDX 体积只小幅变化;截断(如 25088→6656=26%)远低于阈值,据此判损坏。
    返回受损索引文件名列表(空=完好)。
    """
    account_dir = Path(account_dir)
    backup_dir = Path(backup_dir)
    bad: List[str] = []
    try:
        for p in backup_dir.iterdir():
            if not p.is_file() or p.suffix.lower() != ".cdx":
                continue
            live = account_dir / p.name
            try:
                if live.exists() and live.stat().st_size < p.stat().st_size * ratio:
                    bad.append(p.name)
            except OSError:
                pass
    except OSError:
        pass
    return bad


def list_backups(backup_root: str | Path, account_name: str) -> List[Path]:
    """该账套的快照,按 mtime 旧→新排序。"""
    root = Path(backup_root)
    if not root.exists():
        return []
    snaps = [p for p in root.iterdir() if p.is_dir() and p.name.startswith(account_name + "_")]
    return sorted(snaps, key=lambda p: p.stat().st_mtime)


def prune(
    backup_root: str | Path, account_name: str, keep: int, protect: Iterable[Path] = ()
) -> List[Path]:
    """保留最近 keep 份(非保护)快照,删更早的非保护快照。protect 集合绝不删(PACK 失败快照)。

    返回已删快照列表。
    """
    protected = {Path(p).resolve() for p in protect}
    backups = list_backups(backup_root, account_name)
    deletable = [b for b in backups if b.resolve() not in protected]
    excess = len(deletable) - max(0, int(keep))
    removed: List[Path] = []
    for b in deletable[: max(0, excess)]:  # 删最旧的若干份
        try:
            shutil.rmtree(b)
            removed.append(b)
        except OSError:
            pass
    return removed
