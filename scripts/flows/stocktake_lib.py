"""盘点录入弹窗（ERP / Cowork 同一个）的页面操作片段（11-02 起一个框：打字选物料、回车按码认、框内扫码图标开扫码器）。

页面步骤里弹窗变量叫 `d`。
"""
from __future__ import annotations


def type_code(code_js: str) -> str:
    """框里填一个码回车（扫码枪 / 手打编码），`code_js` 是 JS 表达式（已 json.dumps 的字符串）。"""
    return (f"d.fields_dict.code.$input.val({code_js}); "
            "d.fields_dict.code.input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); "
            "await new Promise(r=>setTimeout(r,300));")


# 点框内右侧的扫码图标开扫码器
SCAN = "d.fields_dict.code.$wrapper.find('.pearnly-link-scan').trigger('click');"
# 弹窗里有没有那颗扫码图标
HAS_SCAN = "d.fields_dict.code.$wrapper.find('.pearnly-link-scan').length"
