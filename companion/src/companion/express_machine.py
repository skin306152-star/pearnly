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
from .express_locate import _deep_scan, _scan_exp_roots, candidate_roots, sccomp_company_count, sccomp_path
from .pairing import ACCOUNT_MAP_KEYS

_DEFAULT_MIN_COMPANIES = 10


def norm_root(path) -> str:
    """同一个文件夹的不同写法算一个：Qt 选择窗口给的是 C:/a\\b 这种正斜杠（2026-10-10 测试机合并后多出一条重复）。"""
    text = str(path or "").strip()
    return ntpath.normpath(text).lower().rstrip("\\/") if text else ""


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


def express_members(store) -> list:
    """有 Express 账套的成员（MR.ERP 的小助手成员只读文件夹，没有 Express 数据根，2026-10-09）。"""
    return [record for record in store.profiles if getattr(record, "kind", "express") != "folder"]


def profile_root_candidates(store, exclude_id: str = "") -> List[str]:
    """Reuse configured roots as candidates; rank_roots still validates SCCOMP."""
    roots = []
    for record in express_members(store):
        if record.profile_id == exclude_id:
            continue
        root = str(record.express_root or "").strip()
        if not root:
            root = ntpath.dirname(str(record.account_dir or "").rstrip("\\/"))
        if root:
            roots.append(root)
    return roots


def authorized_order(roots: Iterable[str]) -> List[str]:
    """授权年度的存放顺序：与旧版配对一致，新年度在前（同年度保持原顺序）。"""
    return sorted(roots, key=year_key, reverse=True)


def member_roots(record) -> List[str]:
    """一个成员自己的数据根：主根（没有就取账套的上一级）在前，再接它授权的其它年度。"""
    found: List[str] = []
    primary = str(getattr(record, "express_root", "") or "").strip() or ntpath.dirname(
        str(getattr(record, "account_dir", "") or "").rstrip("\\/"))
    for root in (primary, *(getattr(record, "authorized_roots", None) or ())):
        root = str(root or "").strip().rstrip("\\/")
        if root and not any(same_root(root, known) for known in found):
            found.append(root)
    return found


def machine_roots(store) -> List[str]:
    """这台电脑当前的 Express 数据根 = 全部成员数据根的并集（主根在前，按出现顺序）。"""
    found: List[str] = []
    for record in express_members(store):
        for root in member_roots(record):
            if not any(same_root(root, known) for known in found):
                found.append(root)
    return found


def detect_roots(store) -> List[str]:
    """还没有成员时自动找 Express：已配过的根、注册表、快捷方式、常见盘位，默认年度排第一。"""
    ranked = rank_roots([*machine_roots(store), *profile_root_candidates(store), *candidate_roots()])
    if not ranked:
        return []
    first = pick_default(ranked)
    return [first, *(root for root, _count in ranked if not same_root(root, first))]


# 手动选上层文件夹 / 盘符时往下找的上限：人在等（界面显示「正在查找…」），大的共享盘给足时间与目录数
# （测试机 S 盘 60 秒只列完 1300 多个文件夹，自动查找那 8 秒不够）。
PICK_SECONDS = 60.0
PICK_MAX_DIRS = 20000


def roots_for_pick(path: str) -> List[str]:
    """「更换…」选的文件夹 → 要授权的数据根。

    选的就是某个年度的数据根：它放第一，同一位置下的其它年度一并授权（网页上再选年度 / 账套）。
    选的是上层文件夹或盘符（如映射的共享盘）：往下找（同自动查找：认登记表不认名字、最多 3 层、限时），
    找到的全部授权、默认年度放第一（2026-10-10 用户在测试机选 S 盘被拒）。都没有返回空。"""
    if sccomp_company_count(path) > 0:
        parent = str(Path(path).parent)
        others = [root for root, _count in rank_roots(_scan_exp_roots([parent])) if not same_root(root, path)]
        return [path, *others]
    # 只做深层扫描（认登记表、任何名字、3 层内，已覆盖按 *EXP* 名字找的那一遍）：整体不超过 PICK_SECONDS。
    ranked = rank_roots(_deep_scan([path], total_seconds=PICK_SECONDS, seconds=PICK_SECONDS, max_dirs=PICK_MAX_DIRS))
    if not ranked:
        return []
    first = pick_default(ranked)
    return [first, *(root for root, _count in ranked if not same_root(root, first))]


def rescan_roots(store) -> List[str]:
    """「重新查找」：全机 + 局域网再找一遍（同第一次连接时）；现在用的主数据根还找得到就仍排第一。"""
    found = detect_roots(store)
    current = machine_roots(store)[:1]
    if current and any(same_root(current[0], root) for root in found):
        return [current[0], *(root for root in found if not same_root(root, current[0]))]
    return found


def default_account(sets: List[AccountSet], keep: str = "") -> Optional[AccountSet]:
    """成员的内部默认账套（不显示，账套由网页第一步选）：原来那个还在就沿用，否则取第一个够得到的。"""
    if keep:
        for item in sets:
            if same_root(item.path, keep):
                return item
    return next((item for item in sets if str(item.path or "").strip() and Path(str(item.path)).exists()), None)


def apply_roots(store, roots: List[str]) -> int:
    """把这台电脑的数据根写进每个成员（暂停的也写）；返回改了几个成员。

    成员原来的默认账套仍登记在新数据根里（按登记表里的账套名单认，账套可以在别的盘）→ 账套、行号、人工改过的
    科目原样保留，主数据根取真正登记它的那一个（推送按「数据根 + 账套」找账套，两者必须对得上）；
    不在了才换成主根里第一个够得到的账套，科目取它 ISINFO 的默认值。全部算好再写，有一个成员找不到可用账套
    就一个都不改。服务端在下一次心跳拿到新的账套与数据根，不用重新配对。
    """
    if not roots:
        raise ValueError("no Express data root")
    listed: dict = {}

    def registered(root: str) -> List[AccountSet]:
        if root not in listed:  # 只读登记表（不读每个账套的 ISINFO），每个根只读一次
            try:
                listed[root] = probe_account_sets(root, include_details=False)
            except Exception:
                listed[root] = []
        return listed[root]

    fallback: list = []
    targets = []
    for record in express_members(store):
        cfg = store.runtime_config(record.profile_id)
        account = str(cfg.account_dir or "").strip()
        home = next((root for root in roots
                     if account and any(same_root(item.path, account) for item in registered(root))), None)
        if home is not None:
            updates = {"express_root": home}
        else:
            if not fallback:
                fallback.append(default_account(probe_account_sets(roots[0])))
            chosen = fallback[0]
            if chosen is None:
                raise ValueError(roots[0])
            home = roots[0]
            defaults = probe_default_accounts(chosen.path) or {}
            updates = {
                "express_root": home,
                "account_dir": str(chosen.path),
                "account_set": str(chosen.path),
                "account_company": chosen.company,
                "account_set_row": int(getattr(chosen, "row", 0) or 0),
                **{key: str(defaults.get(key, "") or "") for key in ACCOUNT_MAP_KEYS},
            }
        updates["authorized_roots"] = authorized_order(roots)
        if not same_root(home, cfg.express_root):  # 主根没变就不动程序路径（与配对时的取法一致）
            updates["express_exe"] = str(Path(home) / "ExpressI.exe")
        target = replace(cfg)
        target.update(**updates)
        targets.append((target, record.paused))
    for target, paused in targets:
        store.save_profile(target, paused=paused)
    return len(targets)
