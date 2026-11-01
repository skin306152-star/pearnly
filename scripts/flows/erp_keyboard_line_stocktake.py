"""流程用例：LINE 手机盘点页 `/stocktake`（手机宽度）扫到系统没有的码时的新建商品各格，键盘弹出后正在填的格不被盖住（11-01）。

盘点照 `erp_line_stocktake_phone.py` 备好（库存岗本账号的假 LINE 绑定回落）；输一个没有的码出新建商品表单，取最下面三格按安卓（整页变矮）
模拟（见 `keyboard_lib.py`；整页滚动的 iOS 平移模拟不了）。只点不填、不建商品。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_line_stocktake_phone import READY, prepare  # noqa: E402,F401
from keyboard_lib import PRELOAD, judge_probes, probe_steps  # noqa: E402,F401

TITLE = "ERP 手机盘点：键盘弹出后新建商品各格不被盖住"
NAME = "新建商品"


def steps(params, camera_code=None):
    code = json.dumps("NO-SUCH-" + uuid.uuid4().hex[:6])
    return probe_steps(NAME, (
        "await kbUntil(()=>document.querySelector('[data-task], #code, #app .error'));"
        f"const b=document.querySelector('[data-task=\"{params['count']}\"]'); if(b) b.click();"
        "if(!await kbUntil(()=>document.getElementById('code'))) return {error:'count not opened'};"
        f"document.getElementById('code').value={code}; document.getElementById('find').click();"
        "if(!await kbUntil(()=>document.getElementById('np_min_stock'))) return {error:'no new product form'};"
        "document.activeElement&&document.activeElement.blur(); await kbSleep(400);"
        "const root=document.getElementById('newproduct');"), ios=False)


def judge(params, results, camera_code=None):
    return judge_probes(results, [NAME], ios=False)


def verify(lab, manifest, params, camera_code=None):
    return []
