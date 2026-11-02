"""流程用例：ERP 手机宽度的弹窗里点最下面几格，键盘弹出后正在填的格不被盖住（11-01 用户：「弹起来的输入框会把这个字段盖住」）。

物料「新建」、团队「添加用户」、入库「新建」（含明细行）、调拨「新建」（最后一格是备注多行框，11-01 iPhone LINE 被挡的那一格）
四个弹窗，各取最下面三格，按 iOS Safari（只缩可见区）、iOS App 内嵌（坐标原点跟可见区走）与安卓（整页变矮）各模拟一遍
（见 `keyboard_lib.py`）：这一格在可见区里、中心点就是它，弹窗外框缩成可见区高、滚得到按钮栏；键盘收起后类名去掉、弹窗不留样式。
只点不填，不提交，关掉弹窗不留数据。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from keyboard_lib import PRELOAD, judge_probes, probe_steps  # noqa: E402,F401

TITLE = "ERP 弹窗：键盘弹出后正在填的格不被盖住"
DIALOGS = [("物料新建", None), ("团队添加用户", "pearnly-team"), ("入库新建明细", "pearnly-stock-in"),
           ("调拨新建备注", "pearnly-stock-transfer")]
CLOSE = ("document.querySelectorAll('.modal.show .btn-modal-close').forEach(b=>b.click());"
         "return {closed: !!(await kbUntil(()=>!document.querySelector('.modal.show'),40))};")


def prepare(lab, manifest):
    return {"cases": []}


def opener(route):
    return ((f"frappe.set_route({json.dumps(route)}); await kbSleep(800);" if route else "")
            + "const b=await kbUntil(()=>[...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e=>e.offsetParent));"
            "if(!b) return {error:'no new button'}; b.click();"
            "if(!await kbUntil(()=>document.querySelector('.modal.show .modal-body input'))) return {error:'dialog not opened'};"
            "await kbSleep(600); const root=document.querySelector('.modal.show .modal-body');")


def steps(params, camera_code=None):
    return [step for name, route in DIALOGS for step in probe_steps(name, opener(route), ios=True, close_js=CLOSE)]


def judge(params, results, camera_code=None):
    return judge_probes(results, [name for name, _ in DIALOGS], ios=True)


def verify(lab, manifest, params, camera_code=None):
    return []
