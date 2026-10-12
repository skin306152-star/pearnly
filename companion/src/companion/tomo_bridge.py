# -*- coding: utf-8 -*-
"""Tomo 窗口里对话页和本机之间的桥（QWebChannel，对象名 tomoDesktop）。

对话页的设置（「此电脑」：连接、系统）经这里读写本机设置（desktop_settings.py）；文件夹选择框由这里弹本机原生的。
只在 Tomo 自己的对话页（*.pearnly.com/assistant）上应答，登录页等其他页面调用一律拒绝。

协议（网页侧见 frontend/assistant/src/lib/desktop.ts）：
- state() → 当前设置的 JSON；本机状态变了发 changed()，网页再取一次。
- call(method, args_json) → 结果 JSON；慢的操作（配对、换 Express 文件夹、找 Express）先回 {"job": id}，
  做完发 done(id, 结果 JSON)。
"""
from __future__ import annotations

import json
import uuid
from typing import Callable

from PySide6.QtCore import QObject, QStandardPaths, Signal, Slot
from PySide6.QtWidgets import QFileDialog

from .desktop_settings import DesktopSettings, inside, picker_start, program_folder
from .gui_pairing_support import Probes
from .i18n import normalize, t
from .logger import get_logger

log = get_logger(__name__)

REFUSED = json.dumps({"ok": False, "refused": True})


class TomoBridge(QObject):
    changed = Signal()
    done = Signal(str, str)
    open_settings = Signal(str)   # 托盘「连接与设置」：让网页打开设置的某一栏

    def __init__(self, settings: DesktopSettings, *, allowed: Callable[[], bool], window: Callable[[], object],
                 set_lang: Callable[[str], None]) -> None:
        super().__init__()
        self._settings = settings
        self._allowed = allowed
        self._window = window
        self._set_lang = set_lang
        self._probes = Probes()

    @Slot(result=str)
    def state(self) -> str:
        if not self._allowed():
            return REFUSED
        try:
            # window.frameless：本版窗口没有系统标题栏，对话页要自己画（旧版桌面端没有这一项，页面就不画，免得两层标题栏）
            return json.dumps({**self._settings.snapshot(), "window": {"frameless": True}}, ensure_ascii=False)
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
            picked = QFileDialog.getExistingDirectory(self._window(), t("browse_title", normalize(lang)),
                                                      self._start(s.snapshot().get("express_roots") or []))
            if not picked:
                return {"ok": False, "cancelled": True}
            return self._job(lambda: s.change_roots(picked, lang))
        if method == "rescan":
            return self._job(lambda: s.rescan(lang))
        if method == "pause":
            return self._changed(s.set_paused(member, bool(args.get("paused"))))
        if method == "remove":
            return self._changed(s.remove(member))
        if method == "add_folder":
            current = self._folders(member)
            picked = QFileDialog.getExistingDirectory(self._window(), t("folders_pick", normalize(lang)),
                                                      self._start(current))
            if not picked:
                return {"ok": False, "cancelled": True}
            if program_folder() and inside(picked, program_folder()):
                return {"ok": False, "message": t("folders_program", normalize(lang))}
            if picked in current:
                return {"ok": True}
            return self._changed(s.set_folders(member, current + [picked], lang))
        if method == "remove_folder":
            path = str(args.get("path") or "")
            return self._changed(s.set_folders(member, [p for p in self._folders(member) if p != path], lang))
        if method == "autostart":
            return self._changed(s.set_autostart(bool(args.get("enabled"))))
        if method == "always_on":
            return self._changed(s.set_always_on(bool(args.get("enabled"))))
        if method == "update":
            return self._changed(s.update())
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
