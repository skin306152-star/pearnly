"""Shared, non-blocking refresh for Express account sets and chart accounts.

目录同步(用户 2026-09-27):网页打开「新建录入」用服务端存着的目录,不再每次现扫。
小助手这边:
- 平时每分钟在后台看一眼各年度文件夹的登记表 `secure/SCCOMP.DBF`(和当前账套的科目表)有没有变,
  变了才重扫、上报;另有每天一次的兜底全扫(科目表以外的改动也能跟上);
- 网页点「重新读取」(强制刷新)时整套重扫,配对时选了「全部授权」的还会重新找一遍盘上的年度文件夹
  (新建了 70EXP 这种);
- 扫描一律带公司名(读 ISINFO 有按修改时间的缓存,见 account_probe),不再有只读登记表、
  把公司名丢掉的快扫。
所有读盘都在后台线程里,不占拉取任务那条线。

增量扫描(2026-10-18,老电脑也不长时间占核;办公室测试机 1353 份账套原来每次启动重扫 5 分钟、21.8 秒 CPU):
- 每份账套读到的公司名 / 税号 / 默认科目 / 会计期间、各登记表的行、科目表连同文件的(大小, 修改时间)存在本机
  (account_index);重启后的第一遍只 stat,没变的不开 DBF,变了的才重读。启动时心跳先报上次的清单与科目表。
- 扫描线程主动让出 CPU(scan_pace.Pace,平时最多 15% 个核;不改 Windows 线程优先级,见 scan_pace 文件头);读完所选账套所在的数据根就先交一份「进行中」快照
  (``partial``,心跳把这些数据根换成新的、其余沿用上次报的),扫得慢时之后每隔一会儿再交一份,全部读完交完整快照。
- 局域网服务器断开 / 很慢:每台服务器每轮先限时问一次(scan_pace.HostGate),不在就整台跳过、按 1–15 分钟退避,
  不反复卡在超时上,也不因为它反复整套重扫。
- 找新年度文件夹(整盘、共享盘深层查找)不再每次启动都做:距上次超过 24 小时才做(时间记在本机),「重新读取」照旧马上找。
"""

from __future__ import annotations

import ntpath
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .logger import get_logger

log = get_logger(__name__)

_WATCH_INTERVAL_S = 60.0  # 多久看一次登记表有没有变
_FULL_RESCAN_S = 24 * 3600.0  # 兜底全扫
_DISCOVER_INTERVAL_S = 24 * 3600.0  # 多久找一次新的年度文件夹(重启不重找;网页「重新读取」马上找)
# 扫得慢时交「进行中」快照:开始读账套这么久后第一次(读完所选账套所在的数据根就交),之后每隔这么久一次。
# 扫得快(几秒内读完)就只交完整快照,与原来一样。
_PARTIAL_AFTER_S = 3.0
_PARTIAL_EVERY_S = 10.0


@dataclass
class _Refresh:
    running: bool = False
    payload: Optional[Dict[str, Any]] = None
    completed_at: float = 0.0
    error: str = ""
    checked_at: float = 0.0
    signature: tuple = ()
    generation: int = 0  # 上一次整套重扫时的 _rescan_generation


_lock = threading.Lock()
_refreshes: dict[str, _Refresh] = {}
# rescan_soon() 每调一次加一：比它旧的扫描结果下一次都整套重扫（账套目录换了文件号时，见 worker 写前核对）。
_rescan_generation = 0


def rescan_soon() -> None:
    """下一次心跳整套重扫一遍账套（不等登记表变化或每天一次），之前扫的结果不再交出去：写前核文件号对不上
    （ERR_ACCOUNT_UID_MISMATCH）时服务端要这台整份重报，报上去的文件号得是现在的；设置里「重新查找」之后同理（账套目录改名、
    挪走不改登记表，平时每分钟那一眼看不出来）。"""
    global _rescan_generation
    with _lock:
        _rescan_generation += 1


