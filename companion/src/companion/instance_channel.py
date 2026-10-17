# -*- coding: utf-8 -*-
"""已在运行时再打开一次（双击桌面 / 开始菜单的 Tomo）：通知正在跑的那个把 Tomo 窗口拿到前面，自己退出。

单实例本身仍由 single_instance.py 的命名互斥量判定；这里只是本机同一用户的一条本地管道（QLocalServer），
只收固定的一个词，不带任何数据。
"""
from __future__ import annotations

import getpass

from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .logger import get_logger

log = get_logger(__name__)

SHOW = b"show"


def _name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return "PearnlyTomo-" + "".join(c for c in user if c.isalnum())[:40]


def listen(on_show) -> QLocalServer | None:
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.UserAccessOption)
    if not server.listen(_name()):
        QLocalServer.removeServer(_name())   # 上次异常退出留下的
        if not server.listen(_name()):
            log.warning("本地管道启动失败：%s", server.errorString())
            return None

    def accept() -> None:
        while server.hasPendingConnections():
            sock = server.nextPendingConnection()

            def read(s=sock) -> None:
                if s.readAll().data().strip() == SHOW:
                    on_show()
                s.disconnectFromServer()

            sock.readyRead.connect(read)
            sock.disconnected.connect(sock.deleteLater)

    server.newConnection.connect(accept)
    return server


def _allow_any_foreground() -> None:
    """再点一次桌面图标的这个进程是前台进程：把「拿到最前面」的权利放出去，托盘再交给 CC 窗口进程（window_link）。"""
    import sys

    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(-1)   # ASFW_ANY
    except Exception:
        pass


def ask_running_to_show() -> bool:
    """告诉正在跑的那个显示 Tomo；成功返回 True（本进程随即退出），连不上返回 False。"""
    _allow_any_foreground()
    sock = QLocalSocket()
    sock.connectToServer(_name())
    if not sock.waitForConnected(1500):
        return False
    sock.write(SHOW + b"\n")
    ok = sock.waitForBytesWritten(1500)
    sock.disconnectFromServer()
    return ok
