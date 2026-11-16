"""验收场景（一组）：收银日汇总写成 Express 现销单；已写进 Express 的采购单 / 销售单作废后账本不动。

编排 B（实验栈挂候选源码 + 测试机2 的 CC 桌面版 + 验收账套）。模型不参与，费用 0。前置：公司已从连接添加并同步过主档。

  acc.erp.pos.day.express          开班 → 两张现金小票 → 合成当天的日汇总（再合一次没有第二张）→ 现销单 HS → 账本读回
  acc.erp.purchase.cancel.express  采购单写成后作废：ERP 里作废、不再排写账件；账套与作废前逐文件一致（Express 那张由财务处理）
  acc.erp.sales.cancel.express     赊销单写成后作废：同上

**状态：写好未跑**（11-15 晚）。首跑要校准的：
- 日汇总：公司是不是增值税登记户、有没有免税商品会各问一次（停在等人选），场景把原因原样报出来；HS 的客户是现销客户「เงินสด」，
  这里不核客户编码；明细按物料合并成几行没定，按数量合计核。
- 作废后等 45 秒（够 CC 桌面版领一次单）再拷现场对比；CC 领单间隔变了要跟着调。
"""
import importlib.util
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


def written(ctx, chain, out, what):
    state = out["state"]
    if state.get("ledger_state") == "held":
        raise Unmet(f"{what}停在等人处理：{state.get('ledger_reason')} {state.get('ledger_needed') or ''}")
    ctx.require("写账状态", "written", state.get("ledger_state"))
    ctx.check("回显了 Express 单号", True, bool(state.get("ledger_docnum")))
    ctx.check("CC 桌面版写后读回", "ok", (out.get("job") or {}).get("verify_state"))
    chain.only_ledger(ctx, out["book_paths"])
    ctx.facts.update(docnums=[state.get("ledger_docnum") or ""], ledger=chain.LEDGER, erp_doc=out["doc"])
    return state["ledger_docnum"]


def pos_day(ctx, chain, conf, book):
    with ctx.step("开班卖货、合成日汇总并等写入", "当天的小票合成一张日汇总，CC 桌面版写成现销单并回执"):
        # 验收账套 TEST2 是增值税登记户（它的发票带 7% 税、ISVAT 有行）：第一次写日汇总时产品问一次就答「是」（用户的正路，记在公司上）
        out = chain.site(ctx, "erp_pos_day.py", {**conf, "run": chain.tag(ctx), "vat_registered": "yes"}, timeout=900)
        if out.get("answered"):
            ctx.save("vat-registered-asked.json", out["answered"])
            ctx.check("第一次写日汇总只问了一次性的「是不是增值税登记户」", ["vat_registered"], out["answered"]["asked"])
        docnum, expected = written(ctx, chain, out, "日汇总"), out["expected"]
        ctx.check("单别是现销单", ["HS", True], [out["state"].get("doc_kind") or "HS", docnum.startswith("HS")])
        ctx.check("卖的小票都进了这张日汇总", expected["receipts"], expected["in_summary"])
        ctx.check("日汇总的张数与金额 = 卖的", [expected["receipts"], chain.money(expected["sold_total"])],
                  [int(out["state"].get("sales_count") or 0), chain.money(expected["grand_total"])])
        ctx.check("再合一次没有第二张日汇总", None, out["again"])
    with ctx.step("账本读回", "现销单按单号读得到、金额与数量一致；预期以外的表逐字节不变"):
        after, summary = chain.copy_and_diff(ctx, book, conf, chain.sales_tables())
        head = chain.rows(book, after, "ARTRN", f"DOCNUM={docnum}")
        ctx.save(f"artrn-{docnum}.json", head)
        ctx.require(f"ARTRN 里 {docnum} 恰好一行", 1, len(head))
        ctx.check("现销单合计", chain.money(expected["grand_total"]), chain.money(head[0].get("NETAMT")))
        ctx.check("现销单已收清（未收余额 0）", 0.0, chain.money(head[0].get("REMAMT")))
        lines = chain.rows(book, after, "STCRD", f"DOCNUM={docnum}")
        ctx.save(f"stcrd-{docnum}.json", lines)
        ctx.check("明细都是卖的那件物料", [expected["item_code"]], sorted({str(row.get("STKCOD") or "").strip() for row in lines}))
        ctx.check("明细数量合计 = 卖的件数", float(expected["qty"]), sum(abs(float(row.get("TRNQTY") or 0)) for row in lines))
        ctx.check("ARTRN 只多一行（两张小票合成一张）", 1, chain.grew(summary, "ARTRN"))


def cancel(ctx, chain, conf, book, kind):
    script, allow = ("erp_purchase_post.py", None) if kind == "purchase" else ("erp_sales_post.py", chain.sales_tables())
    with ctx.step("提交单据并等写入", "单据提交后进写账队列，CC 桌面版写进验收账套并回执"):
        params = {**conf, "run": chain.tag(ctx)}
        if kind == "sales":
            params.update(price=1000, buyer=f"ร้านเอซีซี {chain.tag(ctx)}")
        out = chain.site(ctx, script, params, timeout=480)
        docnum = written(ctx, chain, out, "单据")
    with ctx.step("写后的现场", "先把写成之后的账套拷一份，作废后拿它比"):
        if allow is None:   # 采购允许变化的表：照示范 1 的算法（总清单去掉应收三张）
            base = chain.tuple_of("companion/src/companion/dbf_schema.py", "BACKUP_TABLES")
            allow = [name for name in base if name not in ("ARTRN", "ARBAL", "ARMAS")] + ["STLOC", "ARTRNRM", "GLBAL"]
        first, _summary = chain.copy_and_diff(ctx, book, conf, allow, part="-written")
    with ctx.step("作废", "ERP 里作废，写账栏照旧记着已写入的单号，不再排新的写账件"):
        done = chain.site(ctx, "erp_doc_cancel.py", {"user": conf["user"], "kind": kind, "doc": out["doc"]}, timeout=300)
        ctx.check("ERP 里这张单已作废", "void", done["cancelled"]["status"])
        ctx.check("写账栏照旧：已写入、单号没变", ["written", docnum], [done["after"]["ledger_state"], done["after"]["ledger_docnum"]])
        ctx.check("没有多排写账件", len(done["jobs_before"]), len(done["jobs_after"]))
    with ctx.step("账本没被动过", "作废前后账套逐文件一致：Express 里那张单还在、没被改"):
        second = chain.after_dir(ctx, "-cancelled")
        ctx.require("拷出作废后的现场", True, book.copy(second).get("ok"))
        change = book.diff(first, second, allow=[], out=f"{second}.diff.json")
        ctx.save("ledger-diff-cancel.json", change)
        ctx.check("作废前后没有任何文件变化", [], change.get("stray"))


def run(ctx):
    chain = _chain()
    conf = chain.settings()
    kind = ctx.scenario["id"].removeprefix("acc.erp.").removesuffix(".express")
    book = ctx.targets.ledger(chain.HOST, chain.LEDGER)
    chain.baseline(ctx, book, conf)
    if kind == "pos.day":
        pos_day(ctx, chain, conf, book)
    elif kind in ("purchase.cancel", "sales.cancel"):
        cancel(ctx, chain, conf, book, kind.split(".")[0])
    else:
        raise Unmet(f"场景 {ctx.scenario['id']} 在这个模块里没有对应的走法")


def cleanup(ctx):
    _chain().cleanup(ctx)
