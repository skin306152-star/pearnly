"""流程用例：代录对话里识别挂在服务端等时，在界面上点发送能发出去。同 `cc_send_while_waiting.py`（直写），只是先在代录新建页
选公司说一句建一段代录会话（造数据同 `cowork_operator_lib.py`），再在这段会话里挂附件让 CC 识别。"""
from __future__ import annotations

import cc_send_while_waiting as base
from cc_send_while_waiting import READY, USER, cleanup, judge, steps, verify  # noqa: F401

TITLE = "CC 代录对话识别挂在服务端等时在界面上点发送能发（代录，方案 B：模型空闲显示发送，状态行照常说识别这一步）"


def prepare(lab, manifest):
    return base.prepare(lab, manifest, mode="operator")
