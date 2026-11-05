# -*- coding: utf-8 -*-
"""后台做法的 ``Desk``：不碰真键盘、真鼠标，直接给 Express 的窗口发消息（10-26 用户：操作 Express 时不和人抢鼠标键盘）。

Express 不用在最前面，人照常用这台电脑；锁屏也不影响（发消息不经过输入桌面）。做法：
- 打字：给 Express 当前的焦点格发 ``WM_CHAR``（同步），不走剪贴板；泰文先把 Express 的界面线程切到泰文输入法再发 Unicode
  （系统按收字线程的输入法代码页换成 ANSI，默认输入法是中文时泰文会变「?」，10-28 测试机）；
- 全选：编辑格发 ``EM_SETSEL``、下拉框发 ``CB_SETEDITSEL``（代替 Home、Shift+End）；
- 回车走格：给焦点格投递 ``WM_KEYDOWN`` / ``WM_KEYUP``（投递才会经过 Express 自己的消息循环，回车跳格由它处理）；
- 菜单：读菜单树，按编号（「&1.」「4.」……）或文字找到命令号（``menu.resolve``），给主窗口发 ``WM_COMMAND``；关单据窗口发 ``SC_CLOSE``（同 Ctrl+F4）；
- 明细表格：给表格控件投递鼠标按下 / 抬起（表格里的坐标），不动真鼠标；
- 焦点和「前台」都按 Express 自己的界面线程读（``GetGUIThreadInfo``），不看整台电脑的前台；别的程序抢了前台后 Express
  线程没有焦点格（10-30 实机），给它最后激活的顶层窗口发 ``WM_ACTIVATE``，让它自己放回焦点（不把 Express 拉到前面）；
- 截图：``PrintWindow`` 只画 Express 主窗口，被挡住也能截，不截别的窗口。
哪一步在真 Express 上不认后台消息，由实机实验记下来，只在那一步另想办法。
"""
from __future__ import annotations

import base64
import ctypes
import time
from typing import Dict, List, Optional

from .desk import Refused
from .win32_desk import IS_WIN, SHOT_SIZE, TH_LAYOUT, VK, WindowsDesk, button_command, png

if IS_WIN:
    from ctypes import wintypes as W

    from .win32_desk import GUITHREADINFO, HWND, gdi32, user32

    user32.GetLastActivePopup.argtypes = [HWND]
    user32.GetLastActivePopup.restype = HWND
    user32.ScreenToClient.argtypes = [HWND, ctypes.POINTER(W.POINT)]
    user32.IsWindowEnabled.argtypes = [HWND]
    user32.PrintWindow.argtypes = [HWND, W.HDC, W.UINT]
    user32.SendMessageW.argtypes = [HWND, W.UINT, W.WPARAM, W.LPARAM]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.SendMessageA.argtypes = [HWND, W.UINT, W.WPARAM, W.LPARAM]
    user32.SendMessageA.restype = ctypes.c_ssize_t
    user32.GetKeyboardLayout.argtypes = [W.DWORD]
    user32.GetKeyboardLayout.restype = W.HKL

WM_KEYDOWN, WM_KEYUP, WM_CHAR, WM_COMMAND, WM_SYSCOMMAND = 0x0100, 0x0101, 0x0102, 0x0111, 0x0112
WM_ACTIVATE, WA_ACTIVE = 0x0006, 1
WM_INPUTLANGCHANGEREQUEST, LANG_THAI = 0x0050, 0x041E
WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON, SC_CLOSE = 0x0201, 0x0202, 0x0001, 0xF060
EM_SETSEL, CB_SETEDITSEL = 0x00B1, 0x0142
SCAN = {0x0D: 0x1C, 0x09: 0x0F, 0x24: 0x47, 0x23: 0x4F, 0x26: 0x48, 0x28: 0x50, 0x25: 0x4B, 0x27: 0x4D}


def _lang(hkl) -> int:
    return int(hkl or 0) & 0xFFFF


def _key_lparam(vk: int, up: bool) -> int:
    value = 1 | (SCAN.get(vk, 0) << 16)
    if vk in (0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E):
        value |= 1 << 24  # 扩展键
    if up:
        value |= (1 << 30) | (1 << 31)
    return value



SEND_TIMEOUT_MS = 2000  # 同步发给 Express 的消息最多等这么久


def _send(hwnd, msg: int, wparam: int, lparam: int, *, ansi: bool = False) -> bool:
    """同步发消息、有时限（SMTO_ABORTIFHUNG）。Express 停在模态框里时直接 SendMessage 可能一直不回（11-01 录制机：放回焦点时
    给报错框「Message#. 13」发 WM_ACTIVATE，录单线程卡死 24 分钟，点停止也停不下）。回是否送到、处理完。"""
    res = ctypes.c_size_t()
    send = user32.SendMessageTimeoutA if ansi else user32.SendMessageTimeoutW
    return bool(send(hwnd, msg, wparam, lparam, 0x2, SEND_TIMEOUT_MS, ctypes.byref(res)))

