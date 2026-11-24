"""流程用例：Cowork 版 CC 销售引导在接 MR.ERP 的公司的直写对话里（总表「销售 · Cowork 直写 · MR.ERP」）：账套有两个已关联的导入菜单、
还没选过 → 题卡上出导入菜单题 → 点选 → 复核卡 →「立即录入」→ 结果卡；菜单记在账套名下、票推向这份账套。做法与限制见
`cowork_cc_guide_mrerp_docs_lib.py`（编造的 MR.ERP 连接，不带凭据，不连真 MR.ERP）。"""
from __future__ import annotations

import cowork_cc_guide_mrerp_docs_lib as lib

TITLE = "Cowork 版 CC 销售引导（接 MR.ERP 的公司）：题卡问导入菜单 → 复核卡 →「立即录入」→ 结果卡；菜单记在账套名下、票推向这份账套"
BUSINESS = "sales"


def prepare(lab, manifest):
    return lib.prepare(lab, manifest, BUSINESS)


steps, judge, verify, cleanup = lib.steps, lib.judge, lib.verify, lib.cleanup
