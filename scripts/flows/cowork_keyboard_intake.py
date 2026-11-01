"""流程用例：Cowork 手机宽度的录入复核页点最下面几格，键盘弹出后正在填的格不被键盘和贴顶的分区导航盖住（11-01）。

票照 `cowork_intake_review.py` 手工建（不跑识别）；取页面最下面三格按安卓（整页变矮）模拟（见 `keyboard_lib.py`；整页滚动的 iOS 平移模拟不了）。
只点不填、不保存。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cowork_intake_review import prepare  # noqa: E402,F401
from keyboard_lib import PRELOAD, judge_probes, probe_steps  # noqa: E402,F401

TITLE = "Cowork 录入复核：键盘弹出后正在填的格不被盖住"
NAME = "复核页"
OPEN = ("await kbUntil(()=>document.querySelector('.frappe-control[data-fieldname=\"party_tax\"] input')); await kbSleep(1500);"
        "const page=[...document.querySelectorAll('.page-container')].find(e=>e.offsetParent)||document;"
        "const root=page.querySelector('.layout-main-section')||page;")


def steps(params, camera_code=None):
    return probe_steps(NAME, OPEN, ios=False)


def judge(params, results, camera_code=None):
    return judge_probes(results, [NAME], ios=False)


def verify(lab, manifest, params, camera_code=None):
    return []