class BackgroundDesk(WindowsDesk):
    background = True  # 引擎据此告诉 AI 接手：这台只能键盘与 express_menu（鼠标动作一律被拒）

    def __init__(self, express_exe: str = ""):
        super().__init__(express_exe)
        self.sent = 0  # 发出的消息数（汇报里看速度用）
        self._last_focus = 0  # 上一次读到的当前格（排查用）

    # ── Express 自己的界面线程 ──────────────────────────────────────────────
    def _main_hwnd(self):
        main = self.main_window()
        if not main:
            raise Refused("no Express window")
        return HWND(main["hwnd"])

    def _thread_info(self):
        tid = user32.GetWindowThreadProcessId(self._main_hwnd(), None)
        info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
        if not user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
            raise Refused("cannot read Express focus")
        return info

    def guard(self) -> None:
        """后台做法不要求 Express 在最前面，只要求它在。"""
        self._main_hwnd()

    def guard_point(self, x: int, y: int) -> None:
        raise Refused("background mode does not move the real mouse")

    def foreground(self) -> Dict:
        """「前台」= Express 自己当前激活的窗口（主窗口或它的弹窗），不是整台电脑的前台。"""
        info = self._thread_info()
        hwnd = info.hwndActive or self._main_hwnd().value
        return {"hwnd": int(hwnd or 0), "exe": "ExpressI.exe", "title": self._title(HWND(hwnd))}

    def focus_info(self) -> Dict:
        try:
            focus = self._focus()
        except Refused:
            focus = None
        return {"hwnd": int(focus or 0), "cls": self._cls(focus) if focus else "",
                "text": self._gettext(focus) if focus else "", "rect": self._rect(focus) if focus else None}

    def _focus(self):
        info = self._thread_info()
        if info.hwndFocus:
            self._last_focus = int(info.hwndFocus)
            return info.hwndFocus
        focus = self._restore_focus(info)
        if not focus:
            raise Refused("Express has no focused field")
        return focus

    def _restore_focus(self, info):
        """别的程序抢了前台：Express 线程这时没有激活窗口、也没有焦点格。给它最后激活的那个顶层窗口（弹窗或主框架）发一条
        ``WM_ACTIVATE``，让它在自己的线程里按自己记住的位置放回焦点；不改整台电脑的前台、不把 Express 拉到前面（10-30
        录制机实测：登录窗被记事本抢了前台后，这一条消息就把焦点放回了原来的输入格，前台仍是记事本；挂输入状态再
        ``SetFocus`` 在 Express 不在前台时无效）。放回来的不是格子（只有主框架本身）回 None，照旧转人工。"""
        if info.hwndActive:
            return None
        main = self._main_hwnd()
        target = user32.GetLastActivePopup(main) or main
        if not user32.IsWindowEnabled(target):
            return None
        if not _send(target, WM_ACTIVATE, WA_ACTIVE, 0):
            return None  # Express 没在时限内处理（停在模态框里）：不再等，照旧转人工、由引擎看弹窗
        self.sent += 1
        again = self._thread_info().hwndFocus
        if not again or int(again) == int(getattr(main, "value", main) or 0):
            return None
        self._last_focus = int(again)
        return again

    def activate(self) -> Dict:
        return {"title": self.foreground()["title"], "background": True}

    def us_layout(self) -> None:
        return None

    # ── 键盘：发消息 ──────────────────────────────────────────────────────
    def _post(self, hwnd, msg: int, wparam: int, lparam: int) -> None:
        user32.PostMessageW(hwnd, msg, wparam, lparam)
        self.sent += 1

    def _press(self, vk: int) -> None:
        focus = self._focus()
        self._post(focus, WM_KEYDOWN, vk, _key_lparam(vk, False))
        self._post(focus, WM_KEYUP, vk, _key_lparam(vk, True))
        time.sleep(0.05)

    def _select_all(self) -> None:
        focus = self._focus()
        if self._cls(focus) == "ComboBox":
            _send(focus, CB_SETEDITSEL, 0, 0xFFFF0000)
        else:
            _send(focus, EM_SETSEL, 0, -1)
        self.sent += 1

    def type_any(self, text: str) -> None:
        """Express 不是 Unicode 程序，字符消息在发字和收字两头都按各自线程当前输入法的代码页换：
        收字线程是英文输入法时发 Unicode 泰文变「?」（10-26 实机），发字线程是中文输入法时发 cp874 字节也变「?」
        （10-28 测试机，泰文字节被当成双字节字的头）。所以有非 ASCII 字时先把 Express 的界面线程切到泰文输入法再发 Unicode；
        切不过去才照旧发 cp874 字节（单字节输入法下来回原样）。"""
        focus = self._focus()
        text.encode("cp874", "strict")  # 泰文以外的字 Express 存不下：照旧拒绝
        if any(ord(c) > 126 for c in text) and self._thai_input(focus):
            for ch in text:
                _send(focus, WM_CHAR, ord(ch), 1)
                self.sent += 1
            return
        for byte in text.encode("cp874", "strict"):
            _send(focus, WM_CHAR, byte, 1, ansi=True)
            self.sent += 1

    def _thai_input(self, focus, wait_s: float = 1.0) -> bool:
        """Express 的界面线程当前是不是泰文输入法；不是就请它切过去（只换 Express 自己这条线程，不动别的程序），等它切好。"""
        tid = user32.GetWindowThreadProcessId(focus, None)
        if _lang(user32.GetKeyboardLayout(tid)) == LANG_THAI:
            return True
        user32.PostMessageW(self._main_hwnd(), WM_INPUTLANGCHANGEREQUEST, 0, self._layout(TH_LAYOUT))
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            time.sleep(0.05)
            if _lang(user32.GetKeyboardLayout(tid)) == LANG_THAI:
                return True
        return False

    def paste(self, text: str) -> None:
        self.type_any(text)  # 后台做法不走剪贴板

    def sendkeys(self, spec: str) -> None:
        if spec.replace(" ", "").upper() == "{HOME}+{END}":
            self._select_all()
            return
        i = 0
        while i < len(spec):
            ch = spec[i]
            if ch in "+^%":
                raise Refused("background mode has no modifier keys")
            if ch == "{":
                end = spec.index("}", i)
                parts = spec[i + 1:end].split(" ")
                i = end + 1
                name, times = parts[0].upper(), int(parts[1]) if len(parts) > 1 else 1
                if name in ("ESC", "ESCAPE"):
                    raise Refused("Esc is not allowed")  # 已有明细时 Esc 会直接存档
                if name not in VK:
                    raise Refused(f"unknown key {name}")
                for _ in range(times):
                    self._press(VK[name])
            else:
                self.type_any(ch)
                i += 1

    def key_combo(self, text: str, repeat: int = 1) -> None:
        name = text.replace(" ", "").lower()
        if name == "alt":
            return
        if name == "ctrl+f4":
            self.close_form()
            return
        if "+" in name:
            raise Refused("background mode has no modifier keys")
        for _ in range(max(1, min(int(repeat), 100))):
            self.sendkeys("{" + name.upper() + "}" if len(name) > 1 else name)

    # ── 菜单与单据窗口 ────────────────────────────────────────────────────
    def menu(self, path: List[str], name: str = "") -> Dict:
        """按菜单树找命令号（``menu.resolve``：编号 → 文字 → 子菜单里唯一的命令；多个候选抛 ``MenuAmbiguous``），给主窗口发
        ``WM_COMMAND``。读菜单在 ``WindowsDesk``（两种做法共用）。"""
        cmd = self.menu_command([str(k) for k in path], name)
        self._post(self._main_hwnd(), WM_COMMAND, cmd, 0)
        return {"cmd": cmd}

    def close_form(self) -> None:
        form = self.active_form()
        if int(form.value if hasattr(form, "value") else form) == int(self._main_hwnd().value):
            return
        self._post(form, WM_SYSCOMMAND, SC_CLOSE, 0)

    def dialog_button(self, text: str) -> Dict:
        found = []
        for w in self.windows():
            if w["cls"] != "#32770" or not user32.IsWindowEnabled(HWND(w["hwnd"])):
                continue  # 叠着几个弹窗时，下面那个是灰的：只按能用的那个
            found += [ch for ch in self._children(HWND(w["hwnd"])) if self._cls(ch) == "Button"
                      and user32.IsWindowVisible(ch) and self._gettext(ch).replace("&", "").strip() == text]
        if len(found) != 1:
            raise Refused(f"{len(found)} buttons named {text!r}")
        dialog, wparam, lparam = button_command(found[0])
        self._post(dialog, WM_COMMAND, wparam, lparam)
        return {"pressed": text}

    def grid_click(self, offset=(112, 30)) -> Dict:
        grids = [ch for ch in self._children(self.active_form())
                 if user32.IsWindowVisible(ch) and self._cls(ch) == "MFCGridCtrl"]
        if not grids:
            raise Refused("no grid")
        grid = max(grids, key=lambda h: (lambda r: (r[2] - r[0]) * (r[3] - r[1]))(self._rect(h)))
        scale = user32.GetDpiForSystem() / 96
        x, y = int(offset[0] * scale), int(offset[1] * scale)
        lparam = (y << 16) | (x & 0xFFFF)
        self._post(grid, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
        self._post(grid, WM_LBUTTONUP, 0, lparam)
        return {"xy": [x, y], "background": True}

    def click(self, x: int, y: int, button: str = "left", count: int = 1) -> None:
        raise Refused("background mode does not move the real mouse")

    # ── 截图：只画 Express 主窗口 ──────────────────────────────────────────
    def screenshot(self, size=SHOT_SIZE) -> Dict:
        main = self._main_hwnd()
        left, top, right, bottom = self._rect(main)
        w, h = right - left, bottom - top
        src = user32.GetDC(None)
        mem = gdi32.CreateCompatibleDC(src)
        bmp = gdi32.CreateCompatibleBitmap(src, w, h)
        old = gdi32.SelectObject(mem, bmp)
        try:
            user32.PrintWindow(main, mem, 2)  # PW_RENDERFULLCONTENT：被挡住也照画
            out_w, out_h = (int(size[0]), int(size[1])) if size else (w, h)
            small = gdi32.CreateCompatibleDC(src)
            sbmp = gdi32.CreateCompatibleBitmap(src, out_w, out_h)
            sold = gdi32.SelectObject(small, sbmp)
            gdi32.SetStretchBltMode(small, 4)
            gdi32.SetBrushOrgEx(small, 0, 0, None)
            gdi32.StretchBlt(small, 0, 0, out_w, out_h, mem, 0, 0, w, h, 0x00CC0020)
            stride = (out_w * 3 + 3) & ~3
            import struct
            info = ctypes.create_string_buffer(struct.pack("<IiiHHIIiiII", 40, out_w, -out_h, 1, 24, 0, 0, 0, 0, 0, 0), 44)
            pixels = ctypes.create_string_buffer(stride * out_h)
            gdi32.GetDIBits(small, sbmp, 0, out_h, pixels, info, 0)
            gdi32.SelectObject(small, sold)
            gdi32.DeleteObject(sbmp)
            gdi32.DeleteDC(small)
        finally:
            gdi32.SelectObject(mem, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(None, src)
        data = pixels.raw
        rows = []
        for y in range(out_h):
            bgr = data[y * stride: y * stride + out_w * 3]
            rgb = bytearray(len(bgr))
            rgb[0::3], rgb[1::3], rgb[2::3] = bgr[2::3], bgr[1::3], bgr[0::3]
            rows.append(bytes(rgb))
        return {"png": base64.b64encode(png(out_w, out_h, rows)).decode(), "screen": [w, h], "size": [out_w, out_h]}

    # ── 等 Express 处理完，不死等 ──────────────────────────────────────────
    def settle(self, max_ms: int) -> float:
        """等 Express 把刚投递的消息处理完就走，最多等 ``max_ms``（流程里原来写死的等待时长当上限）。

        做法：先让出几十毫秒，让 Express 的界面线程取走刚投递的消息；再给它发一个空消息（WM_NULL，同步）——它正忙着
        （查对方、存档、开单据窗口）时这个空消息要等它忙完才回；连续两次很快就回、焦点和弹窗也没再变，就算处理完了。
        Express 卡住（网络盘慢）时最多等到上限，和原来一样。"""
        started = time.monotonic()
        deadline = started + max_ms / 1000
        main = self._main_hwnd()
        last, quiet = None, 0
        time.sleep(0.03)
        while time.monotonic() < deadline:
            left = max(1, int((deadline - time.monotonic()) * 1000))
            t0 = time.monotonic()
            self._send_message(main, 0x0000, 0, 0, left)  # WM_NULL：Express 忙完才回
            busy = time.monotonic() - t0 > 0.02
            info = self._thread_info()
            now = (int(info.hwndFocus or 0), int(info.hwndActive or 0), len(self.windows()))
            quiet = quiet + 1 if (now == last and not busy) else 0
            if quiet >= 2:
                break
            last = now
            time.sleep(0.03)
        return time.monotonic() - started

    def do(self, a: Dict) -> Dict:
        op = a.get("do")
        if op == "wait":
            return {"waited": round(self.settle(min(int(a.get("ms", 500)), 30000)), 3)}
        if op == "menu":
            return self.menu(a["path"], str(a.get("name") or ""))
        if op == "close_form":
            self.close_form()
            return {}
        if op in ("click", "drag", "scroll", "move", "mouse"):
            raise Refused("background mode does not move the real mouse")
        return super().do(a)


def make(express_exe: str = "", background: bool = True) -> Optional[WindowsDesk]:
    if not IS_WIN:
        return None
    return BackgroundDesk(express_exe) if background else WindowsDesk(express_exe)
