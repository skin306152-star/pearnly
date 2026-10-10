# -*- coding: utf-8 -*-
"""Tomo 设置页「此电脑」一栏背后的本机操作（不碰界面，2026-10-10 起取代原小助手主窗口）。

设置界面在对话页里（frontend/assistant/src/components/settings.tsx，同 Claude 的设置），经 tomo_bridge 调到这里：

- 连接：这台电脑的 Express 数据根（检测一次、「更换…」时同步到全部成员）；成员列表（状态、更换配对码、暂停 / 恢复、移除）；
  每个成员网页可选的文件夹（只能在本机设，是安全边界，见 folder_store）。
- 系统：开机自动启动、快捷键、版本与更新、日志文件夹。

返回给网页的都是普通数据；客户可见的报错文案按网页当前语言用小助手的术语表渲染。
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable, Mapping, Optional

from . import autostart, folder_store, pairing_flow
from .express_machine import apply_roots, detect_roots, machine_roots, roots_for_pick
from .i18n import MessageSpec, normalize, t
from .logger import get_logger
from .profile_store import ProfileRecord, ProfileStore
from .version import VERSION

log = get_logger(__name__)


def status_key(record: ProfileRecord, state: Optional[tuple]) -> str:
    """成员状态（同原主窗口）：paused 优先；其余按取任务线程报的状态归成 5 种。"""
    if record.paused:
        return "paused"
    if state is None or state[0] == "starting":
        return "connecting"
    current = state[0] if state else "offline"
    if current == "idle":
        return "online"
    if current in {"working", "indexing"}:
        return "processing"
    if current in {"waiting_lock", "error"}:
        return "attention"
    return "offline"


def member_name(record: ProfileRecord) -> str:
    # MR.ERP 的成员没有心跳身份（只读文件夹），名字用连接名
    return (str(getattr(record, "pearnly_account", "") or "").strip()
            or str(getattr(record, "endpoint_name", "") or "").strip() or "-")


class DesktopSettings:
    def __init__(self, *, states: Callable[[], Mapping[str, tuple]] = dict,
                 update_status: Callable[[], tuple] = lambda: ("none", ""),
                 on_update: Callable[[], None] = lambda: None,
                 on_profiles_changed: Callable[[], None] = lambda: None,
                 on_folders_changed: Callable[[], None] = lambda: None,
                 hotkey: Callable[[], bool] = lambda: False,
                 load_store: Callable[[], ProfileStore] = ProfileStore.load) -> None:
        self._states = states
        self._update_status = update_status
        self._on_update = on_update
        self._on_profiles_changed = on_profiles_changed
        self._on_folders_changed = on_folders_changed
        self._hotkey = hotkey
        self._load_store = load_store
        self._detected: list[str] | None = None

    def _store(self) -> ProfileStore:
        return self._load_store()   # 每次从磁盘重读：取任务线程可能刚更新过成员，不拿旧副本盖回去

    # ── 读 ──
    def snapshot(self) -> dict:
        store = self._store()
        states = dict(self._states() or {})
        roots = machine_roots(store) or list(self._detected or [])
        members = []
        for record in store.profiles:
            try:
                key = folder_store.connection_key(store.runtime_config(record.profile_id))
            except Exception:
                key = ""
            members.append({
                "id": record.profile_id,
                "name": member_name(record),
                "kind": getattr(record, "kind", "express") or "express",
                "paused": bool(record.paused),
                "status": status_key(record, states.get(record.profile_id)),
                "folders": [row["path"] for row in folder_store.roots(key)] if key else None,
            })
        try:
            update_state, update_version = self._update_status()
        except Exception:
            update_state, update_version = "none", ""
        return {
            "version": VERSION,
            "express_roots": roots,
            "members": members,
            "autostart": self._autostart(store),
            "hotkey": bool(self._hotkey()),
            "update": {"state": update_state, "version": update_version},
        }

    @staticmethod
    def _autostart(store) -> bool:
        try:
            return autostart.is_enabled()
        except Exception:   # 非 Windows（开发机）读不到注册表：按已存设置
            return bool(store.global_settings.autostart)

    def detect(self) -> list[str]:
        """还没有成员时后台找一次 Express（读共享盘上的登记表较慢）。"""
        store = self._store()
        if store.profiles:
            return machine_roots(store)
        self._detected = list(detect_roots(store) or [])
        return self._detected

    # ── 写 ──
    def pair(self, code: str, lang: str, profile_id: Optional[str] = None) -> dict:
        """（后台线程）用配对码添加成员；给了 profile_id 就是更换这个成员的配对码。"""
        store = self._store()
        roots = machine_roots(store) or list(self._detected or [])
        try:
            result = pairing_flow.run(store, str(code or "").strip(), roots=roots, profile_id=profile_id,
                                      lang=normalize(lang))
        except Exception:
            log.exception("配对失败")
            return {"ok": False, "message": MessageSpec("pair_fail", {"err": MessageSpec("pair_err_unexpected")}).render(lang)}
        if result is None:
            return {"ok": False, "message": t("status_reg_error", lang)}
        if not result.ok and result.error_key == pairing_flow.NEED_ROOTS:
            return {"ok": False, "message": t("browse_hint", lang)}
        if not result.ok:
            reason = MessageSpec(result.error_key or "pair_err_unexpected", result.error_fmt or {})
            return {"ok": False, "message": MessageSpec("pair_fail", {"err": reason}).render(lang)}
        return {"ok": True, "profiles_changed": True}   # 在后台线程里：由桥回到界面线程再重启取任务线程

    def profiles_changed(self) -> None:
        """成员变了：重启取任务线程（只在界面线程调）。"""
        self._on_profiles_changed()

    def set_paused(self, profile_id: str, paused: bool) -> dict:
        self._store().set_paused(profile_id, bool(paused))
        self._on_profiles_changed()
        return {"ok": True}

    def remove(self, profile_id: str) -> dict:
        self._store().remove_profile(profile_id)
        self._on_profiles_changed()
        return {"ok": True}

    def set_autostart(self, enabled: bool) -> dict:
        autostart.enable() if enabled else autostart.disable()
        store = self._store()
        cfg = store.new_runtime_config()
        cfg.update(autostart=bool(enabled))
        store.save_profile(cfg)
        return {"ok": True}

    def change_roots(self, picked: str, lang: str) -> dict:
        """（后台线程）「更换…」选的 Express 数据所在文件夹：有成员时同步到全部成员（服务端下次心跳拿到，不用重新配对）。"""
        roots = roots_for_pick(picked)
        if not roots:
            return {"ok": False, "message": t("status_reg_error", lang)}
        store = self._store()
        if store.profiles:
            apply_roots(store, roots)
            return {"ok": True, "profiles_changed": True}
        self._detected = list(roots)
        return {"ok": True}

    def set_folders(self, profile_id: str, paths: list, lang: str) -> dict:
        store = self._store()
        key = folder_store.connection_key(store.runtime_config(profile_id))
        try:
            folder_store.set_roots(key, [str(p) for p in paths])
        except ValueError as error:
            return {"ok": False, "message": t("folders_overlap", lang, path=str(error))}
        self._on_folders_changed()
        return {"ok": True}

    def update(self) -> dict:
        self._on_update()
        return {"ok": True}

    @staticmethod
    def open_logs() -> dict:
        from .config import LOG_DIR

        if sys.platform == "win32":
            os.startfile(str(LOG_DIR))
        else:
            subprocess.Popen(["open", str(LOG_DIR)])
        return {"ok": True}
