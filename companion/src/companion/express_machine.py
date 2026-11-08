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
import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Iterable, List, Optional

from .account_probe import AccountSet, probe_account_sets, probe_default_accounts
from .express_locate import _deep_scan, _scan_exp_roots, candidate_roots, sccomp_company_count, sccomp_path
from .logger import get_logger
from .pairing import ACCOUNT_MAP_KEYS

log = get_logger(__name__)

_DEFAULT_MIN_COMPANIES = 10


# 路径判断在 express_places（位置清单，用户 10-24），这里照旧能引用
from .express_places import norm_root, same_root, under, within, place_of, merge  # noqa: E402,F401


def year_key(root: str) -> int:
    years = re.findall(r"\d{2}", Path(root).name)
    return max((int(year) for year in years), default=-1)


def root_identity(root: str):
    """同一份登记表（经映射盘和共享路径各见一次）只算一个；文件号（st_ino，拿不到是 0）不同的就是另一份——整个年度文件夹
    复制出来的大小、修改时间都一样（10-24 验收 72EXP）。"""
    try:
        stat = sccomp_path(root).stat()
        return "sig", stat.st_size, int(stat.st_mtime), int(getattr(stat, "st_ino", 0) or 0)
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


def detect_roots(store, extra: Iterable[str] = ()) -> List[str]:
    """还没有成员时自动找 Express：已配过的根（``extra``：断开连接前这台电脑授权过的）、注册表、快捷方式、常见盘位，
    默认年度排第一。"""
    ranked = rank_roots([*machine_roots(store), *extra, *profile_root_candidates(store), *candidate_roots()])
    if not ranked:
        return []
    first = pick_default(ranked)
    return [first, *(root for root, _count in ranked if not same_root(root, first))]


# 手动选上层文件夹 / 盘符时往下找的上限：人在等（界面显示「正在查找…」），大的共享盘给足时间与目录数
# （测试机 S 盘 60 秒只列完 1300 多个文件夹，自动查找那 8 秒不够）。
PICK_SECONDS = 60.0
PICK_MAX_DIRS = 20000


def root_gone(root: str, asked: Optional[dict] = None) -> bool:
    """这个数据根确实没了：它所在的盘 / 共享此刻访问得到，文件夹却不在。服务器关着、网络盘没连上、盘符不在（拔掉的移动盘）
    都不算没了（只是暂时访问不到）；文件夹在、只是这次没读到登记表的也不算。所在服务器先限时问一次（scan_pace.GATE，
    卡住不等），不在就不再碰它；``asked``（同一次查找共用）记着问过的服务器，同一台只问一次（夜里 16 个年度在同一台上）。"""
    from .scan_pace import GATE

    text = str(root or "").strip()
    if not text:
        return False
    # 同一次查找里同一个根只判一次：定时重扫按成员逐个判，服务器在两个成员之间掉线时不会一个去掉、一个保留（10-26 用例）
    key = ("root", text.replace("/", "\\").rstrip("\\").lower())
    if asked is not None and key in asked:
        return asked[key]
    gone = _root_gone(text, asked, GATE)
    if asked is not None:
        asked[key] = gone
    return gone


def _root_gone(text: str, asked: Optional[dict], GATE) -> bool:
    host = GATE.host(text)
    if asked is None or host not in asked:
        reachable = GATE.reachable(text, retry=True)
        if asked is not None:
            asked[host] = reachable
    else:
        reachable = asked[host]
    if not reachable:
        return False
    anchor = ntpath.splitdrive(text.replace("/", "\\"))[0]
    try:
        if anchor and not os.path.isdir(anchor + "\\"):
            return False
        return not os.path.isdir(text)
    except OSError:
        return False


def gone_test(asked: Optional[dict] = None, *, wait: bool = True):
    """一次查找用的「确实没了」判断（:func:`root_gone`，``asked`` 在这一次里共用：每台服务器只问一次）。

    ``wait=False``：心跳里用（在取任务线程上，不能卡）：所在服务器在退避期内、这会儿问不通的不去等，按暂时访问不到算（保留）；
    问得通的（本机盘、刚问过还新鲜的服务器）照 :func:`root_gone` 判。"""
    from .scan_pace import GATE

    asked = {} if asked is None else asked
    quick: dict = {}

    def gone(root) -> bool:
        if not wait:
            host = GATE.host(str(root or ""))
            if host not in quick:
                quick[host] = GATE.reachable(str(root or ""))
            if not quick[host]:
                return False
        return root_gone(root, asked)

    return gone


