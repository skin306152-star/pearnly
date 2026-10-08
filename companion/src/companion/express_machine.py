# -*- coding: utf-8 -*-
"""这台电脑的 Express 账套（全局一份，2026-10-08）：检测数据根、换数据根时同步到全部成员。

主次关系（用户 2026-10-08）：小助手本身（公司 / 事务所的这台电脑）是主体，Express 账套是它的全局设置；
每个网页账号是挂在下面的成员，只管自己的密钥和网页可选的文件夹。两种装法都照旧：装在 Express 那台电脑上、
多个成员共用；或成员各装在自己电脑上、账套指向共享盘（这台电脑就只有这一个成员）。

不另存一份全局字段：成员记录里本来就有数据根（``express_root`` + ``authorized_roots``，写盘与授权检查都读它们），
全局值就是全部成员数据根的并集；换数据根时把新的一组写进每个成员。还没有成员时只在窗口里检测，不落盘。
本模块不碰界面，主窗口在后台线程里调用。
"""

from __future__ import annotations

import ntpath
import re
from dataclasses import replace
from pathlib import Path
from typing import Iterable, List, Optional

from .account_probe import AccountSet, probe_account_sets, probe_default_accounts
from .express_locate import _scan_exp_roots, candidate_roots, sccomp_company_count, sccomp_path
from .pairing import ACCOUNT_MAP_KEYS

_DEFAULT_MIN_COMPANIES = 10


def norm_root(path) -> str:
    return str(path or "").lower().rstrip("\\/")


def same_root(left, right) -> bool:
    return bool(left) and bool(right) and norm_root(left) == norm_root(right)


def year_key(root: str) -> int:
    years = re.findall(r"\d{2}", Path(root).name)
    return max((int(year) for year in years), default=-1)


def root_identity(root: str):
    try:
        stat = sccomp_path(root).stat()
        return "sig", stat.st_size, int(stat.st_mtime)
    except OSError:
        return "path", norm_root(root)


def rank_roots(candidates: Iterable[str]):
    """有效数据根（有登记表）去重后按年度新→旧、公司多→少排：[(根, 公司数)]。"""
    seen, roots = set(), []
    for candidate in candidates:
        if not norm_root(candidate):
            continue
        company_count = sccomp_company_count(candidate)
        if company_count <= 0:
            continue
        identity = root_identity(candidate)
        if identity in seen:
            continue
        seen.add(identity)
        roots.append((candidate, company_count))
    roots.sort(key=lambda item: (year_key(item[0]), item[1]), reverse=True)
    return roots


def pick_default(roots) -> str:
    """默认数据根：最新的、公司数够多的那一年（次年刚建、几乎空的不选）。"""
    for root, company_count in roots:
        if company_count >= _DEFAULT_MIN_COMPANIES:
            return root
    return roots[0][0]


def profile_root_candidates(store, exclude_id: str = "") -> List[str]:
    """Reuse configured roots as candidates; rank_roots still validates SCCOMP."""
    roots = []
    for record in store.profiles:
        if record.profile_id == exclude_id:
            continue
        root = str(record.express_root or "").strip()
        if not root:
            root = ntpath.dirname(str(record.account_dir or "").rstrip("\\/"))
        if root:
            roots.append(root)
    return roots


def machine_roots(store) -> List[str]:
    """这台电脑当前的 Express 数据根 = 全部成员数据根的并集（主根在前，按出现顺序）。"""
    found: List[str] = []
    for record in store.profiles:
        primary = str(record.express_root or "").strip() or ntpath.dirname(
            str(record.account_dir or "").rstrip("\\/"))
        for root in (primary, *(getattr(record, "authorized_roots", None) or ())):
            root = str(root or "").strip().rstrip("\\/")
            if root and not any(same_root(root, known) for known in found):
                found.append(root)
    return found


def detect_roots(store) -> List[str]:
    """还没有成员时自动找 Express：已配过的根、注册表、快捷方式、常见盘位，默认年度排第一。"""
    ranked = rank_roots([*machine_roots(store), *profile_root_candidates(store), *candidate_roots()])
    if not ranked:
        return []
    first = pick_default(ranked)
    return [first, *(root for root, _count in ranked if not same_root(root, first))]


def roots_for_pick(path: str) -> List[str]:
    """手动选的数据根放第一，同一位置下的其它年度一并授权（网页上再选年度 / 账套）；选的不是数据根返回空。"""
    if sccomp_company_count(path) <= 0:
        return []
    parent = str(Path(path).parent)
    others = [root for root, _count in rank_roots(_scan_exp_roots([parent])) if not same_root(root, path)]
    return [path, *others]


def default_account(sets: List[AccountSet], keep: str = "") -> Optional[AccountSet]:
    """成员的内部默认账套（不显示，账套由网页第一步选）：原来那个还在就沿用，否则取第一个够得到的。"""
    if keep:
        for item in sets:
            if same_root(item.path, keep):
                return item
    return next((item for item in sets if str(item.path or "").strip() and Path(str(item.path)).exists()), None)


def apply_roots(store, roots: List[str]) -> int:
    """把这台电脑的数据根写进每个成员（暂停的也写）；返回改了几个成员。

    成员原来的默认账套还在新数据根下就原样保留（连同人工改过的科目）；不在了就换成主根里第一个够得到的账套，
    科目取它 ISINFO 的默认值。服务端在下一次心跳拿到新的账套与数据根，不用重新配对。
    """
    if not roots:
        raise ValueError("no Express data root")
    changed, fallback, targets = 0, [], []  # 主根的默认账套只读一次（共享盘上读登记表较慢）
    for record in store.profiles:
        cfg = store.runtime_config(record.profile_id)
        inside = any(
            str(cfg.account_dir or "").lower().startswith(norm_root(root) + "\\")
            or str(cfg.account_dir or "").lower().startswith(norm_root(root) + "/")
            for root in roots
        )
        updates = {
            "express_root": roots[0],
            "authorized_roots": list(roots),
            "express_exe": str(Path(roots[0]) / "ExpressI.exe"),
        }
        if not inside:
            if not fallback:
                fallback.append(default_account(probe_account_sets(roots[0])))
            chosen = fallback[0]
            if chosen is None:
                raise ValueError(roots[0])
            defaults = probe_default_accounts(chosen.path) or {}
            updates.update(
                account_dir=str(chosen.path),
                account_set=str(chosen.path),
                account_company=chosen.company,
                account_set_row=int(getattr(chosen, "row", 0) or 0),
                **{key: str(defaults.get(key, "") or "") for key in ACCOUNT_MAP_KEYS},
            )
        target = replace(cfg)
        target.update(**updates)
        targets.append((target, record.paused))
    # 全部算好再写：有一个成员找不到可用账套就一个都不改，不会只改一半。
    for target, paused in targets:
        store.save_profile(target, paused=paused)
        changed += 1
    return changed
