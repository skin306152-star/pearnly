"""「Express 登录授权一次、以后 CC 自动登录」的雏形（只在 Windows 测试机上用，演示用，不是产品代码）。

链路只有「CC 存、CC 填」：
1. 用户本人在这台电脑上运行 ``pythonw -m pilot.cc_login save``，弹一个小窗，自己输一次 Express 用户名和密码；
   存进 Windows 凭据管理器（普通凭据，CRED_PERSIST_LOCAL_MACHINE：只在这台电脑、只给这个 Windows 用户，
   由系统用 DPAPI 加密）。不写文件、不传服务器、不进日志。
2. 之后执行代理收到 ``{"do": "login"}``：确认前台是白名单里的 ExpressI 且是登录窗口，从凭据管理器取出来直接打进登录框，
   回 ``{"ok": true}`` 或失败原因。调用方（包括 Claude）拿不到用户名和密码。
3. ``forget`` 删掉这条凭据。

登录窗口的样子（标题、要不要先点用户名格）写在工作目录的 login.json，第一次由人对着画面定一次：
``{"title_contains": "...", "user_click": [x, y] 或 null, "after_ms": 3000}``（坐标相对登录窗口左上角）。
"""
from __future__ import annotations

import ctypes
import json
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

DEFAULT_TARGET = "Pearnly CC/Express/accserver"
CONFIG = Path(__file__).resolve().parent.parent / "login.json"
CRED_TYPE_GENERIC, CRED_PERSIST_LOCAL_MACHINE = 1, 2

if sys.platform == "win32":
    from ctypes import wintypes as W

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", W.DWORD), ("Type", W.DWORD), ("TargetName", W.LPWSTR), ("Comment", W.LPWSTR),
                    ("LastWritten", W.FILETIME), ("CredentialBlobSize", W.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", W.DWORD),
                    ("AttributeCount", W.DWORD), ("Attributes", ctypes.c_void_p), ("TargetAlias", W.LPWSTR),
                    ("UserName", W.LPWSTR)]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), W.DWORD]
    advapi32.CredReadW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    advapi32.CredDeleteW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD]
    advapi32.CredFree.argtypes = [ctypes.c_void_p]


def store(user: str, password: str, target: str = DEFAULT_TARGET) -> None:
    blob = password.encode("utf-16-le")
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = CREDENTIAL(Type=CRED_TYPE_GENERIC, TargetName=target, Comment="Pearnly CC：Express 自动登录（试点）",
                      CredentialBlobSize=len(blob), CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)),
                      Persist=CRED_PERSIST_LOCAL_MACHINE, UserName=user)
    ok = advapi32.CredWriteW(ctypes.byref(cred), 0)
    ctypes.memset(buf, 0, len(blob))
    if not ok:
        raise OSError(ctypes.get_last_error(), "CredWriteW 失败")


def _read(target: str) -> Optional[Tuple[str, str]]:
    ptr = ctypes.POINTER(CREDENTIAL)()
    if not advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
        return None
    try:
        c = ptr.contents
        raw = ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize)
        return c.UserName or "", raw.decode("utf-16-le")
    finally:
        advapi32.CredFree(ptr)


def exists(target: str = DEFAULT_TARGET) -> bool:
    found = _read(target)
    return found is not None


def forget(target: str = DEFAULT_TARGET) -> bool:
    return bool(advapi32.CredDeleteW(target, CRED_TYPE_GENERIC, 0))


def _config() -> Dict:
    if CONFIG.exists():
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    return {"title_contains": "", "user_click": None, "after_ms": 3000}


def fill(target: str = DEFAULT_TARGET) -> Dict:
    """把存好的凭据填进 Express 登录框。只回成败，不回、不记任何凭据内容。"""
    from . import agent_win as A

    cfg = _config()
    if not cfg.get("title_contains"):
        return {"ok": False, "why": "login.json 还没定登录窗口标题"}
    A.guard()
    fg = A.foreground()
    if cfg["title_contains"] not in fg["title"]:
        return {"ok": False, "why": "前台不是 Express 登录窗口"}
    found = _read(target)
    if found is None:
        return {"ok": False, "why": "这台电脑还没存 Express 登录"}
    user, password = found
    try:
        if any(ord(ch) > 126 for ch in user + password):
            return {"ok": False, "why": "用户名或密码含非英文字符，雏形暂不支持"}
        if cfg.get("user_click"):
            left, top = A._rect(A.HWND(fg["hwnd"]))[:2]
            A.click(left + cfg["user_click"][0], top + cfg["user_click"][1])
        A.ensure_us_layout()
        A.type_ascii(user)
        A._tap(A.VK["TAB"])
        A.type_ascii(password)
        A._tap(A.VK["ENTER"])
    finally:
        password = user = ""  # noqa: F841 — 尽早丢掉引用
    time.sleep(cfg.get("after_ms", 3000) / 1000)
    still = cfg["title_contains"] in A.foreground()["title"]
    A.log("login " + ("failed" if still else "ok"))
    return {"ok": not still, "why": "登录窗口还在（用户名或密码不对？）" if still else ""}


def _save_dialog(target: str) -> int:
    """用户本人在这台电脑上输一次。窗口文字只写完成操作必需的字。"""
    import tkinter as tk

    root = tk.Tk()
    root.title("Express 登录")
    root.resizable(False, False)
    tk.Label(root, text="用户名").grid(row=0, column=0, padx=8, pady=6, sticky="e")
    tk.Label(root, text="密码").grid(row=1, column=0, padx=8, pady=6, sticky="e")
    user = tk.Entry(root, width=28)
    pwd = tk.Entry(root, width=28, show="•")
    user.grid(row=0, column=1, padx=8, pady=6)
    pwd.grid(row=1, column=1, padx=8, pady=6)
    result = {"code": 1}

    def save():
        if user.get() and pwd.get():
            store(user.get(), pwd.get(), target)
            result["code"] = 0
        pwd.delete(0, "end")
        root.destroy()

    tk.Button(root, text="保存", width=10, command=save).grid(row=2, column=1, padx=8, pady=8, sticky="e")
    user.focus_set()
    root.bind("<Return>", lambda _e: save())
    root.mainloop()
    return result["code"]


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else ""
    target = args[1] if len(args) > 1 else DEFAULT_TARGET
    if cmd == "save":
        return _save_dialog(target)
    if cmd == "forget":
        return 0 if forget(target) else 1
    if cmd == "status":  # 只说有没有存，不显示内容
        print("已存" if exists(target) else "未存")
        return 0
    print("用法：python -m pilot.cc_login save | status | forget")
    return 2


if __name__ == "__main__":
    sys.exit(main())
