# -*- coding: utf-8 -*-
"""Tomo 窗口里对话页和本机之间的桥（QWebChannel，对象名 tomoDesktop）。

对话页的设置（「此电脑」：连接、系统）经这里读写本机设置（desktop_settings.py）；文件夹选择框由这里弹本机原生的。
只在 Tomo 自己的对话页（*.pearnly.com/assistant）上应答，登录页等其他页面调用一律拒绝。

公司的文件夹（2026-10-15，CC 按公司分组）：``pick_folder`` 不收任何路径（网页传来的一概不看），在界面线程弹原生
选择框，选好后在后台判定（desktop_settings.pick_folder），只回绑定（读者、根目录编号、相对路径、显示名…），由页面交给
服务端。另要求对话页的站点就是这台电脑连着的那家公司（``page_url``），否则回 ``other_site``。
``state()`` 带 ``features: ["company_folders", "devices"]``（旧版没有，页面据此降级）与 ``legacy_roots``（设置页的旧授权清理行）。

多台电脑（ADR-021，1.7.0）：``state()`` 另带 ``device: {key, name}``（这台电脑的设备密钥与电脑名；取不到是空对象），对话页每次
打开都带它调 ``companion.api.register``（对话页一侧在第 6 批）；成员带 ``device_state``（``superseded`` / ``claimed_elsewhere``：
这把密钥不再用，对话页重新登记，前者带 ``rekey``）。设备密钥只交给本站自己的对话页（``allowed``），不进任何地址栏参数。

窗口在自己的进程里（2026-10-18 轻量化，window_link.py / window_main.py）：这个桥在托盘进程里照旧应答，窗口进程的
``RemoteBridge`` 把页面的调用转过来；选择文件夹框经 ``pick`` 请窗口进程弹（以 CC 窗口为父），窗口按钮在窗口进程里做。

协议（网页侧见 frontend/assistant/src/lib/desktop.ts）：
- state() → 当前设置的 JSON；本机状态变了发 changed()，网页再取一次。
- call(method, args_json) → 结果 JSON；慢的操作（配对、换 Express 文件夹、找 Express）先回 {"job": id}，
  做完发 done(id, 结果 JSON)。
"""
from __future__ import annotations

import json
import uuid
from typing import Callable, Optional
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QStandardPaths, Signal, Slot
from PySide6.QtWidgets import QFileDialog

from . import folder_plan as plan
from .desktop_settings import DesktopSettings, inside, picker_start, program_folder
from .gui_pairing_support import Probes
from .i18n import normalize, t
from .logger import get_logger

log = get_logger(__name__)

REFUSED = json.dumps({"ok": False, "refused": True})
# 本版桥能做的事（页面按这里判断，旧版没有这一项）：company_folders = 公司的「选择文件夹…」（pick_folder）；
# devices = 多台电脑（ADR-021，1.7.0）：state() 带 ``device``（设备密钥与电脑名，对话页每次打开带它调 register）、成员带
# ``device_state``（这把密钥不再用、要重新登记），``remove`` 先吊销本机这把密钥再删成员（慢，回 job）；
# sites = 每个成员带自己的站点与通道（1.11.0，ADR-025）：同一台电脑可以同时连一个 Cowork 站点和一个 ERP 站点，ERP 站点的
# 对话页据此登记这台电脑（配对码 ``pcc_…``）。
FEATURES = ("company_folders", "devices", "sites", "express_places")


