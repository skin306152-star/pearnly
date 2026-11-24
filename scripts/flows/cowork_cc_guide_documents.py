"""流程用例：Cowork 版 CC「单据」引导在直写对话里真页面走一遍删除（点菜单 → 操作「删除」→ 选单 → 复核 → 允许 → 结果卡已删除 1 条；
库里运行 done、票真删了）。做法见 `cowork_cc_guide_maint_lib.py`。"""
from __future__ import annotations

import cowork_cc_guide_maint_lib as lib

TITLE = "Cowork 版 CC「单据」引导（直写）：选删除 → 选单 → 复核 → 允许 → 结果卡已删除 1 条；运行 done、票真删了"


def prepare(lab, manifest):
    return lib.prepare(lab, manifest, "documents", "chat")


steps, judge, verify, cleanup = lib.steps, lib.judge, lib.verify, lib.cleanup
