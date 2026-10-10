# -*- coding: utf-8 -*-
"""添加成员 / 更换配对码的流程（不碰界面；Tomo 设置页经 tomo_bridge 调用，2026-10-10 起取代原配对窗）。

- 先问这个配对码是哪种连接：MR.ERP → 只读文件夹的成员（不需要 Express）；
- Express → 用这台电脑的 Express 数据根读账套名单，定内部默认账套与科目（不让人选，账套由网页第一步选），
  再配对（连 Pearnly 校验密钥并上报账套）。
- 更换配对码只换密钥：用这个成员自己原来的数据根，默认账套、人工改过的科目、授权年度都不变。
"""
from __future__ import annotations

from typing import List, Optional

from . import pairing
from .account_probe import probe_account_sets, probe_default_accounts
from .express_machine import authorized_order, default_account, member_roots
from .profile_store import ProfileStore, ProfileStoreError

NEED_ROOTS = "browse_hint"  # Express 配对码、但这台电脑还没找到 Express 数据根


def config_for(store: ProfileStore, profile_id: Optional[str]):
    if profile_id is None:
        return store.new_runtime_config()
    cfg = next((c for c in store.runtime_configs(include_paused=True) if c.profile_id == profile_id), None)
    if cfg is None:
        raise ProfileStoreError("profile not found")
    return cfg


def prefill_account_index(cfg, sets, prefill: bool) -> int:
    """内部默认账套：已配过且还在 → 沿用；否则取第一个。"""
    want = (cfg.account_set or cfg.account_dir or "").strip() if prefill else ""
    for i, acc in enumerate(sets):
        if want and str(getattr(acc, "path", "") or "").strip() == want:
            return i
    return 0 if sets else -1


def auto_mapping(cfg, chosen, defaults: dict, prefill: bool) -> dict:
    """科目映射全自动：同一账套重配沿用存值（保住人工改过的码），换账套用其 ISINFO 默认。"""
    want = (cfg.account_set or cfg.account_dir or "").strip()
    same_set = bool(prefill and want and str(getattr(chosen, "path", "") or "").strip() == want)
    out = {}
    for key in pairing.ACCOUNT_MAP_KEYS:
        stored = (getattr(cfg, key, "") or "").strip() if same_set else ""
        out[key] = stored or str((defaults or {}).get(key, "") or "")
    return out


def run(store: ProfileStore, token: str, *, roots: List[str], profile_id: Optional[str] = None,
        lang: str = "") -> Optional[pairing.PairResult]:
    """配对一次（在后台线程跑：读共享盘、连网）。返回 PairResult；读不到账套名单返回 None。"""
    cfg = config_for(store, profile_id)
    prefill = bool(cfg.paired)
    if profile_id is not None:
        roots = member_roots(cfg) or roots
    roots = [str(root) for root in (roots or []) if str(root or "").strip()]
    autostart = bool(getattr(store.global_settings, "autostart", True))
    if pairing.connection_kind(token, cfg) == "mrerp":
        return pairing.pair_folder(token=token, enable_autostart=autostart, lang=lang, config=cfg, profile_store=store)
    if not roots:
        return pairing.PairResult(False, error_key=NEED_ROOTS)
    sets = probe_account_sets(roots[0])
    index = prefill_account_index(cfg, sets, prefill)
    chosen = sets[index] if index >= 0 else None
    if chosen is None:
        chosen = default_account(sets)
    if chosen is None:
        return None
    try:
        defaults = probe_default_accounts(chosen.path) if chosen.path else {}
    except Exception:
        defaults = {}
    return pairing.pair(
        token=token,
        chosen=chosen,
        all_sets=sets,
        express_root=roots[0],
        enable_autostart=autostart,
        mapping=auto_mapping(cfg, chosen, defaults, prefill),
        authorized_roots=authorized_order(roots),
        lang=lang,
        config=cfg,
        profile_store=store,
    )
