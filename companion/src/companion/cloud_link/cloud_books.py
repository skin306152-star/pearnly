# -*- coding: utf-8 -*-
r"""云端那份 CC 找账套、报状态、执行桥的命令（第二阶段评估 4.2，D4）。

- 找账套（:func:`detect`）：照本地「第一次连接」（``express_machine.detect_roots``，云端模式只找本机盘、不碰 ``\\tsclient``），
  另加直接探 ``<本机盘>:\<云端用户名>\`` 与其下一层（11-24 首测：受限账号列 ``D:\`` 只看得到「Help Express」，数据目录在
  ``D:\<用户名样的目录>\ExpressI-Cloud``；只列自己用户名那一层，不列盘根）。
- 状态（:func:`status_payload` / :func:`write_status`）：云端 CC 版本、成员结果码、各数据根与账套数、最近一次查找的结果码与时间、
  最近一次「添加文件夹」的结果。用当前会话加密写 ``ctl\status.json``（``ctl_files``），桥读后删；云端在换会话后、成员改后、
  命令做完后写，平时每 5 分钟看一次、内容变了才写。
- 命令（:func:`run_command`）：``rescan.json`` 重新查找（重找数据根；已有成员就把找到的写进成员，没有就按最近一次下发的站点再建）；
  ``add-place.json {path}`` 手填的数据目录：路径要在、不在 ``\\tsclient`` 下、下面找得到登记表才用。带编号，见过的不再做。

本模块不导入 Qt。所有探测、读写都可注入，Mac 上离线测。
"""
from __future__ import annotations

import ntpath
import os
import time
from typing import Callable, Iterable, List, Optional

from ..logger import get_logger

log = get_logger(__name__)

STATUS_EVERY = 300.0
# 查找结果码
FOUND = "found"
NOT_FOUND = "not_found"
# 添加文件夹结果码
OK = "ok"
NOT_ALLOWED = "not_allowed"
BAD_PATH = "bad_path"
PATH_MAX = 260


def _user() -> str:
    return str(os.environ.get("USERNAME") or "").strip()


def user_dirs(*, user: Optional[str] = None, drives: Optional[Callable[[], List[str]]] = None,
              listdir: Callable[[str], list] = os.listdir, is_dir: Callable[[str], bool] = os.path.isdir) -> List[str]:
    r"""``<本机盘>:\<用户名>`` 与它下一层的文件夹（候选数据根，交 ``rank_roots`` 核登记表）。不列盘根；列不了的跳过。"""
    from .. import express_locate

    name = _user() if user is None else str(user or "").strip()
    if not name or any(ch in name for ch in "\\/:"):
        return []
    roots = (drives or (lambda: [d for d in express_locate._mounted_drive_roots()
                                 if not express_locate._client_side(d)]))()
    found: List[str] = []
    for drive in roots:
        base = ntpath.join(drive, name)
        try:
            if not is_dir(base):
                continue
            found.append(base)
            for child in sorted(listdir(base))[:200]:
                path = ntpath.join(base, child)
                if is_dir(path):
                    found.append(path)
        except OSError:
            continue
    return found


def detect(store, *, extra: Callable[[], Iterable[str]] = user_dirs) -> List[str]:
    from .. import express_machine

    return express_machine.detect_roots(store, extra=list(extra() or []))


def places(store, *, probe: Optional[Callable] = None) -> list:
    """本机成员的数据根与各自登记的账套数（读登记表，不读每个账套）。"""
    from .. import express_machine

    if probe is None:
        from ..account_probe import probe_account_sets

        probe = lambda root: probe_account_sets(root, include_details=False)  # noqa: E731
    found = []
    for root in express_machine.machine_roots(store):
        try:
            count = len(probe(root))
        except Exception:  # noqa: BLE001 - 暂时读不了：照列，账套数 -1
            count = -1
        found.append({"path": root, "ledgers": count})
    return found


# ── 状态文件 ──────────────────────────────────────────────────────────────────

def status_payload(link, store, *, places_of=places, version: Optional[str] = None) -> dict:
    """要报给桥的状态（不含时间戳以外的易变项，便于比较「内容变了才写」）。"""
    if version is None:
        from ..version import VERSION as version
    return {"version": str(version), "members": link.members_code or "", "places": places_of(store),
            "scan": getattr(link, "scan_code", "") or "", "scanned_at": int(getattr(link, "scanned_at", 0) or 0),
            "add": dict(getattr(link, "add_result", {}) or {})}


