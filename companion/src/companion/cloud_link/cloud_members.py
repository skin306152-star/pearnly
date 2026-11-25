# -*- coding: utf-8 -*-
r"""云端那份 CC 的成员（第二阶段评估 4.1，D3）：按桥下发的 ``ctl\members.json`` 在云端本机的 ``ProfileStore`` 里建 / 删成员。

- 一个站点一个成员，成员号 ``cloud-<站点>``；只管这类成员，别的一概不碰。桥那边没有的站点，云端删掉。
- 地址 ``https://<站点>``；通道按下发的 ``channel``。**令牌只放占位**：``<前缀><端点号>_cloud-relay``——前缀让 CC 按通道选方法路径
  （``channel.of_token``），端点号保持令牌原来的形状；请求经隧道时桥丢掉云端带的 ``Authorization``、按路径换上这个站点的真令牌
  （``tunnel_relay``、``host.CloudHost._route``），真令牌从不落云端磁盘。
- 成员要有账套（``ProfileRecord`` 要求 Express 成员有 ``account_dir`` / ``account_set``）：先用本机已有成员的数据根，没有就
  照本地「第一次连接」自动找（``express_machine.detect_roots``，云端模式只找本机盘、不碰 ``\\tsclient``），取默认年度里第一个
  够得到的账套作内部默认（账套由网页选，同本地）。找不到就**先不建**，结果码 ``no_books``（报给桥，设置页云端行显示）。
- 已有的云端成员：地址或通道变了就改，账套、科目原样。

本模块不导入 Qt（云端精简包里没有）。读写本机文件，探测函数都可注入，Mac 上离线测。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional

from ..logger import get_logger

log = get_logger(__name__)

PREFIX = "cloud-"
PLACEHOLDER = "cloud-relay"
TOKEN_PREFIX = {"cowork": "exp_", "core": "pcc_"}

READY = "ready"          # 下发的站点都有成员
NO_BOOKS = "no_books"    # 有站点要建，但本机找不到 Express 账套
NONE = "none"            # 桥还没登记任何站点


def profile_id(site: str) -> str:
    return PREFIX + str(site)


def is_cloud_member(record) -> bool:
    return str(getattr(record, "profile_id", "") or "").startswith(PREFIX)


def placeholder_token(entry: dict) -> str:
    """占位令牌（见模块说明）。不是秘密，桥会换掉。"""
    return TOKEN_PREFIX[entry["channel"]] + (entry.get("endpoint") or "cloud") + "_" + PLACEHOLDER


def sync(members: Iterable[dict], *, load: Optional[Callable] = None, detect: Optional[Callable] = None,
         probe_sets: Optional[Callable] = None, probe_defaults: Optional[Callable] = None) -> tuple:
    """按下发的成员（``ctl_files.members_of`` 的结果）改本机成员。回 ``(结果码, 有没有改动)``。"""
    from .. import express_machine
    from ..account_probe import probe_account_sets, probe_default_accounts
    from ..pairing import ACCOUNT_MAP_KEYS
    from ..profile_store import ProfileStore

    load = load or ProfileStore.load
    detect = detect or express_machine.detect_roots
    probe_sets = probe_sets or probe_account_sets
    probe_defaults = probe_defaults or probe_default_accounts
    wanted = {profile_id(entry["site"]): entry for entry in members}
    changed = False

    store = load()
    for record in list(store.profiles):
        if is_cloud_member(record) and record.profile_id not in wanted:
            store.remove_profile(record.profile_id)
            changed = True
            log.info("云端成员已删 · 成员=%s", record.profile_id)

    store = load()
    existing = {record.profile_id: record for record in store.profiles}
    for pid, entry in wanted.items():
        record = existing.get(pid)
        if record is None:
            continue
        cfg = store.runtime_config(pid)
        url, token = "https://" + entry["site"], placeholder_token(entry)  # scan: fake
        if str(cfg.backend_url or "") != url or str(cfg.bearer_token or "") != token:
            cfg.update(backend_url=url, bearer_token=token)
            store.save_profile(cfg, paused=record.paused)
            changed = True
            log.info("云端成员已改 · 成员=%s", pid)

    missing = [pid for pid in wanted if pid not in existing]
    if not missing:
        return (READY if wanted else NONE), changed

    store = load()
    roots = express_machine.machine_roots(store) or list(detect(store) or [])
    chosen = None
    if roots:
        try:
            chosen = express_machine.default_account(probe_sets(roots[0]))
        except Exception as exc:  # noqa: BLE001 - 读不了登记表：当没找到
            log.warning("云端读 Express 登记表失败：%s", type(exc).__name__)
    if chosen is None:
        log.warning("云端没找到 Express 账套，成员先不建 · 站点数=%d", len(missing))
        return NO_BOOKS, changed
    defaults = probe_defaults(chosen.path) or {}
    home = roots[0]
    for pid in missing:
        entry = wanted[pid]
        cfg = store.new_runtime_config()
        cfg.profile_id = pid
        cfg.update(
            bearer_token=placeholder_token(entry),
            backend_url="https://" + entry["site"],
            kind="express",
            express_root=home,
            authorized_roots=express_machine.authorized_order(roots),
            express_exe=str(Path(home) / "ExpressI.exe"),
            account_dir=str(chosen.path),
            account_set=str(chosen.path),
            account_company=chosen.company,
            account_set_row=int(getattr(chosen, "row", 0) or 0),
            **{key: str(defaults.get(key, "") or "") for key in ACCOUNT_MAP_KEYS},
        )
        store.save_profile(cfg)
        store = load()
        changed = True
        log.info("云端成员已建 · 成员=%s", pid)
    return READY, changed


__all__ = ["NONE", "NO_BOOKS", "PLACEHOLDER", "PREFIX", "READY", "is_cloud_member", "placeholder_token", "profile_id",
           "sync"]