def live_roots(roots: Iterable[str], asked: Optional[dict] = None, *, wait: bool = True, gone=None) -> List[str]:
    """给成员定数据根（写 ``authorized_roots`` / ``express_root``）之前一律过这一道：确实没了的去掉（所在盘 / 共享访问得到、
    文件夹却不在，:func:`root_gone`），暂时访问不到的保留，顺序不变（主根在前）。一个都不剩时原样交回：不把成员弄成没有数据根。
    配对 / 换配对码（pairing_flow.run）、断开后重连（desktop_settings._kept 经 detect 再到配对）、心跳里的定时重扫
    （worker._drop_gone_roots）都经这里（测试机 10-25：71EXP、72EXP 早删了，每次配对仍带着它们）。"""
    roots = [str(root) for root in roots if str(root or "").strip()]
    if gone is None:
        gone = gone_test(asked, wait=wait)
    left = [root for root in roots if not gone(root)]
    if not left:
        return roots
    if len(left) < len(roots):
        log.info("去掉已经不在的 Express 数据根：%s", [root for root in roots if root not in left])
    return left


def prune_member(record, asked: Optional[dict] = None, *, wait: bool = True, gone=None) -> dict:
    """一个成员（成员记录或它的配置）里确实没了的数据根去掉 → 要改的字段；没有要改的回 ``{}``。

    主数据根没了：按「主根在前」换成剩下的第一个（:func:`member_roots` 的顺序），程序路径跟着换（同 :func:`apply_roots`）；
    一个都不剩时什么都不改（:func:`live_roots`）。账套、科目不在这里换。"""
    roots = member_roots(record)
    if len(roots) < 2:
        return {}
    left = live_roots(roots, asked, wait=wait, gone=gone)
    if len(left) == len(roots):
        return {}
    authorized = [str(root) for root in (getattr(record, "authorized_roots", None) or ())
                  if any(same_root(root, kept) for kept in left)] or list(left)
    updates = {"authorized_roots": authorized}
    if not any(same_root(roots[0], kept) for kept in left):
        updates["express_root"] = left[0]
        updates["express_exe"] = str(Path(left[0]) / "ExpressI.exe")
    return updates


def offline_roots(found: Iterable[str], previous: Iterable[str], *, gone=None) -> List[str]:
    """以前授权、这次没找到、又不是确实没了的数据根（暂时访问不到：夜里关着的服务器、没连上的网络盘）：保留授权，
    下次在线时心跳照常扫到它（扫描按服务器限时问、不在就跳过，见 scan_pace）。一次查找失败不删用户的授权（2026-10-20
    测试机：夜里 S 盘不在线时点「重新查找」，17 个授权只剩本机那 1 个）。"""
    if gone is None:
        gone = gone_test()
    found = list(found)
    kept: List[str] = []
    for root in previous:
        if any(same_root(root, known) for known in (*found, *kept)):
            continue
        if not gone(root):
            kept.append(root)
    return kept


def places_roots(places: Iterable[str], *, seconds: float = PICK_SECONDS) -> List[str]:
    """位置下面的全部年度文件夹（express_places，用户 10-24）：认登记表、任何名字、3 层内、限时（同「添加」时往下找）；
    位置本身就是年度文件夹的就是它自己。所在服务器这会儿不在的位置跳过（不等）。默认年度排第一。"""
    from .scan_pace import GATE

    places = merge(places)
    found: List[str] = []
    deep: List[str] = []
    for place in places:
        if not GATE.reachable(place):
            continue
        if sccomp_company_count(place) > 0:
            found.append(place)
        else:
            deep.append(place)
    if deep:
        found += _scan_exp_roots(deep)
        found += _deep_scan(deep, total_seconds=seconds, seconds=seconds, max_dirs=PICK_MAX_DIRS)
    ranked = rank_roots(root for root in found if within(root, places))
    if not ranked:
        return []
    first = pick_default(ranked)
    return [first, *(root for root, _count in ranked if not same_root(root, first))]


def default_places(store) -> List[str]:
    """第一次装、还没添加过位置：自动找一个 Express（全机 + 局域网，同原来第一次连接），取它所在的位置作默认（用户可移除、改）。"""
    roots = detect_roots(store)
    return [place_of(roots[0])] if roots else []


