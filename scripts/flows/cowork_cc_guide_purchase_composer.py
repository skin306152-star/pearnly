"""流程用例：Cowork 版 CC **直写**对话里先点「采购」、再用输入框 + 上传一张票、只发文件——同 ERP 那两格（`cc_guide_composer_lib.py`）：
对话里出采购引导卡并交进识别，库里一条采购引导运行、交的就是那一份、有识别批次。原来 Cowork 靠模型自己调识别工具（真模型调不调不定、
识别完也不进复核卡），现在与 ERP 同一条路。造数据同 Cowork 表单引导（`cowork_cc_guide_form_lib`：一条 Express 连接、本月、账套 T.8）。"""
from __future__ import annotations

import cc_guide_composer_lib as lib
import cowork_cc_guide_form_lib as form

TITLE = "Cowork 版 CC 直写先选采购再用输入框上传：出采购引导卡并交进识别；一条引导运行、交的是那一份、有识别批次"
BUSINESS = "purchase"
steps, judge = lib.steps, lib.judge


def prepare(lab, manifest):
    params = {**form.prepare(lab, manifest, tag_prefix="gcc"), "business": BUSINESS}
    params["file"] = f"{params['tag']}.png"
    try:
        return {**params, **lib.configure(lab, manifest)}
    except Exception:
        cleanup(lab, manifest, params)
        raise


def verify(lab, manifest, params, camera_code=None):
    return lib.guide_cases(lib.guide_rows(lab, manifest, params), params)


def cleanup(lab, manifest, params):
    lib.release(lab, manifest, params)
    return form.cleanup(lab, manifest, params)
