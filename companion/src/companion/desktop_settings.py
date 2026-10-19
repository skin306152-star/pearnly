# -*- coding: utf-8 -*-
"""Tomo 设置页「此电脑」一栏背后的本机操作（不碰界面，2026-10-10 起取代原小助手主窗口）。

设置界面在对话页里（frontend/assistant/src/components/settings.tsx，同 Claude 的设置），经 tomo_bridge 调到这里：

- 连接：这台电脑的 Express 数据根（检测一次、「更换…」时同步到全部成员）；成员列表（状态、更换配对码、暂停 / 恢复、移除）；
  以前授权、现在没人用的文件夹（``legacy_roots``，可以移除）。
- 公司的文件夹（2026-10-15 起，CC 按公司分组）：侧栏里公司的「选择文件夹…」经桥弹本机原生选择框（``pick_folder``），
  这里判定能不能用、复用已有的根目录还是新加一个（经公司添加的没人用一小时后自动收回，见 folder_worker）。授权仍只在本机设，
  是安全边界（见 folder_store）；网页传来的路径一概不收。
- 系统：开机自动启动、快捷键、版本与更新、日志文件夹。

返回给网页的都是普通数据；客户可见的报错文案按网页当前语言用小助手的术语表渲染。
"""
from __future__ import annotations

import ntpath
import os
import subprocess
import sys
import time
from typing import Callable, Mapping, Optional
from urllib.parse import urlsplit

from . import autostart, device_link, folder_store, master_refresh, pairing, pairing_flow
from . import folder_plan as plan
from .express_machine import apply_roots, detect_roots, machine_roots, rescan_roots, roots_for_pick, same_root
from .i18n import MessageSpec, normalize, t
from .logger import get_logger
from .profile_store import ProfileRecord, ProfileStore
from .queue_client import _device_name
from .version import VERSION

log = get_logger(__name__)


def status_key(record: ProfileRecord, state: Optional[tuple]) -> str:
    """成员状态（同原主窗口）：paused 优先；密钥服务端说不能再用（device_state，等对话页重新登记）是 attention；
    其余按取任务线程报的状态归成 5 种。"""
    if record.paused:
        return "paused"
    if getattr(record, "device_state", ""):
        return "attention"
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


def site_of(store: ProfileStore) -> str:
    """这台电脑连的是哪家公司（站点域名）：所有成员同一个后端（global backend_url），没有成员就是空。"""
    if not store.profiles:
        return ""
    return (urlsplit(str(store.global_settings.backend_url or "")).hostname or "").lower()


def code_site(code: str) -> str:
    """配对码 `密钥@域名` 里的域名（2026-09-27 起的格式）；旧格式没有域名就是空。"""
    return str(code or "").rpartition("@")[2].strip().lower() if "@" in str(code or "") else ""


def member_name(record: ProfileRecord) -> str:
    # MR.ERP 的成员没有心跳身份（只读文件夹），名字用连接名
    return (str(getattr(record, "pearnly_account", "") or "").strip()
            or str(getattr(record, "endpoint_name", "") or "").strip() or "-")


def program_folder() -> str:
    """CC 自己的程序文件夹（打包后才有；开发时为空）。"""
    return os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else ""


def inside(path: str, folder: str) -> bool:
    """path 是 folder 本身或在它下面（Windows 路径，不分大小写）。"""
    left = ntpath.normcase(ntpath.normpath(str(path or ""))).rstrip("\\")
    right = ntpath.normcase(ntpath.normpath(str(folder or ""))).rstrip("\\")
    return bool(left and right) and (left == right or left.startswith(right + "\\"))


def picker_start(current, documents: str, program: str = "") -> str:
    """选文件夹的窗口从哪打开：上次那个文件夹的上一级，没有就是「文档」；不从 CC 的程序文件夹里打开。不能给空值——
    空值时 Qt 从工作目录打开，而 CC 的工作目录就是程序文件夹，一点「选择文件夹」就把它开放给了网页（2026-10-10 测试机上发生过）。"""
    for path in reversed([str(p) for p in current or [] if p]):
        parent = ntpath.dirname(path.rstrip("\\/"))
        if parent and os.path.isdir(parent) and not (program and inside(parent, program)):
            return parent
    return documents

