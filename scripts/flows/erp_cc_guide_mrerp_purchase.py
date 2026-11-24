"""流程用例：ERP 版 CC **直写**对话里的采购引导在接 MR.ERP 的公司上（总表「采购 · ERP 直写 · MR.ERP」）：题卡问 MR.ERP 导入菜单 →
复核卡 → 确定 → 结果卡；收单记录提交成 ERP 单、去向 MR.ERP，菜单记在公司这份账套名下。做法与限制见 `erp_cc_guide_mrerp_lib.py`
（编造的 MR.ERP 连接，不带凭据，不连真 MR.ERP）。"""
from __future__ import annotations

import erp_cc_guide_mrerp_lib as lib

TITLE = "ERP 版 CC 直写采购引导（接 MR.ERP 的公司）：题卡问导入菜单 → 复核卡 → 确定 → 结果卡；提交成 ERP 单、去向 MR.ERP、菜单记在账套名下"
BUSINESS = "purchase"


def prepare(lab, manifest):
    return lib.prepare(lab, manifest, BUSINESS)


steps, judge, verify, cleanup = lib.steps, lib.judge, lib.verify, lib.cleanup
