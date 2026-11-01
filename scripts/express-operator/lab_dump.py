# -*- coding: utf-8 -*-
"""实机看样子（开发工具，只读）：把 Express 现在开着的每个窗口和里面的控件（类名、文字、位置、控件号）记下来，
给「授权一次自动登录」「公司列表选公司」写配方用（评估 §16）。不打字、不点、不发任何会改变 Express 的消息；
密码框只记类名和位置，不读内容（WM_GETTEXT 对密码框本来也读不到）。

    python lab_dump.py --src C:\\XpPilot\\companion_src --out C:\\XpPilot\\lab\\dump-login.json
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    sys.path.insert(0, args.src)
    from companion.express_operator.win32_desk import HWND, WindowsDesk, user32

    desk = WindowsDesk()
    LVM_GETITEMCOUNT, ES_PASSWORD, GWL_STYLE = 0x1004, 0x20, -16
    user32.GetDlgCtrlID.argtypes = [HWND]
    user32.GetWindowLongW.argtypes = [HWND, ctypes_int()]
    out = []
    for w in desk.windows():
        hwnd = HWND(w["hwnd"])
        children = []
        for ch in desk._children(hwnd):
            cls = desk._cls(ch)
            style = user32.GetWindowLongW(ch, GWL_STYLE)
            secret = cls == "Edit" and bool(style & ES_PASSWORD)
            row = {"cls": cls, "id": int(user32.GetDlgCtrlID(ch)), "rect": desk._rect(ch),
                   "visible": bool(user32.IsWindowVisible(ch)), "password": secret,
                   "text": "" if secret else desk._gettext(ch)[:120]}
            if cls == "SysListView32":
                row["items"] = desk._send_message(ch, LVM_GETITEMCOUNT, 0, 0)
            children.append(row)
        out.append({"title": w["title"], "cls": w["cls"], "rect": w["rect"], "owned": w["owned"], "children": children})
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return 0


def ctypes_int():
    import ctypes

    return ctypes.c_int


if __name__ == "__main__":
    sys.exit(main())
