"""验收场景：Cowork 的一张采购票交给**代录**——CC 桌面版在测试机上打开 Express 界面录入（不是直写 DBF）→ 回执 → 账本按单号读回 →
预期以外的表逐字节不变 → 恢复基线（核心集第 12 条，格 Cowork 代录 · 本地 Express · 采购）。

  acc.cowork.operator.purchase.express   一张服务类采购票（样票 purchase-a2，内容写死、不跑识别）由代录录成

编排 B，要占测试机的桌面（代录在那台电脑的 Express 窗口里操作，一次一条）。模型不参与，费用 0。从服务端函数走（via = "api"）：
对话里下令、开始前卡片、中途弹框带回对话不在这条里。
代录写出的单与直写同一张票应当一样：账本读回与 `acc.cowork.intake.express` 同一套断言；代录特有的只核三样——推送载荷是代录载荷
（``entry_mode=operator``）且批次就是这次建的代录任务、这张票名下只有这一条推送、代录任务里这张是已录入并带同一个单号。

**状态：写好未跑**（11-23，核心集批 2）。参数同 `cowork_intake_express`（PEARNLY_ACC_COWORK_USER / _COWORK_CONNECTION / _BASELINE /
_LEDGER / _LEDGER_HOST，站点跟 PEARNLY_ACC_SITE_TARGET）；那台电脑的 CC 桌面版上登录的员工要已在 CC 里做过 Express「授权登录」。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
Unmet = sys.modules["acc_errors"].Unmet


def _intake():
    """同一套设置、样票、站点与读回（`cowork_intake_express`），不另写一份。"""
    key = "acc_cowork_intake_shared"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / "cowork_intake_express.py")
        sys.modules[key] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[key])
    return sys.modules[key]


def run(ctx):
    intake = _intake()
    chain = intake._chain()
    conf, ticket = intake.settings(chain), intake.sample()
    book = ctx.targets.ledger(chain.HOST, chain.LEDGER)
    chain.baseline(ctx, book, conf)

    with ctx.step("交给代录并等录成", "票交进代录任务，推送载荷是代录载荷；CC 桌面版在 Express 界面录成并回执"):
        out = chain.site(ctx, "cowork_operator_post.py",
                         {**conf, "run": chain.tag(ctx), "seller_tax": ticket["supplier"]["tax_id"], "seller_name": ticket["supplier"]["name"],
                          "amount": ticket["expect"]["amount"], "vat": ticket["expect"]["vat"], "lines": ticket.get("lines") or []},
                         timeout=1200, product="cowork", name=intake.SITE, target=intake.SITE_TARGET)
        record = out.get("record") or {}
        if record.get("status") == "manual":
            raise Unmet(f"代录停在等人处理：{record.get('reason')}")
        ctx.require("交单后没有停在要选的项上", [], [row.get("reason") for row in (out.get("started") or {}).get("results") or []
                                                if row.get("status") == "needs_choice"])
        ctx.require("推送状态", "success", record.get("status"))
        ctx.check("回显了 Express 单号", True, bool(record.get("docnum")))
        ctx.check("推送载荷是代录载荷（entry_mode=operator）", True, record.get("operator"))
        ctx.check("代录批次就是这次建的代录任务", out.get("task"), record.get("operator_batch"))
        ctx.check("这张票名下只有这一条推送（直写没有再接）", 1, out.get("pushes"))
        rows = [row for row in (out.get("task_view") or {}).get("documents") or [] if row.get("doc_id") == out.get("history")]
        ctx.require("代录任务里有这张票", 1, len(rows))
        ctx.check("代录任务里这张是已录入、带同一个单号", ["saved", record.get("docnum")], [rows[0].get("state"), rows[0].get("docnum")])
        chain.only_ledger(ctx, [record.get("written_to") or record.get("frozen_book")])   # 代录回执不带落点：按交给代录时冻结的那本
        ctx.facts.update(docnums=[record["docnum"]], ledger=chain.LEDGER, erp_doc=out.get("history"), task=out.get("task"))

    with ctx.step("账本读回", "账套里按单号读得到这张单，表头与票面一致；预期以外的表逐字节不变"):
        after, summary = chain.copy_and_diff(ctx, book, conf, intake.purchase_tables(chain))
        rows = chain.rows(book, after, "APTRN", f"DOCNUM={record['docnum']}")
        ctx.save("aptrn-row.json", rows)
        ctx.require("APTRN 里这张单恰好一行", 1, len(rows))
        ctx.check("供应商编码", ticket["expect"]["supplier_code"], str(rows[0].get("SUPCOD") or "").strip())
        ctx.check("含税合计", chain.money(out["expected"]["total"]), chain.money(rows[0].get("NETAMT")))
        ctx.check("税额", chain.money(out["expected"]["vat"]), chain.money(rows[0].get("VATAMT")))
        ctx.check("APTRN 只多一行", 1, chain.grew(summary, "APTRN"))
        ctx.check("进项税登记恰好一行", 1, len(chain.rows(book, after, "ISVAT", f"DOCNUM={record['docnum']}")))
        ctx.check("凭证多一张", 1, chain.grew(summary, "GLJNL"))


def cleanup(ctx):
    _intake()._chain().cleanup(ctx)