def write_status(link, *, load: Optional[Callable] = None, force: bool = False, payload_of=status_payload) -> bool:
    r"""用当前会话加密写 ``ctl\status.json``；内容和上次写的一样且不 ``force`` 就不写。回写了没有。"""
    from . import cred_handoff as handoff
    from . import ctl_files

    if load is None:
        from ..profile_store import ProfileStore

        load = ProfileStore.load
    payload = payload_of(link, load())
    key = (link.session.epoch, repr(sorted(payload.items(), key=lambda kv: kv[0])))
    if not force and key == getattr(link, "status_key", None):
        return False
    handoff.write_atomic(link.ctl_dir / ctl_files.STATUS_FILE, ctl_files.seal(link.session, "status", payload))
    link.status_key = key
    log.info("云端状态已报 · 成员=%s 数据根=%d 查找=%s", payload["members"] or "-", len(payload["places"]),
             payload["scan"] or "-")
    return True


# ── 命令 ─────────────────────────────────────────────────────────────────────

def _client_side(path: str) -> bool:
    from .. import express_locate

    return express_locate._client_side(path)


def check_place(path, *, exists: Callable[[str], bool] = os.path.isdir) -> str:
    """手填的数据目录能不能用：回 ``ok`` / ``bad_path``（空、太长、不是绝对路径、不在）/ ``not_allowed``（在 ``\\tsclient`` 下）。"""
    text = ntpath.normpath(str(path or "").strip()) if str(path or "").strip() else ""
    if not text or len(text) > PATH_MAX or not ntpath.isabs(text) or any(ch < " " for ch in text):
        return BAD_PATH
    if _client_side(text):
        return NOT_ALLOWED
    try:
        return OK if exists(text) else BAD_PATH
    except OSError:
        return BAD_PATH


def _apply(store_load, roots: List[str], link, *, sync=None) -> str:
    """把找到的数据根用上：已有云端成员就写进成员（``apply_roots``）；没有就按最近一次下发的站点再建（``cloud_members.sync``）。"""
    from .. import express_machine

    if sync is None:
        from .cloud_members import sync
    store = store_load()
    if express_machine.express_members(store):
        express_machine.apply_roots(store, roots)
        return link.members_code or "ready"
    code, changed = sync(link.last_members or [], detect=lambda _store: list(roots))
    link.members_code = code
    if changed:
        link.bump()
    return code


def rescan(link, *, load: Optional[Callable] = None, find=detect, sync=None, clock=time.time) -> str:
    """「重新查找」：重找数据根并用上。回查找结果码。"""
    if load is None:
        from ..profile_store import ProfileStore

        load = ProfileStore.load
    roots = list(find(load()) or [])
    link.scan_code, link.scanned_at = (FOUND if roots else NOT_FOUND), int(clock())
    if roots:
        _apply(load, roots, link, sync=sync)
    log.info("云端重新查找 · 结果=%s 数据根=%d", link.scan_code, len(roots))
    return link.scan_code


def add_place(link, path, item_id: str, *, load: Optional[Callable] = None, find_under=None, sync=None,
              check=check_place) -> str:
    """手填路径：核过、在它下面找数据根，找到就和现有的一起用上。结果记进 ``link.add_result``（带编号，桥据此对上）。"""
    from .. import express_machine

    if load is None:
        from ..profile_store import ProfileStore

        load = ProfileStore.load
    if find_under is None:
        find_under = lambda place: express_machine.places_roots([place])  # noqa: E731
    code = check(path)
    roots: List[str] = []
    if code == OK:
        roots = list(find_under(ntpath.normpath(str(path).strip())) or [])
        code = OK if roots else NOT_FOUND
    if roots:
        current = express_machine.machine_roots(load())
        merged = [*roots, *(root for root in current
                            if not any(express_machine.same_root(root, known) for known in roots))]
        _apply(load, merged, link, sync=sync)
    link.add_result = {"id": item_id, "code": code}
    log.info("云端添加数据目录 · 结果=%s 数据根=%d", code, len(roots))
    return code
