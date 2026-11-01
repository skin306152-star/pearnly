# -*- coding: utf-8 -*-
"""操作 Express 的统一接口：引擎只认「一批动作 → 每个动作的结果 + 当前 Express 窗口清单」。

Windows 上的实现是 ``win32_desk.WindowsDesk``；测试里用假 Express（同一个接口）。一批动作按顺序做、遇错即停，
后面的回 ``skipped``。每次按键 / 点击前实现方都要核前台是 Express、落点在 Express 窗口上，否则回 ``refused``——
这是写死在实现里的守卫，不靠调用方自觉。

动作（``{"do": ...}``）：
- 窗口：``activate``、``maximize_main``、``us_layout``、``state``（前台、Express 顶层窗口和文字、焦点格）、``windows``；
- 键盘：``keys``（SendKeys 子集，如 ``{HOME}+{END}``、``{ENTER}``）、``key``（组合键，如 ``ctrl+F4``、``alt``）、
  ``type``（英文数字直接打，泰文走剪贴板 + Shift+Insert）、``paste``；
- 表单：``command``（工具栏命令号，灰的拒绝）、``toolbar``（各命令亮不亮）、``fields``（当前单据窗口的输入格和左边标签）、
  ``grid_click``（点明细表格某一格）、``dialog_button``（按弹窗里某个文字的按钮）；
- 鼠标（只给 AI 接手用）：``click``、``drag``、``scroll``、``move``、``mouse``、``cursor``；
- 画面：``screenshot``（缩到给定大小的 PNG，base64）、``zoom``；
- 其他：``wait``、``close_express``。
"""
from __future__ import annotations

from typing import Dict, List, Protocol


# CC 自己用 SendInput 发的每个键盘、鼠标事件都在 dwExtraInfo 打这个签名；「有人在用」的键鼠钩子只忽略带它的（主会话 10-30）
CC_INPUT_MARK = 0x50524E4C  # "PRNL"


class Refused(Exception):
    """守卫拒绝：前台不是 Express、落点不在 Express、命令是灰的、弹窗里没有那个按钮等。"""


class Desk(Protocol):
    def act(self, actions: List[Dict], windows: bool = True) -> Dict:
        """回 ``{"results": [{"ok": bool, ...}], "windows": [...], "fg": {...}}``。``windows=False`` 时不带清单（省时）。"""
        ...


def run(do, actions: List[Dict]) -> List[Dict]:
    """按顺序执行、遇错即停的共用写法（实现方各自给 ``do(action) -> dict``）。"""
    results, failed = [], False
    for action in actions:
        if failed:
            results.append({"ok": False, "skipped": True})
            continue
        try:
            results.append({"ok": True, **(do(action) or {})})
        except Refused as error:
            failed = True
            results.append({"ok": False, "error": str(error), "refused": True})
        except Exception as error:  # noqa: BLE001 — 回给引擎，由它决定接手还是停下
            failed = True
            results.append({"ok": False, "error": f"{type(error).__name__}: {error}"})
    return results
