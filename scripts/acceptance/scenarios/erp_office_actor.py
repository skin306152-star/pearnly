"""验收场景（修复前红）：只用手机的员工发的单由办公室电脑写（office_actor）。

  acc.erp.office.actor.purchase   一个从没在电脑上登录过 CC 的员工（只用手机）开现购单提交 → 写账件归这家公司里在够得到这本账套的
                                   电脑上登录过 CC 的员工（11-17 定稿起改为领单时替写：写账件仍归他本人，`cc_desktop.jobs` 领单时判）→ 测试机那台 CC 桌面版领走写进
                                   Express → 账本读回；ERP 单据仍是发单人的，单据页不说「CC 桌面版未连接」。

修复前红：评估「ERP 写账人改走 office_actor」（11-16 主会话「按建议」，排在 H3 与「未连接提示」之后施工，流程 `erp.office-actor` 先红后绿
的证据在共享笔记，代码还没推到远端分支）：`express/push.actor_of` 对采购 / 销售单仍回开单人，这张单排着没人领、单据页一直「CC 桌面版
未连接」。站点脚本见到写账件归发单人就不空等，撤件作废后交回，场景判不通过。修复进 main 并部署到被测站点后应转绿。

编排 B（实验栈 + 测试机2 的 CC 桌面版 + 验收账套），直写不占桌面。模型不参与，费用 0；从服务端函数走（via = "api"）。
**状态：写好未跑**（11-16）。要配：`PEARNLY_ACC_ERP_PHONE_USER`——验收公司里一个有开采购单权限、从没在任何电脑上登录过 CC 的员工
（没配记前置条件不满足）；测试机那台 CC 桌面版此刻在线（不在线记前置条件不满足）。
"""
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
Unmet = sys.modules["acc_errors"].Unmet
NOT_CONNECTED = "express.cc_desktop_not_connected"   # 同 `express.contracts.NOT_CONNECTED`


def _chain():
    if "acc_erp_chain" not in sys.modules:
        spec = importlib.util.spec_from_file_location("acc_erp_chain", ROOT / "scripts" / "acceptance" / "erp_chain.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["acc_erp_chain"] = module
        spec.loader.exec_module(module)
    return sys.modules["acc_erp_chain"]


def run(ctx):
    chain = _chain()
    if ctx.scenario["id"] != "acc.erp.office.actor.purchase":
        raise Unmet(f"场景 {ctx.scenario['id']} 在这个模块里没有对应的走法")
    phone = os.environ.get("PEARNLY_ACC_ERP_PHONE_USER", "")
    if not phone:
        raise Unmet("没配：PEARNLY_ACC_ERP_PHONE_USER（验收公司里一个只用手机、从没在电脑上登录过 CC 的员工）")
    conf = chain.settings()
    book = ctx.targets.ledger(chain.HOST, chain.LEDGER)
    chain.baseline(ctx, book, conf)
    with ctx.step("只用手机的员工提交", "写账件归发单人；办公室电脑领单时替他写成；单据仍是发单人的，不显示未连接"):
        out = chain.site(ctx, "erp_office_actor.py", {**conf, "phone_user": phone, "run": chain.tag(ctx), "price": 10}, timeout=600)
        first = out.get("first_job") or {}
        ctx.facts.update(erp_doc=out.get("doc"))
        # 11-17 定稿：写账件归发单人本人，办公室那台在领单那一刻替他写、写账件上记下替写的人
        ctx.check("写账件归发单人本人", phone, first.get("actor"))
        ctx.check("办公室电脑上登录 CC 的员工替他写", conf.get("user"), (out.get("job") or {}).get("writer"))
        ctx.check("排队时单据页不说 CC 桌面版未连接", True, out.get("note") != NOT_CONNECTED)
        state = out.get("state") or {}
        ctx.require("写账状态", "written", state.get("ledger_state"))
        ctx.check("回显了 Express 单号", True, bool(state.get("ledger_docnum")))
        ctx.check("单据仍是发单人的", phone, out.get("owner"))
        ctx.check("CC 桌面版写后读回", "ok", (out.get("job") or {}).get("verify_state"))
        chain.only_ledger(ctx, out.get("book_paths"))
        ctx.facts.update(docnums=[state["ledger_docnum"]], ledger=chain.LEDGER)
    with ctx.step("账本读回", "账套里按单号读得到这张现购单；预期以外的表逐字节不变"):
        chain.purchase_readback(ctx, book, conf, state["ledger_docnum"], out["expected"])


def cleanup(ctx):
    _chain().cleanup(ctx)
