"""验收场景（一组）：ERP 采购 / 销售单导入 MR.ERP 测试账套（test01 · TEST2019）→ 按单号把 MR.ERP 的单据页与日记账读回来核。

把 11-15 写账窗口在实验栈真写过的那批（共享笔记「MR.ERP写账-testerp真机清单-11-15.md」的 T1–T8）做成可重复的四种单：
  acc.erp.mrerp.purchase.goods     货品赊购（T1）：采购日记账，借方合计 = 贷方合计 = 单据合计，其中有一行进项税 = 单据税额
  acc.erp.mrerp.purchase.expense   费用（T2）：分录借贷各等于单据合计（单行含税）
  acc.erp.mrerp.sales.credit       赊销（T3 / T4）：贷方有一行销项税 = 单据税额
  acc.erp.mrerp.sales.cash         现销现金（T5）：借方一行 = 单据合计
  acc.erp.mrerp.twice              写成后再推一次、再跑一次导入：单号不变，MR.ERP 列表里仍只有这一张
  acc.erp.mrerp.purchase.cash      现购（T7）：分录借贷相等、进项税一行；贷方不是应付（记现金）——只核金额，科目首跑时看
  acc.erp.mrerp.sales.discount     整单折扣的赊销（T8）：金额 = 单据合计（折后），销项税一行
  acc.erp.mrerp.purchase.novat     无税卖家的采购（T8）：借贷各一行都等于合计，没有进项税
  acc.erp.mrerp.purchase.nobook    单据日期不在任何账套期间里（T15）：停下「没有账套」、不导入、没有 MR.ERP 单号

模型不参与，费用 0。不是 Express：没有账套基线可恢复，MR.ERP 里的单不删、会累积，断言只按本次的单号读回。

**状态：写好未跑**（11-15 晚）。**依赖三条还没进 main 的分支**（公司接入第 3 批 → `pearnly/mrerp-ledger-dialog` → `pearnly/mrerp-catalog-pick`）：
按日期选账套并把账套键记在单据上、现购写入、称谓拆分都在那里；在今天的 main 上跑，「单据上记着写进了哪本」那条会不过。
没在这里做、留着的：要人选菜单 / 再写一次 / CC 对话推送 / 导入中作废（T9–T12，界面与对话）、关开关（T16）、
税报告（T18，要在 MR.ERP 里归期）——下一批。

参数（环境变量）：
  PEARNLY_ACC_ERP_USER        开单的员工
  PEARNLY_ACC_MRERP_COMPANY   接着 MR.ERP 测试账套的那家 ERP 公司
  PEARNLY_ACC_MRERP_BOOK      账套键（默认 6:1）；PEARNLY_ACC_MRERP_LABEL 账套名里必须带的字（默认 TEST2019）
  PEARNLY_ACC_SITE_TARGET / PEARNLY_ACC_ERP_SITE 同别的 ERP 场景
跑前先问主会话：登录会踢掉别处的 test01（公司接入窗口也用它）；运行器占 `mrerp:test01` 的锁。
"""
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
Unmet = sys.modules["acc_errors"].Unmet


