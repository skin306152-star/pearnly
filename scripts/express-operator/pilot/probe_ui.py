"""第 2 层实测：不截图，能从 Express（VFP 程序）读到什么。由执行代理的 ``{"do": "probe"}`` 调用，在 Express 的桌面会话里跑。

对前台的 Express 窗口分别用三种办法列控件树，记每种读到的控件数、带文字的控件、耗时：
- ``raw``：原生 Win32（EnumChildWindows + WM_GETTEXT），同 agent_win.windows；
- ``win32``：pywinauto win32 后端；
- ``uia``：pywinauto uia 后端（UI Automation；VFP 的格子如果有 MSAA 支持，只可能从这里露出名字和值）。
另读两种后端的焦点元素。只读，不点、不按键，不改、不注入 Express。
"""
from __future__ import annotations

import time
from typing import Dict, List


def _raw(hwnd: int, limit: int) -> List[Dict]:
    from . import agent_win as A
    out: List[Dict] = []

    def child(ch, _):
        if len(out) < limit:
            out.append({"cls": A._cls(ch), "text": A._gettext(ch)[:200], "rect": A._rect(ch),
                        "visible": bool(A.user32.IsWindowVisible(ch))})
        return True

    A.user32.EnumChildWindows(A.HWND(hwnd), A.EnumProc(child), 0)
    return out


def _tree(backend: str, hwnd: int, depth: int, limit: int) -> List[Dict]:
    from pywinauto import Desktop
    root = Desktop(backend=backend).window(handle=hwnd).wrapper_object()
    out: List[Dict] = []

    def walk(w, level):
        if len(out) >= limit or level > depth:
            return
        info = w.element_info
        r = info.rectangle
        item = {"level": level, "cls": info.class_name or "", "text": (w.window_text() or "")[:200],
                "rect": [r.left, r.top, r.right, r.bottom]}
        if backend == "uia":
            item["type"] = info.control_type or ""
            item["name"] = (info.name or "")[:200]
            try:
                legacy = w.legacy_properties()
                item["value"] = str(legacy.get("Value") or "")[:200]
                item["role"] = legacy.get("Role")
            except Exception:  # noqa: BLE001
                pass
        out.append(item)
        try:
            for ch in w.children():
                walk(ch, level + 1)
        except Exception:  # noqa: BLE001
            pass

    walk(root, 0)
    return out


def dump(depth: int = 6, limit: int = 400) -> Dict:
    from . import agent_win as A
    fg = A.foreground()
    result: Dict = {"fg": fg, "focus_win32": A._focus_info(), "focus_uia": A._uia_focus(), "trees": {}}
    if fg["exe"].lower() != A.PROCESS:
        result["error"] = "前台不是 Express"
        return result
    for name, fn in (("raw", lambda: _raw(fg["hwnd"], limit)),
                     ("win32", lambda: _tree("win32", fg["hwnd"], depth, limit)),
                     ("uia", lambda: _tree("uia", fg["hwnd"], depth, limit))):
        started = time.perf_counter()
        try:
            items = fn()
            result["trees"][name] = {"ms": int((time.perf_counter() - started) * 1000), "count": len(items),
                                     "with_text": sum(1 for i in items if i.get("text") or i.get("name") or i.get("value")),
                                     "items": items}
        except Exception as e:  # noqa: BLE001
            result["trees"][name] = {"error": f"{type(e).__name__}: {e}"[:300]}
    return result