def rescan_roots(store, places: Iterable[str], previous: Optional[Iterable[str]] = None, *, gone=None):
    """「重新查找」：在位置下面再找一遍 → ``(要授权的数据根, 其中暂时访问不到、照旧保留的)``（不再整台电脑、局域网去搜）。

    ``previous``：这台电脑原来授权的（默认是成员现在的）。找到的全部授权；原来授权、还在位置里、这次没找到的，暂时访问不到
    就保留（:func:`offline_roots`），确实没了才去掉；位置外的不留。原来的主数据根仍排第一（找得到，或只是暂时访问不到）。"""
    places = merge(places)
    previous = machine_roots(store) if previous is None else list(previous)
    previous = [root for root in previous if within(root, places)]
    found = places_roots(places)
    kept = offline_roots(found, previous, gone=gone)
    roots = [*found, *kept]
    current = previous[:1]
    if current and any(same_root(current[0], root) for root in roots):
        first = next(root for root in roots if same_root(current[0], root))
        roots = [first, *(root for root in roots if root is not first)]
    return roots, kept


def default_account(sets: List[AccountSet], keep: str = "") -> Optional[AccountSet]:
    """成员的内部默认账套（不显示，账套由网页第一步选）：原来那个还在就沿用，否则取第一个够得到的。"""
    if keep:
        for item in sets:
            if same_root(item.path, keep):
                return item
    return next((item for item in sets if str(item.path or "").strip() and Path(str(item.path)).exists()), None)


def apply_roots(store, roots: List[str], offline: Iterable[str] = ()) -> int:
    """把这台电脑的数据根写进每个成员（暂停的也写）；返回改了几个成员。

    成员原来的默认账套仍登记在新数据根里（按登记表里的账套名单认，账套可以在别的盘）→ 账套、行号、人工改过的
    科目原样保留，主数据根取真正登记它的那一个（推送按「数据根 + 账套」找账套，两者必须对得上）；
    不在了**也不换**（11-06：自动换成过 S 盘一本真实客户的账）：所选原样留着、主根取新位置第一个，等人在网页里选；
    只有成员还没有所选账套时才取主根里第一个够得到的账套，科目取它 ISINFO 的默认值。全部算好再写，有一个成员找不到可用账套
    就一个都不改。服务端在下一次心跳拿到新的账套与数据根，不用重新配对。

    ``offline``：其中暂时访问不到、只是保留授权的数据根（:func:`rescan_roots`）：不去读它们的登记表；成员的主数据根就是
    其中之一时它的账套、科目原样不动（核不了，不当作「不在了」换掉）。
    """
    if not roots:
        raise ValueError("no Express data root")
    offline = [root for root in offline or ()]
    reachable = [root for root in roots if not any(same_root(root, away) for away in offline)]
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
        # 登记它的年度不止一个（整个复制出来的年度文件夹，登记表写绝对路径）：原来的主根在里面就留原来的（10-26 测试机：
        # 新加的位置里有复制品，排在前面就成了主根）
        homes = [root for root in reachable
                 if account and any(same_root(item.path, account) for item in registered(root))]
        home = next((root for root in homes if same_root(root, cfg.express_root)), homes[0] if homes else None)
        away = next((root for root in offline if same_root(root, cfg.express_root)), None)
        if home is not None:
            updates = {"express_root": home}
        elif away is not None:
            home, updates = away, {"express_root": away}
        elif account:
            # 所选账套不在新位置里（位置被去掉、账套挪走）：**不换成别的账套**，原来的所选、行号、科目原样留着，推送都带账套与数据根、按授权
            # 位置核对（worker._task_settings），不会因此写进别的账套；人在网页里选账套（11-06 测试机：去掉 70EXP 后两个成员被自动换成 S 盘一本真实客户的账）。
            # 主根取新位置的第一个（心跳照常扫新位置、报新名单）。
            if not reachable:
                raise ValueError(roots[0])
            home = reachable[0]
            updates = {"express_root": home}
            log.warning("所选账套不在新的 Express 位置里，保留原来的所选、不自动换账套，等人在网页里选 · profile=%s",
                        record.profile_id)
        else:
            if not reachable:
                raise ValueError(roots[0])
            if not fallback:
                fallback.append(default_account(probe_account_sets(reachable[0])))
            chosen = fallback[0]
            if chosen is None:
                raise ValueError(reachable[0])
            home = reachable[0]
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