class TomoBridge(QObject):
    changed = Signal()
    done = Signal(str, str)
    open_settings = Signal(str)   # 托盘「连接与设置」：让网页打开设置的某一栏

    def __init__(self, settings: DesktopSettings, *, allowed: Callable[[], bool], window: Callable[[], object],
                 set_lang: Callable[[str], None], page_url: Callable[[], str] = lambda: "",
                 pick: Optional[Callable[[str, str], str]] = None) -> None:
        super().__init__()
        self._settings = settings
        self._allowed = allowed
        self._page_url = page_url   # 对话页当前地址：pick_folder 核对它的站点就是这台电脑连着的那家公司
        self._window = window
        self._set_lang = set_lang
        # pick(标题, 从哪打开) → 选好的文件夹（没选是空串）：默认在本进程弹（以窗口为父）；托盘里经窗口进程弹（WindowHost.pick）
        self._pick = pick or (lambda title, start: QFileDialog.getExistingDirectory(self._window(), title, start))
        self._probes = Probes()

    @Slot(result=str)
    def state(self) -> str:
        if not self._allowed():
            return REFUSED
        try:
            # window.frameless：本版窗口没有系统标题栏，对话页要自己画（旧版桌面端没有这一项，页面就不画，免得两层标题栏）
            return json.dumps({**self._settings.snapshot(), "window": {"frameless": True}, "features": list(FEATURES)},
                              ensure_ascii=False)
        except Exception:
            log.exception("读取本机设置失败")
            return json.dumps({"ok": False})

    @Slot(str, str, result=str)
    def call(self, method: str, args_json: str) -> str:
        if not self._allowed():
            return REFUSED
        try:
            args = json.loads(args_json or "{}")
        except ValueError:
            args = {}
        lang = str(args.get("lang") or "")
        try:
            result = self._dispatch(str(method), args, lang)
        except Exception:
            log.exception("本机设置操作失败：%s", method)
            result = {"ok": False, "message": t("profiles_error", normalize(lang))}
        return json.dumps(result, ensure_ascii=False)

    def _dispatch(self, method: str, args: dict, lang: str) -> dict:
        s = self._settings
        member = str(args.get("id") or "")
        if method == "lang":
            if lang in ("zh", "th"):
                self._set_lang(lang)
            return {"ok": True}
        if method == "pair":
            code, profile_id = str(args.get("code") or ""), (member or None)
            switch = bool(args.get("switch"))
            return self._job(lambda: s.pair(code, lang, profile_id, switch))
        if method == "detect":
            return self._job(lambda: {"ok": True, "roots": s.detect()})
        if method == "pick_roots":
            picked = self._pick(t("browse_title", normalize(lang)), self._start(s.snapshot().get("express_roots") or []))
            if not picked:
                return {"ok": False, "cancelled": True}
            return self._job(lambda: s.change_roots(picked, lang))
        if method == "rescan":
            return self._job(lambda: s.rescan(lang))
        if method == "pause":
            return self._changed(s.set_paused(member, bool(args.get("paused"))))
        if method == "add_place":
            # 「Express 位置」添加一个盘 / 文件夹（用户 10-24）：只认这里弹的原生选择框，args 里的路径不看
            places = s.snapshot().get("express_places") or s.snapshot().get("express_roots") or []
            picked = self._pick(t("browse_title", normalize(lang)), self._start(places))
            if not picked:
                return {"ok": False, "cancelled": True}
            return self._job(lambda: s.add_place(picked, lang))
        if method == "remove_place":
            path = str(args.get("path") or "")
            return self._job(lambda: s.remove_place(path, lang))
        if method == "remove":
            # 断开连接：先请服务端吊销本机这把密钥（device_leave，要连网），再删成员；做完由 _job 回界面线程重启各线程
            return self._job(lambda: s.disconnect(member))
        if method == "pick_folder":
            # 公司的「选择文件夹…」：args 里的路径一概不看，只认这里弹的原生选择框
            failed = s.pick_ready(self._page_host(), lang)
            if failed is not None:
                return failed
            current = [path for row in s.snapshot().get("members") or [] for path in row.get("folders") or []]
            picked = self._pick(t("folders_pick", normalize(lang)), self._start(current))
            if not picked:
                return {"ok": False, "cancelled": True}
            host = self._page_host()   # 选择框开着时页面可能换了：后台再核一次
            return self._job(lambda: s.pick_folder(picked, host, lang))
        if method == "add_folder":
            # 旧设置页（回退、旧服务端）的「添加文件夹…」：保留，判定口径同公司选文件夹（不许整块盘、系统位置、程序文件夹）
            current = self._folders(member)
            picked = self._pick(t("folders_pick", normalize(lang)), self._start(current))
            if not picked:
                return {"ok": False, "cancelled": True}
            program = program_folder()
            if program and (inside(picked, program) or inside(program, picked)):
                return {"ok": False, "message": t("folders_program", normalize(lang))}
            if plan.too_broad(picked):
                return {"ok": False, "message": t("folders_too_broad", normalize(lang))}
            if picked in current:
                return {"ok": True}
            return self._changed(s.set_folders(member, current + [picked], lang))
        if method == "remove_folder":
            path = str(args.get("path") or "")
            return self._changed(s.set_folders(member, [p for p in self._folders(member) if p != path], lang))
        if method == "autostart":
            return self._changed(s.set_autostart(bool(args.get("enabled"))))
        if method == "keep_awake":
            return self._changed(s.set_keep_awake(enabled=bool(args.get("enabled"))))
        if method == "keep_awake_on_battery":
            return self._changed(s.set_keep_awake(on_battery=bool(args.get("enabled"))))
        if method == "update":
            return self._changed(s.update())
        if method == "download_update":
            return s.download()
        if method == "logs":
            return s.open_logs()
        if method == "window":
            # 对话页画的标题栏（同 Claude 桌面端）：最小化 / 最大化 / 关闭 / 拖动 / 缩放（gui_tomo.TomoWindow.window_action）
            window = self._window()
            return window.window_action(str(args.get("action") or ""), args) if window is not None else {"ok": False}
        return {"ok": False}

    @staticmethod
    def _start(current: list) -> str:
        return picker_start(current, QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation),
                            program_folder())

    def _page_host(self) -> str:
        try:
            return (urlsplit(str(self._page_url() or "")).hostname or "").lower()
        except ValueError:
            return ""

    def _folders(self, member: str) -> list:
        row = next((m for m in self._settings.snapshot()["members"] if m["id"] == member), None)
        return list((row or {}).get("folders") or [])

    def _changed(self, result: dict) -> dict:
        self.changed.emit()
        return result

    def _job(self, work) -> dict:
        job = uuid.uuid4().hex

        def finished(result) -> None:   # 回到界面线程
            result = dict(result or {"ok": False})
            if result.pop("profiles_changed", False):
                try:
                    self._settings.profiles_changed()
                except Exception:
                    log.exception("重启取任务线程失败")
            self.done.emit(job, json.dumps(result, ensure_ascii=False))
            self.changed.emit()

        def failed(exc) -> None:
            log.error("本机设置后台任务失败：%r", exc)
            self.done.emit(job, json.dumps({"ok": False}))

        self._probes.spawn(job, work, finished, failed)
        return {"job": job}

    def shutdown(self) -> None:
        self._probes.shutdown()
