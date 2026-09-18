# -*- coding: utf-8 -*-
"""DMS 业务客户端的错误类型 + 日期工具。

单独一个文件是为了**打断循环 import**：业务客户端的几个 mixin 互相引用，
错误类型和工具放中间层，大家都能 import 它而不会成环。

照搬老产品 `services/erp/mrerp_dms_client_base.py`。

跟 `dms_adapter.py` 里那几个错误的区别：

· `dms_adapter.DmsAuthError` —— **登录**被拒（账号密码/未批准）。重试没用。
· 这里的 `DmsClientError` —— **业务动作**失败（表单被拒、主档对不上、写库没生效）。
  带一个 `error_code`，上层据此给友好提示，不用去匹配字符串。
"""

from __future__ import annotations

from datetime import date


class DmsClientError(RuntimeError):
    """DMS 业务/传输失败。带 `error_code`（ERR_* 分类），上层不用猜。"""

    def __init__(self, message: str, error_code: str = "ERR_DMS_TECHNICAL"):
        super().__init__(message)
        self.error_code = error_code


# Excel 的日期序列是从 1899-12-30 起算的天数（Lotus-1-2-3 的纪元，Excel 沿用了它）。
# DMS 的导入模板在 C2/F2 用这个序列。
_EXCEL_EPOCH = date(1899, 12, 30)


def excel_serial(d: date) -> int:
    return (d - _EXCEL_EPOCH).days


def to_be_date(d: date) -> str:
    """公历 → 佛历 dd/mm/yyyy（年份 + 543）。"""
    return f"{d.day:02d}/{d.month:02d}/{d.year + 543}"
