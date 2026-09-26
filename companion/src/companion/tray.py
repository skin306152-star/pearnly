"""系统托盘 stub(Phase 2.5 完整实现 + GUI 测试)

Phase 2 目前只暴露 API 契约,GUI 在 P2.5 校准 UI 一起做(避免 PySide6 装两次)

设计要点(留给 P2.5):
  - QSystemTrayIcon 主图标(绿色=空闲 / 黄=工作 / 红=错误)
  - 右键菜单:状态摘要 / 强制重新校准 / 调试模式开关 / 退出
  - 双击 = 弹推送日志窗口(列表 last 50 任务,从 /api/companion/items 拉)
  - 主线程 = Qt 事件循环;后台线程 = QueueClient poll + 字段输入器
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class TrayState:
    status: str = "idle"  # idle / working / error / calibrating
    message: str = ""
    last_task_id: str | None = None


class TrayController:
    """托盘控制器契约(Phase 2.5 实现 PySide6 版)"""

    def __init__(self) -> None:
        self._state = TrayState()
        self._listeners: list[Callable[[TrayState], None]] = []

    @property
    def state(self) -> TrayState:
        return self._state

    def set_status(self, status: str, message: str = "") -> None:
        self._state.status = status
        self._state.message = message
        for cb in self._listeners:
            cb(self._state)

    def on_state_change(self, cb: Callable[[TrayState], None]) -> None:
        self._listeners.append(cb)

    # Phase 2.5 将实现:
    #   def show_recalibrate(self) -> None: ...
    #   def show_logs(self) -> None: ...
    #   def quit(self) -> None: ...
