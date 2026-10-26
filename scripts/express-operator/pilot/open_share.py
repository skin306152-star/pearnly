"""桌面快捷方式「1-打开服务器文件夹」：请 Windows 自己弹出网络凭据框，连上服务器后打开 Express 所在文件夹。

用户在系统自己的框里输服务器用户名和密码（可勾选记住）；本脚本只调 WNetAddConnection3（交互 + 提示），
不读、不存、不代填任何凭据，也拿不到用户输入的内容。用 pythonw 跑，不开控制台窗口。
（从计划任务或带参数启动资源管理器时，Windows 遇到要凭据的服务器会静默退回「文档」，不弹框，所以不用那种办法。）
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes as W

SHARE = r"\\Accserver\d$"
FOLDER = r"\\Accserver\d$\ACCOUNT\70EXP"
CONNECT_INTERACTIVE, CONNECT_PROMPT = 0x08, 0x10


class NETRESOURCE(ctypes.Structure):
    _fields_ = [("dwScope", W.DWORD), ("dwType", W.DWORD), ("dwDisplayType", W.DWORD), ("dwUsage", W.DWORD),
                ("lpLocalName", W.LPWSTR), ("lpRemoteName", W.LPWSTR), ("lpComment", W.LPWSTR),
                ("lpProvider", W.LPWSTR)]


def main() -> int:
    if os.path.isdir(FOLDER):  # 已经连着（记住过凭据）：直接打开
        os.startfile(FOLDER)
        return 0
    mpr = ctypes.WinDLL("mpr")
    res = NETRESOURCE(dwType=1, lpRemoteName=SHARE)  # RESOURCETYPE_DISK
    code = mpr.WNetAddConnection3W(None, ctypes.byref(res), None, None, CONNECT_INTERACTIVE | CONNECT_PROMPT)
    if code == 0 and os.path.isdir(FOLDER):
        os.startfile(FOLDER)
        return 0
    if code != 1223:  # 1223 = 用户点了取消
        ctypes.windll.user32.MessageBoxW(None, f"没连上服务器（{code}），请再点一次。", "服务器文件夹", 0x30)
    return 1


if __name__ == "__main__":
    sys.exit(main())