def _norm(path: str) -> str:
    value = str(path or "").strip().replace("/", "\\").rstrip("\\")
    return ntpath.normcase(ntpath.normpath(value)) if value else ""


def _roots(express_root: str, authorized_roots: Optional[list[str]]) -> list[str]:
    roots: list[str] = []
    seen: set[str] = set()
    for value in [express_root, *(authorized_roots or [])]:
        root = str(value or "").strip().rstrip("\\/")
        identity = _norm(root)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        roots.append(root)
    return roots


def _key(
    express_root: str,
    account_dir: str,
    account_sets_only: bool,
    authorized_roots: Optional[list[str]],
) -> str:
    mode = "sets" if account_sets_only else "full"
    roots = ";".join(_norm(root) for root in _roots(express_root, authorized_roots))
    return f"{roots}|{_norm(account_dir)}|{mode}"


def _sccomp_stamp(root: str) -> Optional[tuple]:
    """登记表的(大小, 修改时间);读不到 / 所在服务器这会儿不在(退避期内不做网络读)回 None。"""
    from .scan_pace import GATE

    if not GATE.reachable(root):
        return None
    try:
        from .express_locate import sccomp_path

        stat = os.stat(sccomp_path(root))
    except OSError:
        return None
    return (stat.st_size, stat.st_mtime_ns)


def _signature(roots: list[str], account_dir: str, with_chart: bool) -> tuple:
    """各年度文件夹登记表(+ 当前账套科目表)的大小与修改时间:没变就不必重扫。"""
    stamps = [(_norm(root), _sccomp_stamp(root)) for root in roots]
    if with_chart and account_dir:
        try:
            stat = os.stat(os.path.join(account_dir, "GLACC.DBF"))
            stamps.append((_norm(account_dir), (stat.st_size, stat.st_mtime_ns)))
        except OSError:
            stamps.append((_norm(account_dir), None))
    return tuple(stamps)


def _discover(roots: list[str]) -> list[str]:
    """重新找盘上的年度文件夹(新建的 70EXP 等);同一份登记表经映射盘和共享路径各见一次的只算一次。"""
    from .express_locate import candidate_roots, sccomp_company_count

    known = {_norm(root) for root in roots}
    stamps = {_sccomp_stamp(root) for root in roots} - {None}
    found = []
    for candidate in candidate_roots():
        root = str(candidate or "").strip().rstrip("\\/")
        stamp = _sccomp_stamp(root) if root else None
        if not root or _norm(root) in known or stamp is None or stamp in stamps:
            continue
        if sccomp_company_count(root) <= 0:
            continue
        known.add(_norm(root))
        stamps.add(stamp)
        found.append(root)
    return found


def _scan_order(roots: list[str], account_dir: str) -> list[str]:
    """读的顺序:所选账套所在的数据根先读(通常就是第一个主根),其余按原顺序。上报顺序不变(见 _snapshot)。"""
    selected = _norm(account_dir)
    owner = next((root for root in roots if selected and selected.startswith(_norm(root) + "\\")), None)
    if owner is None:
        return list(roots)
    return [owner, *(root for root in roots if root is not owner)]


def _snapshot(roots: list[str], done: dict, chart: Optional[list], *, partial: bool) -> Dict[str, Any]:
    """按数据根原来的顺序拼账套清单(同一数据根下同一路径只报一次);``partial`` 时只含读完的数据根并注明是哪些。"""
    seen_accounts: set[tuple[str, str]] = set()
    account_sets: list[dict] = []
    scanned: list[str] = []
    for root in roots:
        normalized_root = _norm(root)
        if normalized_root not in done:
            continue
        scanned.append(root)
        for account in done[normalized_root]:
            identity = (normalized_root, _norm(account.get("path")))
            if not identity[1] or identity in seen_accounts:
                continue
            seen_accounts.add(identity)
            account_sets.append(account)
    payload: Dict[str, Any] = {"account_sets": account_sets}
    if partial:
        payload["partial"] = True
        payload["scanned_roots"] = scanned
    if chart is not None:
        payload["accounts"] = chart
    return payload


