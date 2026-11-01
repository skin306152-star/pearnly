"""流程用例：DMS LINE 订车编辑页（手机宽度）点最下面几格，键盘弹出后正在填的格不被键盘和贴底操作条盖住（11-01）。

编辑页照 `dms_booking_editor.py` 备好（本站点已登录账号自己的绑定回落路径）；取表单最下面三格按安卓（整页变矮）模拟
（见 `keyboard_lib.py`；整页滚动的 iOS 平移模拟不了）。只点不填、不保存。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dms_booking_editor import prepare  # noqa: E402,F401
from keyboard_lib import PRELOAD, judge_probes, probe_steps  # noqa: E402,F401

TITLE = "DMS 订车编辑页：键盘弹出后正在填的格不被盖住"
READY = "!!document.querySelector('#app')"
NAME = "编辑页"
OPEN = ("await kbUntil(()=>document.querySelector('.pn-field-delivery_date_be input'),160); await kbSleep(800);"
        "const root=document.querySelector('form')||document;")


def steps(params, camera_code=None):
    return probe_steps(NAME, OPEN, ios=False)


def judge(params, results, camera_code=None):
    return judge_probes(results, [NAME], ios=False)


def verify(lab, manifest, params, camera_code=None):
    return []
