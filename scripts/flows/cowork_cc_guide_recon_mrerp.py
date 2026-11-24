"""流程用例：Cowork 版 CC 对账引导在**接 MR.ERP 的公司**的对话里（总表「对账 · Cowork 直写 · MR.ERP」）：同
`cowork_cc_guide_recon_follow`（交两份 xlsx、卡上答到可确定 → 确定卡「确定」→ 结果卡带追问 → 答追问记成预设），只是会话开在一家从
MR.ERP 账套目录建的公司下（`cowork_mrerp_lib`，编造的连接与目录、不带凭据）。对账两侧都是交进来的文件，与账本种类无关：这条守的是
MR.ERP 公司的对话里引导照样开得出、办得完。cleanup 先照原流程删会话与对账，再删这家公司与连接。"""
from __future__ import annotations

import cowork_cc_guide_recon_follow as recon
import cowork_mrerp_lib as mr

TITLE = "Cowork 版 CC 对账引导（接 MR.ERP 的公司）：卡上交两侧文件确定 → 结果卡带追问 → 答追问后消失并记成预设"
USER = recon.USER


def prepare(lab, manifest):
    seeded = lab.python(manifest["site"], mr.SEED + 'print(json.dumps({"company": mr_company, "connection": mr_conn}))')
    try:
        return {**recon.prepare(lab, manifest, company=seeded["company"]), "mr_company": seeded["company"]}
    except Exception:
        lab.python(manifest["site"], mr.CLEANUP + 'mr_cleanup()\nprint("{}")')
        raise


steps, judge, verify = recon.steps, recon.judge, recon.verify


def cleanup(lab, manifest, params):
    try:
        recon.cleanup(lab, manifest, params)
    finally:
        lab.python(manifest["site"], mr.CLEANUP + 'mr_cleanup()\nprint("{}")')