def _publish_partial(key: str, payload: Dict[str, Any]) -> None:
    with _lock:
        refresh = _refreshes.get(key)
        if refresh is None:
            return
        refresh.payload = payload
        refresh.completed_at = max(time.time(), refresh.completed_at + 1e-6)


def _scan(
    key: str,
    express_root: str,
    account_dir: str,
    account_sets_only: bool,
    authorized_roots: Optional[list[str]],
    discover: bool = False,
    previous: Optional[tuple] = None,
    force: bool = False,
) -> None:
    """后台线程:`previous` 给了(平时的变化检查)就先比登记表,没变就不扫、不出新快照。
    整套重扫时先扫、先把结果交出去,再记登记表的修改时间 —— 够不到的共享盘读修改时间会卡好几秒,
    不能让它拖住这次结果。`discover` 时距上次找新年度文件夹满 24 小时(或 `force`)才真去找。"""
    error = ""
    payload = None
    signature: Optional[tuple] = None
    roots: list[str] = []
    looked = False
    try:
        from . import device_facts, scan_pace
        from .account_index import INDEX
        from .account_probe import account_sets_payload, chart_accounts_payload

        locator = device_facts.Locator()  # 本机共享表、各盘符的映射这一遍只问一次
        roots = _roots(express_root, authorized_roots)
        if discover and (force or INDEX.discovery_due(time.time(), _DISCOVER_INTERVAL_S)):
            roots += _discover(roots)
            INDEX.mark_discovered(time.time())
            looked = True
        if previous is not None:
            signature = _signature(roots, account_dir, not account_sets_only)
            if signature == previous:
                with _lock:
                    refresh = _refreshes.get(key)
                    if refresh is not None:
                        refresh.running = False
                return
        pace = scan_pace.Pace(gate=scan_pace.GATE, duty=scan_pace.FORCED_DUTY if force else scan_pace.DUTY)
        # 科目表先读(所选账套一张表):进行中的快照就带上,网页「科目映射」不用等整套扫完
        chart = None
        if not account_sets_only:
            chart = chart_accounts_payload(account_dir) if account_dir else []
        done: dict = {}
        began = time.monotonic()
        published = None
        for root in _scan_order(roots, account_dir):
            if scan_pace.GATE.reachable(root, retry=force):
                rows = account_sets_payload(root, locator=locator, pace=pace, reuse_registry=True)
            else:
                rows = []  # 所在服务器这会儿不在(退避期内):不读,这一轮不报它
            done[_norm(root)] = rows
            now = time.monotonic()
            if len(done) < len(roots) and now - began >= _PARTIAL_AFTER_S and (
                published is None or now - published >= _PARTIAL_EVERY_S
            ):
                INDEX.save()  # 扫到一半关机 / 退出也不白读：已读到的下次启动直接用
                _publish_partial(key, _snapshot(roots, done, chart, partial=True))
                published = now
        payload = _snapshot(roots, done, None if account_sets_only else chart, partial=False)
        if looked:
            payload["roots"] = roots
        if pace.slept:
            log.info("[accounts] 账套清单读完：%d 份，让出 CPU %.1f 秒", len(payload["account_sets"]), pace.slept)
        try:
            INDEX.save()  # 先存本机索引再交完整快照（本机盘，几十毫秒）：交出去的与下次启动读到的是同一份
        except Exception:
            log.exception("account index save failed")
    except Exception:
        log.exception("Express master refresh failed: %s", account_dir)
        payload = None
        error = "ERR_MASTER_SCAN_FAILED"
    with _lock:
        refresh = _refreshes.get(key)
        if refresh is None:
            return
        refresh.payload = payload
        refresh.completed_at = max(time.time(), refresh.completed_at + 1e-6)
        refresh.error = error
        if payload is None:
            refresh.running = False
            return
    if signature is None:
        try:
            signature = _signature(roots, account_dir, not account_sets_only)
        except Exception:
            log.exception("Express registry stamp failed: %s", account_dir)
            signature = ()
    with _lock:
        refresh = _refreshes.get(key)
        if refresh is not None:
            refresh.signature = signature
            refresh.running = False


