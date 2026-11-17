"""流程用例：窗口刚开就点了通知（桌面端托盘 / 页面通知点开派发 ``tomo-open``，``window.tomo.open``），页面还在切到那段对话时
就加附件、发一句——同 ``cc_deep_link_attach.py``（同一段 openConversation，同样拖慢侧栏第一页与取单段会话），只是从空白新对话页起、
页面一出来就派发 ``tomo-open``。
"""
from __future__ import annotations

import cc_deep_link_attach as base
from cc_deep_link_attach import PRELOAD, READY, judge, steps, verify  # noqa: F401

TITLE = "CC 通知点开对话（tomo-open）、切过去之前就加附件和发送：附件与消息都进那段对话，不另建会话"


def prepare(lab, manifest):
    return {**base.prepare(lab, manifest), "via": "tomo"}