# pick_folder 失败码 → CC 桌面版术语表的提示（folders_contains 带被包含的文件夹 {path}）
PICK_MESSAGES = {"not_connected": "folders_not_connected", "other_site": "folders_other_site",
                 "program_folder": "folders_program", "too_broad": "folders_too_broad",
                 "contains_existing": "folders_contains", "roots_full": "folders_full"}


def pick_failure(code: str, lang: str, **fmt) -> dict:
    return {"ok": False, "code": code, "message": t(PICK_MESSAGES[code], normalize(lang), **fmt)}


def main_member(store: ProfileStore) -> Optional[ProfileRecord]:
    """新加的公司文件夹放在哪个成员下：有 Express 成员用它，没有就用 MR.ERP 成员（同设置页的主成员）。"""
    return next((r for r in store.profiles if (getattr(r, "kind", "express") or "express") != "folder"),
                store.profiles[0] if store.profiles else None)


class DesktopSettings:
    def __init__(self, *, states: Callable[[], Mapping[str, tuple]] = dict,
                 device: Callable[[], dict] = dict,
                 leave: Callable[[str, str], bool] = device_link.leave,
                 settle_old: Callable[..., str] = device_link.settle_old_key,
                 update_status: Callable[[], tuple] = lambda: ("none", ""),
                 on_update: Callable[[], None] = lambda: None,
                 on_download: Callable[[], None] = lambda: None,
                 on_profiles_changed: Callable[[], None] = lambda: None,
                 on_folders_changed: Callable[[], None] = lambda: None,
                 on_keep_awake: Callable[[bool, bool], None] = lambda enabled, on_battery: None,
                 hotkey: Callable[[], bool] = lambda: False,
                 report_roots: Callable[[str], bool] = lambda profile_id: False,
                 wake_folders: Callable[[], None] = lambda: None,
                 roots_in_use: Callable[[str], Optional[frozenset]] = lambda key: None,
                 max_roots: Callable[[str], int] = lambda key: plan.DEFAULT_MAX_ROOTS,
                 program: Callable[[], str] = lambda: program_folder(),
                 env: Optional[Mapping[str, str]] = None,
                 load_store: Callable[[], ProfileStore] = ProfileStore.load) -> None:
        self._states = states
        # 多台电脑（ADR-021）：这台电脑的设备密钥与电脑名（device_link.identity，对话页登记时带上）；断开时先吊销本机这把密钥
        self._device = device
        self._leave = leave
        self._settle_old = settle_old   # 配对换掉成员的旧密钥前，先拿旧密钥认一次电脑（device_link.settle_old_key）
        self._update_status = update_status
        self._on_update = on_update
        self._on_download = on_download
        self._on_profiles_changed = on_profiles_changed
        self._on_folders_changed = on_folders_changed
        self._on_keep_awake = on_keep_awake   # 保持电脑唤醒的两个开关变了：托盘马上拿住 / 放开（keep_awake.py）
        self._hotkey = hotkey
        # 文件夹线程（folder_worker.FolderIntake）的几样：快速同步、唤醒、最近一次的在用根目录与上限（按连接键）
        self._report_roots = report_roots
        self._wake_folders = wake_folders
        self._roots_in_use = roots_in_use
        self._max_roots = max_roots
        self._program = program
        self._env = env   # 判定「系统位置」用的环境变量（测试里换掉；None = 本机的）
        self._load_store = load_store
        self._detected: list[str] | None = None
        # 断开最后一个 Express 成员前这台电脑授权的数据根（_keep_roots），下次连接时照旧授权
        self._kept: list[str] = []

    def _store(self) -> ProfileStore:
        return self._load_store()   # 每次从磁盘重读：取任务线程可能刚更新过成员，不拿旧副本盖回去

    # ── 读 ──
    def snapshot(self) -> dict:
        store = self._store()
        states = dict(self._states() or {})
        roots = machine_roots(store) or list(self._detected or self._kept or [])
        members, legacy = [], []
        for record in store.profiles:
            try:
                key = folder_store.connection_key(store.runtime_config(record.profile_id))
            except Exception:
                key = ""
            legacy += self._legacy(record.profile_id, key)
            members.append({
                "id": record.profile_id,
                "name": member_name(record),
                "kind": getattr(record, "kind", "express") or "express",
                "paused": bool(record.paused),
                "status": status_key(record, states.get(record.profile_id)),
                # superseded / claimed_elsewhere：这把密钥不再用，对话页据此重新登记（superseded 带 rekey）
                "device_state": str(getattr(record, "device_state", "") or ""),
                "folders": [row["path"] for row in folder_store.roots(key)] if key else None,
            })
        try:
            update_state, update_version = self._update_status()
        except Exception:
            update_state, update_version = "none", ""
        try:
            device = dict(self._device() or {})
        except Exception:
            log.exception("取设备密钥失败")
            device = {}
        return {
            "version": VERSION,
            "site": site_of(store),
            # 这台电脑（设备密钥 + 电脑名）：对话页每次打开带它调 register（多台电脑，ADR-021）；旧版桥没有这一项
            "device": device,
            "express_roots": roots,
            "members": members,
            "autostart": self._autostart(store),
            # CC 开着时保持电脑唤醒（默认开）/ 用电池时也保持（默认关，D8）（keep_awake.py）；旧版桥没有这两项，设置页据此不显示
            "keep_awake": bool(store.global_settings.keep_awake),
            "keep_awake_on_battery": bool(store.global_settings.keep_awake_on_battery),
            "hotkey": bool(self._hotkey()),
            "update": {"state": update_state, "version": update_version},
            "legacy_roots": legacy,
        }

    def _legacy(self, member: str, key: str) -> list:
        """以前在设置里授权（不是经公司添加）、最近一次服务端说没人用（不在 roots_in_use 里）的根目录：设置页「以前授权的
        文件夹」一行据此列出、可以移除。还没收到过 roots_in_use（旧服务端、刚启动）就一个都不列。"""
        if not key:
            return []
        try:
            used = self._roots_in_use(key)
        except Exception:
            used = None
        if used is None:
            return []
        return [{"member": member, "id": row["id"], "path": row["path"]} for row in folder_store.roots(key)
                if row.get("origin") != "company" and row["id"] not in used]

    @staticmethod
    def _autostart(store) -> bool:
        try:
            return autostart.is_enabled()
        except Exception:   # 非 Windows（开发机）读不到注册表：按已存设置
            return bool(store.global_settings.autostart)

    def detect(self) -> list[str]:
        """还没有成员时后台找一次 Express（读共享盘上的登记表较慢）。断开连接前授权过的（``_kept``）一并找；其中这会儿访问
        不到的排在找到的后面、照旧授权（同「重新查找」）；一个都没找到时不拿它们去配对（配对要读得到账套），先留着。"""
        store = self._store()
        if store.profiles:
            return machine_roots(store)
        if self._kept:
            roots, kept = rescan_roots(store, self._kept)
            self._detected = roots if len(roots) > len(kept) else []
        else:
            self._detected = list(detect_roots(store) or [])
        return self._detected

    # ── 写 ──
    def pair(self, code: str, lang: str, profile_id: Optional[str] = None, switch: bool = False) -> dict:
        """（后台线程）用配对码添加成员；给了 profile_id 就是更换这个成员的配对码。

        CC 登录后自动连接（2026-10-10）也走这里，配对码由对话页向本站申请后交过来。一台电脑只连一家公司：
        已连着别家时不混进来，回 other_site；用户点了「改连本公司」（switch）才先断开原来的全部成员。"""
        store = self._store()
        current, wanted = site_of(store), code_site(code)
        switched = False
        if profile_id is None and current and wanted and wanted != current:
            if not switch:
                return {"ok": False, "other_site": current}
            for record in list(store.profiles):
                self._leave_member(store, record.profile_id)
                self._keep_roots(store, record.profile_id)
                store.remove_profile(record.profile_id)
            store, switched = self._store(), True
        if profile_id is None:
            # 这台电脑原来就连着这条连接（被别的电脑顶掉后重新连接）：换这个成员的密钥，不多出一个成员
            endpoint = pairing._endpoint_id_from_token(str(code or "").strip().partition("@")[0])
            profile_id = next((r.profile_id for r in store.profiles if endpoint and r.endpoint_id == endpoint), None)
            if profile_id is not None:
                self._settle_member(store, profile_id, str(code or "").strip().partition("@")[0])
        result = self._pair(store, code, lang, profile_id)
        if switched:
            result["profiles_changed"] = True   # 原来的成员已断开：失败也要重启取任务线程
        return result

    def _pair(self, store: ProfileStore, code: str, lang: str, profile_id: Optional[str]) -> dict:
        roots = machine_roots(store) or list(self._detected or [])
        if not roots and profile_id is None and self._detected is None:
            # 还没找过 Express 就别下「这台电脑没有 Express」的结论（没有才按只读文件夹的成员加入池，pairing_flow）
            roots = list(self.detect() or [])
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
        store = self._store()
        self._keep_roots(store, profile_id)
        store.remove_profile(profile_id)
        self._on_profiles_changed()
        return {"ok": True}

    def _keep_roots(self, store: ProfileStore, profile_id: str) -> None:
        """要删本机成员前（断开连接、移除、改连别的公司）。Express 数据根是这台电脑的设置、存在各成员记录里（express_machine
        文件头）：删的是最后一个 Express 成员时先记下它们，下次在这台电脑上连接时照旧授权（暂时访问不到的也保留，见 detect）。
        断开连接只吊销密钥、删成员，不该顺带丢掉用户授权的 Express 文件夹（2026-10-20 判断：C9 观察到断开清掉 17 个，不是
        设计）。只记在内存里：没有成员时 CC 重启就没了（照旧重新查找）。"""
        others = [r for r in store.profiles if r.profile_id != profile_id
                  and (getattr(r, "kind", "express") or "express") != "folder"]
        if not others:
            roots = machine_roots(store)
            if roots:
                self._kept, self._detected = list(roots), None

    def _settle_member(self, store: ProfileStore, profile_id: str, new_token: str) -> None:
        """对话页登记来的新密钥要换掉这个成员的旧密钥：旧密钥还没被服务端说过不能再用（没有 ``device_state``）就先拿它认一次
        电脑，让服务端把旧槽位那台「旧电脑」并进这台（device_link.settle_old_key）。被说过的不发（服务端已处理）；失败不拦配对。"""
        record = next((r for r in store.profiles if r.profile_id == profile_id), None)
        if record is None or getattr(record, "device_state", ""):
            return
        try:
            cfg = store.runtime_config(profile_id)
            old = str(cfg.bearer_token or "")
            if old and old != new_token:
                self._settle_old(str(cfg.backend_url or ""), old)
        except Exception:
            log.exception("换密钥前旧密钥认电脑失败（照常配对）")

    def _leave_member(self, store: ProfileStore, profile_id: str) -> bool:
        """断开前先请服务端吊销这把密钥（device_leave）；旧服务端、连不上、密钥早已失效都不拦（只删本机）。"""
        try:
            cfg = store.runtime_config(profile_id)
            return bool(self._leave(str(cfg.backend_url or ""), str(cfg.bearer_token or "")))
        except Exception:
            log.exception("断开时吊销密钥失败（只删本机成员）")
            return False

    def disconnect(self, profile_id: str) -> dict:
        """（后台线程）设置里「断开连接」：先吊销本机这把密钥（device_leave），再删本机成员（方案 2.8）。
        由桥回到界面线程重启各线程（profiles_changed）。"""
        store = self._store()
        left = self._leave_member(store, profile_id)
        self._keep_roots(store, profile_id)
        self._store().remove_profile(profile_id)
        return {"ok": True, "left": left, "profiles_changed": True}

    def set_autostart(self, enabled: bool) -> dict:
        autostart.enable() if enabled else autostart.disable()
        store = self._store()
        cfg = store.new_runtime_config()
        cfg.update(autostart=bool(enabled))
        store.save_profile(cfg)
        return {"ok": True}

    def set_keep_awake(self, *, enabled: Optional[bool] = None, on_battery: Optional[bool] = None) -> dict:
        """「CC 开着时保持电脑唤醒」「用电池时也保持」（keep_awake.py）：存进全局设置，托盘马上按新设置拿住 / 放开。"""
        store = self._store()
        cfg = store.new_runtime_config()
        if enabled is not None:
            cfg.update(keep_awake=bool(enabled))
        if on_battery is not None:
            cfg.update(keep_awake_on_battery=bool(on_battery), keep_awake_on_battery_set=True)   # 用户设过：之后不再按默认
        store.save_profile(cfg)
        self._on_keep_awake(bool(cfg.keep_awake), bool(cfg.keep_awake_on_battery))
        return {"ok": True}

    def change_roots(self, picked: str, lang: str) -> dict:
        """（后台线程）「更换…」选的 Express 数据所在文件夹：有成员时同步到全部成员（服务端下次心跳拿到，不用重新配对）。"""
        began = time.monotonic()
        found = roots_for_pick(ntpath.normpath(picked))  # Qt 给的是正斜杠，存成 Windows 写法
        log.info("更换 Express 数据文件夹：找到 %d 个年度，用时 %.1f 秒", len(found), time.monotonic() - began)
        # 选到的排前面，原来已授权的接在后面（合并、不覆盖）：心跳本来也会把同一位置的年度补回来，覆盖只会让
        # 局域网上的年度空窗一两分钟、期间推送那些账套被拒（2026-10-10 测试机）。
        current = machine_roots(self._store())
        return self._use_roots(found and [*found, *(root for root in current
                                                    if not any(same_root(root, new) for new in found))], lang)

    def rescan(self, lang: str) -> dict:
        """（后台线程）「重新查找」：全机 + 局域网再找一遍 Express，找到的全部授权（同「更换…」写到全部成员）；原来授权、
        这次暂时访问不到的（夜里关着的服务器、没连上的网络盘）照旧保留，确实没了的才去掉（express_machine.rescan_roots）。
        之后账套清单整套重扫一遍（master_refresh.rescan_soon）：账套目录改名、挪走不改登记表，平时每分钟那一眼看不出来，
        不重扫就一直报旧的那一行（2026-10-20 测试机）。"""
        began = time.monotonic()
        store = self._store()
        roots, kept = rescan_roots(store, machine_roots(store) or list(self._kept))
        log.info("重新查找 Express：找到 %d 个年度，暂时访问不到、保留授权 %d 个，用时 %.1f 秒",
                 len(roots) - len(kept), len(kept), time.monotonic() - began)
        if kept:
            log.info("暂时访问不到的数据根（保留授权，下次在线时照常扫描）：%s", kept)
        result = self._use_roots(roots, lang, offline=kept)
        if result.get("ok"):
            master_refresh.rescan_soon()
        return result

    def _use_roots(self, roots: list, lang: str, offline: list = ()) -> dict:
        """``offline``：其中只是暂时访问不到、照旧保留授权的（「重新查找」）。一个都没找到、只剩保留的：什么都不改。"""
        if not roots:
            return {"ok": False, "message": t("express_not_found", lang)}
        found = [root for root in roots if root not in offline]
        if not found:
            return {"ok": True}
        store = self._store()
        if store.profiles:
            if any((getattr(r, "kind", "express") or "express") != "folder" for r in store.profiles):
                apply_roots(store, roots, offline)
            # 只读成员按 Express 重新配对：读得到的排前面（配对要读账套），暂时访问不到的接在后面照旧授权
            self._upgrade_pool_members([*found, *(root for root in roots if root in offline)], lang)
            return {"ok": True, "profiles_changed": True}
        self._detected = list(roots)
        return {"ok": True}

    def _upgrade_pool_members(self, roots: list, lang: str) -> None:
        """当初没有 Express、按只读文件夹的成员加入 Express 池的（pairing_flow，存了 ``pool`` 标记），现在找到了 Express：用它原来的
        密钥按 Express 重新配对一次（成了能写账的成员）。MR.ERP 的成员（没有 ``pool``）不动，也不为判断它联网查连接类型；
        密钥被服务端说过不能再用的（``device_state``）不动，等对话页重新登记换上新密钥。"""
        store = self._store()
        for record in list(store.profiles):
            if (getattr(record, "kind", "express") or "express") != "folder" or not getattr(record, "pool", False):
                continue
            if getattr(record, "device_state", ""):
                continue
            try:
                result = pairing_flow.run(store, "", roots=roots, profile_id=record.profile_id, lang=normalize(lang))
                log.info("只读成员按 Express 重新配对：%s", bool(result and result.ok))
            except Exception:
                log.exception("只读成员按 Express 重新配对失败")

    def set_folders(self, profile_id: str, paths: list, lang: str) -> dict:
        store = self._store()
        key = folder_store.connection_key(store.runtime_config(profile_id))
        try:
            folder_store.set_roots(key, [str(p) for p in paths])
        except ValueError as error:
            return {"ok": False, "message": t("folders_overlap", lang, path=str(error))}
        self._on_folders_changed()
        return {"ok": True}

    # ── 公司的文件夹（pick_folder） ──
    def pick_ready(self, page_host: str, lang: str) -> Optional[dict]:
        """弹选择框之前（界面线程）：没连公司 → not_connected；对话页不是这台电脑连的那家公司 → other_site。可以就是 None。"""
        store = self._store()
        if not store.profiles:
            return pick_failure("not_connected", lang)
        site = site_of(store)
        if not site or str(page_host or "").lower() != site:
            return pick_failure("other_site", lang)
        return None

    def pick_folder(self, picked: str, page_host: str, lang: str) -> dict:
        """（后台线程）原生选择框里选好的文件夹（规格 §7.2）：判定能不能用，复用已有的根目录或在主成员下新加一个，
        返回给页面的绑定 ``{reader, root_id, folder, label, share, device}``（页面原样交给服务端，自己不拼）。

        先清掉 roots.json 里已不属于任何当前成员的连接下的根目录（只会收紧）；新加了根目录就马上快速同步一次
        （``reported`` = 服务端收下了没有；没收下也照样成功，下一轮完整同步再报）。"""
        early = self.pick_ready(page_host, lang)
        if early is not None:
            return early
        store = self._store()
        path = os.path.normpath(str(picked or "").strip())
        members = []
        for record in store.profiles:
            try:
                cfg = store.runtime_config(record.profile_id)
                members.append((record, cfg, folder_store.connection_key(cfg)))
            except Exception:
                log.exception("读取成员配置失败")
                members.append((record, None, ""))
        if all(key for _record, _cfg, key in members):
            dropped = folder_store.prune(key for _record, _cfg, key in members)
            if dropped:
                log.info("清掉已不属于任何成员的授权文件夹 %d 个：%s", len(dropped), dropped)
        main = main_member(store)
        main_key = next((key for record, _cfg, key in members if record is main), "")
        if not main_key:
            return pick_failure("not_connected", lang)
        by_key = {key: folder_store.roots(key) for _record, _cfg, key in members if key}
        decision = plan.decide_pick(path, by_key, program=self._program(), max_roots=self._max_roots(main_key),
                                    env=self._env)
        if decision.code == "contains_existing":
            return pick_failure("contains_existing", lang, path=decision.conflict)
        if decision.code:
            return pick_failure(decision.code, lang)
        created, reported = not decision.owner, True
        if decision.owner:
            owner_key, row, folder = decision.owner, decision.root, decision.folder
        else:
            try:
                row = folder_store.add_root(main_key, path, origin="company")
            except ValueError as error:
                return pick_failure("contains_existing", lang, path=str(error))
            owner_key, folder = main_key, ""
            log.info("经公司添加文件夹：%s", path)
            self._on_folders_changed()
            try:
                reported = bool(self._report_roots(main.profile_id))
            except Exception:
                log.exception("快速同步文件夹失败")
                reported = False
            self._wake_folders()
        record, cfg = next((record, cfg) for record, cfg, key in members if key == owner_key)
        reader = pairing._endpoint_id_from_token(str(getattr(cfg, "bearer_token", "") or "")) or str(
            getattr(cfg, "endpoint_id", "") or "")
        label = row["path"].rstrip("\\/") + "\\" + folder if folder else row["path"]
        return {"ok": True, "binding": {"reader": reader, "root_id": row["id"], "folder": folder, "label": label,
                                        "share": plan.share_path(row["path"]), "device": _device_name()},
                "name": plan.folder_name(path), "created": created, "reported": reported}

    def update(self) -> dict:
        self._on_update()
        return {"ok": True}

    def download(self) -> dict:
        """自动更新被 Windows 拦下时的「下载新版」：在系统浏览器打开官网下载（托盘注入，update_block.py）。"""
        self._on_download()
        return {"ok": True}

    @staticmethod
    def open_logs() -> dict:
        from .config import LOG_DIR

        if sys.platform == "win32":
            os.startfile(str(LOG_DIR))
        else:
            subprocess.Popen(["open", str(LOG_DIR)])
        return {"ok": True}
