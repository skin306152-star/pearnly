"""验收场景（一组）：ERP 单据交给**代录**——CC 桌面版在测试机上打开 Express 界面录入（不是直写 DBF）→ 回执 → 账本按单号读回 →
预期以外的表逐字节不变 → 恢复基线。

  acc.erp.operator.purchase.express   现购一张（货品入库）由代录录成
  acc.erp.operator.sales.express      赊销一张（新建「ร้าน…」客户、只记账的一行）由代录录成
  （留桩，没登记）acc.erp.operator.cloud.* 云端 Express、acc.erp.operator.mrerp.* MR.ERP 代录：产品还没到，走到这里记前置条件不满足

编排 B，**要占测试机的桌面**（代录在那台电脑的 Express 窗口里操作，一次一条；有人在用电脑它会停下等）。模型不参与，费用 0。
**状态：写好未跑**（11-15 晚）。跑之前要具备、首跑要校准的：
- 那台测试机的 CC 桌面版上登录的员工已做过「授权登录」（Express 的登录存在这台电脑的凭据管理器里），能开服务器上的验收账套；
  20:40 前收尾（accserver 关机）。
- 代录写出的单与直写同一张单应当一样：读回断言与 `acc.erp.purchase.express` / `acc.erp.sales.credit.express` 用同一套；
  代录特有的只核三样——写账件是代录交的（请求号带代录前缀）、这张单名下只有这一件写账、代录任务里这张的状态是已录入并带同一个单号。
- 从服务端函数走（via = "api"）：对话里下令、开始前卡片、Express 中途弹框带回对话、被人打断后「继续 / 停止」都不在这条里（真机清单）。
- 允许变化的表先按直写的清单；Express 界面自己录会不会多动别的表（如单据流水号、使用记录），首跑时看「预期以外」列出什么再定。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
Unmet = sys.modules["acc_errors"].Unmet
OPERATOR_KEY = "erp:operator:"   # 同 `express.push.OPERATOR_KEY`


def _chain():
    if "acc_erp_chain" not in sys.modules:
        spec = importlib.util.spec_from_file_location("acc_erp_chain", ROOT / "scripts" / "acceptance" / "erp_chain.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["acc_erp_chain"] = module
        spec.loader.exec_module(module)
    return sys.modules["acc_erp_chain"]


def run(ctx):
    chain = _chain()
    parts = ctx.scenario["id"].removeprefix("acc.erp.operator.").split(".")
    if parts[0] in ("cloud", "mrerp"):
        raise Unmet({"cloud": "云端 Express 的代录产品还没到（Express Cloud 接 CC 先做只读实测）",
                     "mrerp": "MR.ERP 代录的服务端开关还关着，ERP 一侧也没接（只有 Cowork 的在途分支）"}[parts[0]])
    kind = parts[0]
    if kind not in ("purchase", "sales"):
        raise Unmet(f"场景 {ctx.scenario['id']} 在这个模块里没有对应的走法")
    conf = chain.settings()
    book = ctx.targets.ledger(chain.HOST, chain.LEDGER)
    chain.baseline(ctx, book, conf)

    with ctx.step("开单并交给代录", "单据交进写账队列并带代录标记；CC 桌面版在 Express 界面里录成并回执"):
        out = chain.site(ctx, "erp_operator_post.py", {**conf, "kind": kind, "run": chain.tag(ctx), "price": 10 if kind == "purchase" else 1000},
                         timeout=1200)
        if out.get("taken_by_direct"):
            raise Unmet("这张单被直写先领走了，没交到代录（站点脚本没能拦住提交后的那次后台直写）")
        state, job = out["state"], out.get("job") or {}
        if state.get("ledger_state") == "held":
            raise Unmet(f"代录停在等人处理：{state.get('ledger_reason') or job.get('reason')}")
        ctx.require("写账状态", "written", state.get("ledger_state"))
        ctx.check("回显了 Express 单号", True, bool(state.get("ledger_docnum")))
        ctx.check("这件写账是代录交的", True, str(job.get("request_key") or "").startswith(OPERATOR_KEY))
        ctx.check("这张单名下只有这一件写账（直写没有再接）", 1, out.get("jobs"))
        rows = [row for row in out["task_view"]["documents"] if row.get("doc_id") == out["doc_id"]]
        ctx.require("代录任务里有这张单", 1, len(rows))
        ctx.check("代录任务里这张是已录入、带同一个单号", ["saved", state["ledger_docnum"]], [rows[0].get("state"), rows[0].get("docnum")])
        chain.only_ledger(ctx, out["book_paths"])
        ctx.facts.update(docnums=[state["ledger_docnum"]], ledger=chain.LEDGER, erp_doc=out["doc"], task=out["task"])

    with ctx.step("账本读回", "账套里按单号读得到这张单，与直写同一张单应有的一样；预期以外的表逐字节不变"):
        docnum, expected = state["ledger_docnum"], out["expected"]
        if kind == "sales":
            after, summary = chain.copy_and_diff(ctx, book, conf, chain.sales_tables())
            chain.invoice_readback(ctx, book, after, out)
            ctx.check("ARTRN 只多一行", 1, chain.grew(summary, "ARTRN"))
        else:
            base = chain.tuple_of("companion/src/companion/dbf_schema.py", "BACKUP_TABLES")
            allow = [name for name in base if name not in ("ARTRN", "ARBAL", "ARMAS")] + ["STLOC", "ARTRNRM", "GLBAL"]
            after, summary = chain.copy_and_diff(ctx, book, conf, allow)
            rows = chain.rows(book, after, "APTRN", f"DOCNUM={docnum}")
            ctx.save("aptrn-row.json", rows)
            ctx.require("APTRN 里这张单恰好一行", 1, len(rows))
            ctx.check("供应商编码", expected["supplier_code"], str(rows[0].get("SUPCOD") or "").strip())
            ctx.check("含税合计", chain.money(expected["totals"]["grand_total"]), chain.money(rows[0].get("NETAMT")))
            ctx.check("税额", chain.money(expected["totals"]["vat_amount"]), chain.money(rows[0].get("VATAMT")))
            lines = chain.rows(book, after, "STCRD", f"DOCNUM={docnum}")
            ctx.require("STCRD 里这张单的明细恰好一行", 1, len(lines))
            ctx.check("明细的物料与数量", [expected["item_code"], float(expected["qty"])],
                      [str(lines[0].get("STKCOD") or "").strip(), abs(float(lines[0].get("TRNQTY") or 0))])
            ctx.check("APTRN 只多一行", 1, chain.grew(summary, "APTRN"))
            ctx.check("进项税登记恰好一行", 1, len(chain.rows(book, after, "ISVAT", f"DOCNUM={docnum}")))
        ctx.check("凭证多一张", 1, chain.grew(summary, "GLJNL"))


def cleanup(ctx):
    _chain().cleanup(ctx)
