# -*- coding: utf-8 -*-
"""操作 Express 的统一接口：引擎只认「一批动作 → 每个动作的结果 + 当前 Express 窗口清单」。

Windows 上的实现是 ``win32_desk.WindowsDesk``；测试里用假 Express（同一个接口）。一批动作按顺序做、遇错即停，
后面的回 ``skipped``。每次按键 / 点击前实现方都要核前台是 Express、落点在 Express 窗口上，否则回 ``refused``——
这是写死在实现里的守卫，不靠调用方自觉。

动作（``{"do": ...}``）：
- 窗口：``activate``、``maximize_main``、``fit``（把超出屏幕的 Express 窗口挪回屏内，见 :func:`fit_rect`）、``us_layout``、``state``（前台、Express 顶层窗口和文字、焦点格）、``windows``；
- 键盘：``keys``（SendKeys 子集，如 ``{HOME}+{END}``、``{ENTER}``）、``key``（组合键，如 ``ctrl+F4``、``alt``）、
  ``type``（英文数字直接打，泰文走剪贴板 + Shift+Insert）、``paste``；
- 表单：``command``（工具栏命令号，灰的拒绝）、``toolbar``（各命令亮不亮）、``fields``（当前单据窗口的输入格和左边标签）、
  ``grid_click``（点明细表格某一格）、``dialog_button``（按弹窗里某个文字的按钮）；
- 鼠标（只给 AI 接手用）：``click``、``drag``、``scroll``、``move``、``mouse``、``cursor``；
- 画面：``screenshot``（缩到给定大小的 PNG，base64）、``zoom``；
- 菜单：``menu``（``path`` 每项是编号或菜单文字，``name`` 是单据名，找法见 ``menu.resolve``）、``menu_tree``（只读，各层文字 /
  命令号 / 是否子菜单 / 是否灰，深度 3）；
- 其他：``wait``、``close_express``。
"""
from __future__ import annotations

from typing import Dict, List, Protocol


# CC 自己用 SendInput 发的每个键盘、鼠标事件都在 dwExtraInfo 打这个签名；「有人在用」的键鼠钩子只忽略带它的（主会话 10-30）
CC_INPUT_MARK = 0x50524E4C  # "PRNL"


FIT_SLACK = 16  # 最大化的窗口边框本来就比工作区多出几像素；差这么多以内不算超出


def fit_rect(rect: List[int], work: List[int], *, main: bool = False) -> List[int]:
    """窗口（``[左, 上, 右, 下]``）超出所在显示器的工作区（去掉任务栏）时该放到哪：回新的 ``[左, 上, 右, 下]``，不用动回空列表。

    按类（11-13 测试机1 150% 缩放：Express 的银行流水报表窗口一部分在屏幕外，按钮看不到、截图截不全）：主窗口铺满工作区；
    别的窗口（单据、报表、弹窗）比工作区大就缩到工作区大小，再整个挪进工作区。"""
    left, top, right, bottom = (int(v) for v in rect)
    wl, wt, wr, wb = (int(v) for v in work)
    if wr <= wl or wb <= wt or right <= left or bottom <= top:
        return []
    inside = left >= wl - FIT_SLACK and top >= wt - FIT_SLACK and right <= wr + FIT_SLACK and bottom <= wb + FIT_SLACK
    if inside:
        return []
    if main:
        return [wl, wt, wr, wb]
    width, height = min(right - left, wr - wl), min(bottom - top, wb - wt)
    x = min(max(left, wl), wr - width)
    y = min(max(top, wt), wb - height)
    return [x, y, x + width, y + height]


class Refused(Exception):
    """守卫拒绝：前台不是 Express、落点不在 Express、命令是灰的、弹窗里没有那个按钮等。"""


class MenuAmbiguous(Exception):
    """菜单路径落到子菜单，里面有不止一个可用命令（同一种单分几套单号，11-05 测试机）：不猜，带上候选文字（去掉编号）
    与走到这个子菜单的路径，由引擎按载荷的选择定一项。"""

    def __init__(self, options: List[str], path: List[str]):
        super().__init__(f"menu submenu has {len(options)} commands: {' | '.join(options)[:200]}")
        self.options, self.path = list(options), list(path)


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
        except MenuAmbiguous as error:  # 带着候选回给引擎（引擎据此抛同一个异常）
            failed = True
            results.append({"ok": False, "error": str(error), "ambiguous": error.options, "path": error.path})
        except Exception as error:  # noqa: BLE001 — 回给引擎，由它决定接手还是停下
            failed = True
            results.append({"ok": False, "error": f"{type(error).__name__}: {error}"})
    return results
