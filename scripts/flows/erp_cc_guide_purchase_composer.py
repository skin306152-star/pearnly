"""流程用例：ERP 版 CC **直写**对话里先点「采购」、再用输入框 + 上传一张票、只发文件——对话里出采购引导卡并交进去识别，库里一条采购引导运行、
交的就是那一份、ERP 建了对话收单记录（11-28 testerp 三次复现：原来停在「文件在会话里」）。做法见 `cc_guide_composer_lib.py`。"""
from __future__ import annotations

import erp_cc_guide_composer_lib as lib

TITLE = "ERP 版 CC 直写先选采购再用输入框上传：出采购引导卡并交进识别；一条引导运行、交的是那一份、ERP 建了对话收单记录"
BUSINESS = "purchase"


def prepare(lab, manifest):
    return lib.prepare(lab, manifest, BUSINESS)


steps, judge, verify, cleanup = lib.steps, lib.judge, lib.verify, lib.cleanup
