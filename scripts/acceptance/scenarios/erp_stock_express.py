"""验收场景（一组）：ERP 库存单据写进 Express 验收账套 → 账本按单号读回 → 预期以外的表逐字节不变 → 恢复基线。

编排 B（实验栈挂候选源码 + 测试机2 的 CC 桌面版 + 验收账套）。模型不参与，费用 0。前置：公司已从连接添加并同步过主档。

按场景 id 分（括号里是站点脚本按顺序做的几步）：
  acc.erp.stock.in.express        入库 2 件（→ Express 生产完工收货 PI）
  acc.erp.stock.out.express       先入库 2 件再出库 1 件（→ 领用 OU；先入库是为了不撞负库存）
  acc.erp.stock.adjust.express    物料调整 +3（→ 增减调整 JU）
  acc.erp.stock.transfer.express  先入库 2 件再调拨 1 件到另一个仓（→ 调拨 RL；账套只有一个 Express 仓时记前置条件不满足）
  acc.erp.stock.void.express      入库 2 件写成后作废（→ 原单在账本里改成作废、数量加回）
  acc.erp.stock.count.express     新建盘点 → 实盘比账面多 1 → 结束 → 按盘点结果生成调整单（再点一次不生成第二张）→ 提交（→ TK）

**状态：写好未跑**（11-15 晚）。首跑要校准的：
- 明细数量的正负（出库、调出行在 STCRD 里的符号）：现在按绝对值核；凭证张数不核（账套是定期盘存或零金额时不过账）。
- 验收账套有没有第二个 Express 仓、有没有 PI / JU / OU / TK / RL 单据簿、允不允许负库存：缺了会停在等人处理，场景把原因原样报出来。
- 从服务端函数走（via = "api"）：出入库页、盘点页的界面在流程用例上验（`erp.stock-out.new`、`erp.stock.transfer`、`erp.stock.adjust`、
  `erp.stocktake.adjust`）。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
Unmet = sys.modules["acc_errors"].Unmet

STEPS = {
    "in": [{"kind": "in", "qty": 2, "unit_cost": 10}],
    "out": [{"kind": "in", "qty": 2, "unit_cost": 10}, {"kind": "out", "qty": 1}],
    "adjust": [{"kind": "adjust", "qty": 3}],
    "transfer": [{"kind": "in", "qty": 2, "unit_cost": 10}, {"kind": "transfer", "qty": 1}],
    "void": [{"kind": "in", "qty": 2, "unit_cost": 10}, {"kind": "void", "of": 0}],
    "count": [{"kind": "count", "delta": 1}],
}
# 照 Express 原生录入的单据簿（docs/work/2026-11-18-stock-express-parity.md；express/stock_entry.py）：入库写 PI、人建物料调整写 JU、
# 盘点调整写 TK、出库按用途（站点脚本用「其他」→ OU）
PREFIX = {"in": "PI", "out": "OU", "adjust": "JU", "count": "TK", "transfer": "RL"}


def _chain():
    if "acc_erp_chain" not in sys.modules:
        spec = importlib.util.spec_from_file_location("acc_erp_chain", ROOT / "scripts" / "acceptance" / "erp_chain.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["acc_erp_chain"] = module
        spec.loader.exec_module(module)
    return sys.modules["acc_erp_chain"]


def run(ctx):
    chain = _chain()
    conf = chain.settings()
    kind = ctx.scenario["id"].removeprefix("acc.erp.stock.").removesuffix(".express")
    if kind not in STEPS:
        raise Unmet(f"场景 {ctx.scenario['id']} 在这个模块里没有对应的走法")
    book = ctx.targets.ledger(chain.HOST, chain.LEDGER)
    chain.baseline(ctx, book, conf)

    with ctx.step("提交库存单并等写入", "每张单提交后进写账队列，CC 桌面版写进验收账套并回执；作废的等原单在账本里改成作废"):
        out = chain.site(ctx, "erp_stock_post.py", {**conf, "run": chain.tag(ctx), "steps": STEPS[kind]}, timeout=900)
        docs = out["docs"]
        for doc in docs:
            state = doc["state"]
            if doc["kind"] == "void":
                ctx.check("ERP 里这张单已作废", "void", doc.get("status"))
                if state.get("ledger_void_state") == "held":
                    raise Unmet(f"作废停在等人处理：{state.get('ledger_void_reason')}")
                ctx.require("账本里的作废状态", "voided", state.get("ledger_void_state"))
                ctx.check("作废后单号没变", doc.get("docnum"), state.get("ledger_docnum"))
                continue
            if state.get("ledger_state") == "held":
                raise Unmet(f"{doc['kind']} 停在等人处理：{state.get('ledger_reason')} {state.get('ledger_needed') or ''}")
            ctx.require(f"{doc['kind']} 的写账状态", "written", state.get("ledger_state"))
            ctx.check(f"{doc['kind']} 回显的 Express 单号是 {PREFIX[doc['kind']]} 开头", True,
                      str(state.get("ledger_docnum") or "").startswith(PREFIX[doc["kind"]]))
            # 库存单（dbf_stock._verify）、调拨单（dbf_transfer._verify）是 CC 在写入过程里逐表读回核对的，核不上整单失败，不像采购 /
            # 销售另报 verify 元数据：写账件成功即读回过，verify_state 只许空或 ok（同收款，11-16 首跑按 ok 写错了）
            job = doc.get("job") or {}
            ctx.check(f"{doc['kind']} CC 桌面版写账件成功（写入时已逐表读回）", ["success", True],
                      [job.get("status"), job.get("verify_state") in ("", "ok")])
            chain.only_ledger(ctx, doc["book_paths"])
            ctx.facts.setdefault("docnums", []).append(state.get("ledger_docnum") or "")
        ctx.require("每一步都做到了", len(STEPS[kind]), len(docs))
        if kind == "count":
            count = docs[0]
            ctx.check("盘点结束后是「已结束」，生成了一张调整单草稿", ["Closed", True], [count.get("closed"), bool(count.get("draft_created"))])
            ctx.check("再点一次「按盘点结果调整物料」：回同一张、不新建", [True, False], count.get("draft_again"))
            ctx.check("调整单提交后盘点转「已过账」", "Posted", count.get("count_status"))
        ctx.facts.update(ledger=chain.LEDGER)

    with ctx.step("账本读回", "每张单按单号读得到、仓库与物料与数量一致；预期以外的表逐字节不变"):
        after, summary = chain.copy_and_diff(ctx, book, conf, chain.stock_tables())
        written = [doc for doc in docs if doc["kind"] != "void"]
        voided = {doc["docnum"] for doc in docs if doc["kind"] == "void"}
        ctx.check("STTRN 多的行数 = 写成的单数", len(written), chain.grew(summary, "STTRN"))
        for doc in written:
            docnum = doc["state"]["ledger_docnum"]
            head = chain.rows(book, after, "STTRN", f"DOCNUM={docnum}")
            ctx.save(f"sttrn-{docnum}.json", head)
            ctx.require(f"STTRN 里 {docnum} 恰好一行", 1, len(head))
            ctx.check(f"{docnum} 的仓库码", out["warehouse_code"], str(head[0].get("LOCCOD") or "").strip())
            lines = chain.rows(book, after, "STCRD", f"DOCNUM={docnum}")
            ctx.save(f"stcrd-{docnum}.json", lines)
            if docnum in voided:
                # 作废照 Express 原生：单头改作废、金额清零，明细打删除标记（读回时已滤掉），不新开一张冲回的单
                ctx.check(f"{docnum} 作废后的单头状态与金额", ["C", 0.0], [str(head[0].get("DOCSTAT") or "").strip(), chain.money(head[0].get("TRNVAL"))])
                ctx.check(f"{docnum} 作废后没有有效明细", 0, len(lines))
                continue
            ctx.check(f"{docnum} 的单头状态", "N", str(head[0].get("DOCSTAT") or "").strip())
            if doc["kind"] == "transfer":
                ctx.check(f"{docnum} 的调入仓", doc["to_warehouse_code"], str(head[0].get("PEOPLE") or "").strip())
                ctx.check(f"{docnum} 的明细是一出一入两行、同一件物料、数量相同",
                          [2, [out["item_code"]] * 2, [float(doc["qty"])] * 2],
                          [len(lines), [str(row.get("STKCOD") or "").strip() for row in lines], [abs(float(row.get("TRNQTY") or 0)) for row in lines]])
            else:
                ctx.require(f"{docnum} 的明细恰好一行", 1, len(lines))
                ctx.check(f"{docnum} 明细的物料与数量", [out["item_code"], float(doc["qty"])],
                          [str(lines[0].get("STKCOD") or "").strip(), abs(float(lines[0].get("TRNQTY") or 0))])


def cleanup(ctx):
    _chain().cleanup(ctx)