def request_master(
    express_root: str,
    account_dir: str,
    *,
    newer_than: float = 0.0,
    refresh_interval: float = _FULL_RESCAN_S,
    force: bool = False,
    account_sets_only: bool = False,
    authorized_roots: Optional[list[str]] = None,
    discover: bool = False,
) -> tuple[Optional[Dict[str, Any]], float]:
    """Return one newer snapshot while DBF scans run off the poll lane.

    平时:每 `_WATCH_INTERVAL_S` 秒在后台比一次登记表,变了才重扫;`refresh_interval` 是兜底全扫间隔。
    `force`(网页「重新读取」):立刻整套重扫;`discover` 同时重新找盘上的年度文件夹。
    `account_sets_only` 只表示不读科目表(科目表跟着所选账套另行刷新)。
    """
    if not _norm(express_root) and not _norm(account_dir):
        return None, 0.0
    identity = _key(express_root, account_dir, account_sets_only, authorized_roots)
    now = time.time()
    with _lock:
        refresh = _refreshes.setdefault(identity, _Refresh())
        # rescan_soon() 之后（设置里「重新查找」、写前核文件号对不上）之前扫的结果不再交出去：取任务线程重启后头一拍
        # 拿到的也是新扫的，不把上一遍里已经不在的账套再报一次（2026-10-20 测试机：目录改回原名后仍报旧行到重启 CC）
        stale = refresh.generation < _rescan_generation
        if refresh.completed_at > newer_than and not stale:
            return refresh.payload, refresh.completed_at
        if refresh.running:
            return None, 0.0
        if not force and not stale and refresh.completed_at and now - refresh.checked_at < _WATCH_INTERVAL_S:
            return None, 0.0
        full = force or stale or not refresh.completed_at or now - refresh.completed_at >= refresh_interval
        if stale:  # 新的一遍交出（含「进行中」快照）之前，上一代的结果谁都不再拿到
            refresh.payload, refresh.completed_at = None, 0.0
        refresh.generation = _rescan_generation
        refresh.running = True
        refresh.error = ""
        refresh.checked_at = now
        thread = threading.Thread(
            target=_scan,
            args=(
                identity,
                express_root,
                account_dir,
                account_sets_only,
                authorized_roots,
                # 找新年度文件夹：网页「重新读取」时马上找；整套重扫时距上次满 24 小时才找（记在本机，重启不重找，
                # 2026-10-18；原来每次启动都整盘找一遍）—— 新建的年度（如明年的 71EXP）不用等人去点（2026-10-08）。
                bool(discover and (force or full)),
                None if full else refresh.signature,
                force,
            ),
            name="pearnly-master-refresh",
            daemon=True,
        )
        thread.start()
    return None, 0.0


def refresh_error(
    express_root: str,
    account_dir: str,
    *,
    newer_than: float,
    account_sets_only: bool = False,
    authorized_roots: Optional[list[str]] = None,
) -> str:
    identity = _key(express_root, account_dir, account_sets_only, authorized_roots)
    with _lock:
        refresh = _refreshes.get(identity)
        if refresh and refresh.completed_at > newer_than and refresh.payload is None:
            return refresh.error or "ERR_MASTER_SCAN_FAILED"
    return ""


def _reset_for_tests() -> None:
    global _rescan_generation
    with _lock:
        _refreshes.clear()
        _rescan_generation = 0


__all__ = ["refresh_error", "request_master", "rescan_soon"]