def _chain():
    if "acc_erp_chain" not in sys.modules:
        spec = importlib.util.spec_from_file_location("acc_erp_chain", ROOT / "scripts" / "acceptance" / "erp_chain.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["acc_erp_chain"] = module
        spec.loader.exec_module(module)
    return sys.modules["acc_erp_chain"]


def settings() -> dict:
    need = {"user": "PEARNLY_ACC_ERP_USER", "company": "PEARNLY_ACC_MRERP_COMPANY"}
    values = {key: os.environ.get(name, "") for key, name in need.items()}
    missing = [need[key] for key, value in values.items() if not value]
    if missing:
        raise Unmet("没配：" + "、".join(missing) + "（见场景文件头）")
    return {**values, "account_set": os.environ.get("PEARNLY_ACC_MRERP_BOOK", "6:1"),
            "book_label": os.environ.get("PEARNLY_ACC_MRERP_LABEL", "TEST2019")}


def amount(text) -> float:
    return round(float(str(text or "0").replace(",", "").strip() or 0), 2)


def run(ctx):
    chain = _chain()
    conf = settings()
    kind = ctx.scenario["id"].removeprefix("acc.erp.mrerp.").replace(".", "_")
    twice = kind == "twice"
    with ctx.step("提交单据并导入 MR.ERP", "单据提交后导入 MR.ERP 测试账套，账本栏记着已写入与 MR.ERP 单号"):
        out = chain.site(ctx, "erp_mrerp_post.py", {**conf, "kind": "purchase_goods" if twice else kind, "run": chain.tag(ctx),
                                                    "resend": twice}, timeout=600)
        state = out["state"]
        if kind == "purchase_nobook":
            # 11-15 清单 T15：两本年度账套都不覆盖单据日期时停下说清楚，不往任何一本里导
            ctx.check("停下、原因是没有账套、没有 MR.ERP 单号", ["held", "express.account_set_missing", ""],
                      [state.get("ledger_state"), state.get("ledger_reason"), state.get("ledger_docnum") or ""])
            ctx.check("没有去读 MR.ERP（没导入就没有可读的单）", None, out.get("read"))
            ctx.facts.update(docnums=[], ledger=f"MR.ERP {conf['book_label']} {conf['account_set']}", erp_doc=out["doc"])
            return
        if state.get("ledger_state") == "held":
            raise Unmet(f"停在等人处理：{state.get('ledger_reason')} {state.get('ledger_needed') or ''}")
        ctx.require("写账状态", "written", state.get("ledger_state"))
        ctx.check("写的是 MR.ERP", "mrerp", state.get("ledger_target"))
        ctx.check("回显了 MR.ERP 单号", True, bool(state.get("ledger_docnum")))
        ctx.check("单据上记着写进了哪本账套", conf["account_set"], state.get("ledger_book"))
        if twice:
            again = (out.get("resend") or {}).get("state") or {}
            ctx.require("确实又推了一次", True, bool(out.get("resend")))
            ctx.check("再推之后还是已写入、单号没变", ["written", state["ledger_docnum"]], [again.get("ledger_state"), again.get("ledger_docnum")])
        ctx.facts.update(docnums=[state["ledger_docnum"]], ledger=f"MR.ERP {conf['book_label']} {conf['account_set']}", erp_doc=out["doc"])

    with ctx.step("MR.ERP 读回", "按单号在所选菜单的列表里恰好一张；单据页单号一致；日记账借贷相等、金额与单据一致"):
        read, total, vat = out.get("read") or {}, amount(state.get("grand_total")), amount(state.get("vat_amount"))
        ctx.require("列表里按单号恰好一张", 1, read.get("listed"))
        ctx.check("单据页的单号", state["ledger_docnum"], str((read.get("form") or {}).get("txtdocnum") or "").strip())
        lines = read.get("journal") or []
        ctx.require("日记账里有这张单的分录", True, bool(lines))
        ctx.check("日记账里这张单只有一张凭证", 1, read.get("journals"))
        debits, credits = [amount(line["debit"]) for line in lines], [amount(line["credit"]) for line in lines]
        ctx.check("借方合计 = 贷方合计 = 单据合计", [total, total], [round(sum(debits), 2), round(sum(credits), 2)])
        shape = "purchase_goods" if twice else kind
        if shape in ("purchase_goods", "purchase_cash"):
            ctx.check("借方有一行进项税 = 单据税额", True, vat in debits)
        elif shape == "purchase_novat":
            ctx.check("无税卖家：单据税额 0，借方一行、贷方一行，都等于单据合计", [0.0, [total], [total]],
                      [vat, [value for value in debits if value], [value for value in credits if value]])
        elif shape == "sales_discount":
            ctx.check("贷方有一行销项税 = 折后的单据税额", True, vat in credits)
        elif shape == "purchase_expense":
            ctx.check("费用单行含税：借方一行、贷方一行，都等于单据合计", [[total], [total]],
                      [[value for value in debits if value], [value for value in credits if value]])
        elif shape == "sales_credit":
            ctx.check("贷方有一行销项税 = 单据税额", True, vat in credits)
            ctx.check("借方一行应收 = 单据合计", [total], [value for value in debits if value])
        elif shape == "sales_cash":
            ctx.check("借方一行现金 = 单据合计", [total], [value for value in debits if value])
