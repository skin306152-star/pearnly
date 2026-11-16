"""验收场景（一组）：ERP 赊销单写成 Express 发票（IV）→ 收款三处（网页 / LINE / CC 对话）写成 Express 收款单（RE）→ 账本读回 →
预期以外的表逐字节不变 → 恢复基线。每条场景自己先开一张赊销单再收它（收的就是「刚开的发票」），互不依赖。

编排 B（实验栈挂候选源码 + 测试机2 的 CC 桌面版 + 验收账套）。模型不参与，费用 0。前置：公司已从连接添加并同步过主档
（`acc.erp.company.connect` 做的那一步；没接上时站点脚本会拒绝，记前置条件不满足）。

按场景 id 的后缀分：
  acc.erp.sales.credit.express      新建「ร้าน…」客户的赊销单：IV 表头、未收余额 = 合计、客户主档称谓 / 核心名分栏；ERP 侧未付款不带收款方式
  acc.erp.collection.three          同一张发票三处各收一笔：网页现金、LINE 现金 + 转账（带凭证）、CC（编码照模型复述的写法交进去）
  acc.erp.collection.nbsp           客户编码带不换行空格的那位：开赊销单 → 复述走样的编码交进去 → RE 的客户编码逐字符原样
  acc.erp.collection.web.twice      网页同一个请求号提交两次：只一笔记录、账本只多一张 RE
  acc.erp.collection.line.edit      LINE 出卡后改金额：拿旧卡的版本确认被挡回、拿新版本确认写成，金额是改后的
  acc.erp.collection.line.refused   LINE 草稿没确认时网页先把同一张收清：再确认被拒、草稿作废、账本只有网页那一张 RE

**状态：写好未跑**（11-15 晚）。首跑要校准的：
- 只记账的一行（item_type = service）写成 IV 时动哪些表：允许变化的表取 CC 桌面版自己的写入清单（销售 + 收款两份合起来），
  实际多动了哪张会直接判不通过并列出来；STCRD 预计不多行（不出库）。
- 验收账套有没有转账方式（收款类型里进银行户头的那种）、有没有编码带不换行空格的客户：没有时对应场景记前置条件不满足，
  要在基线里补一位这样的客户 / 一种转账方式后重取基线（先问主会话）。
- 从服务端函数走（via = "api"）：LINE 的逐步对话、网页弹窗、CC 对话与真模型不在这里，见流程用例与真机行。

这条路上 11-15 查出并修掉、这里守住的（对照表在 docs/work/2026-11-15-acceptance-collection.md）：
- 「ร้าน…」客户写进 Express 丢称谓（72516d120）；未付款的单被当成现金已付（6321901ab）；
- 编码里的不换行空格被换成普通空格、写入被拒（75d4a80bb）；刚开的发票没读回就当成「没有这张 / 已收清」（199315fdb）；
- 账套资料缺收款方式报「CC 桌面版需更新」（8281e1f40，等重读好就恢复）；
- 卡片改过后拿旧卡确认照写（collection-loop）；同一张被别处收清后再确认多写一张（写前拒）；连点两次写两张。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
Unmet = sys.modules["acc_errors"].Unmet

PRICE = 1000          # 不含税单价；含税 1,070.00
SHOP = "ร้านเอซีซี"    # 新客户名的开头：称谓「ร้าน」要进称谓栏


def _chain():
    if "acc_erp_chain" not in sys.modules:
        spec = importlib.util.spec_from_file_location("acc_erp_chain", ROOT / "scripts" / "acceptance" / "erp_chain.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["acc_erp_chain"] = module
        spec.loader.exec_module(module)
    return sys.modules["acc_erp_chain"]


def open_sale(ctx, chain, conf, nbsp=False) -> dict:
    """开一张赊销单并等它写成 IV；回站点脚本的结果。新客户名带本轮标记，重跑不撞名。"""
    who = {"nbsp": True} if nbsp else {"buyer": f"{SHOP} {chain.tag(ctx)}"}
    with ctx.step("开赊销单并等写入", "未付款的销售单提交后进写账队列，CC 桌面版写成发票并回执"):
        sale = chain.site(ctx, "erp_sales_post.py", {**conf, "run": chain.tag(ctx), "price": PRICE, **who}, timeout=480)
        state = sale["state"]
        if state.get("ledger_state") == "held":
            raise Unmet(f"赊销单停在等人选：{state.get('ledger_reason')} {state.get('ledger_needed')}")
        ctx.require("写账状态", "written", state.get("ledger_state"))
        ctx.check("回显了 Express 发票号", True, bool(state.get("ledger_docnum")))
        ctx.check("CC 桌面版写后读回", "ok", (sale.get("job") or {}).get("verify_state"))
        chain.only_ledger(ctx, sale["book_paths"])
        # 11-15：取消「已付款」的单详情仍显示现金、未付草稿提交被当成已付
        ctx.check("ERP 里这张单是未付款、已收 0、不带收款方式", ["unpaid", 0.0, ""],
                  [sale["sale"]["payment_status"], chain.money(sale["sale"]["paid_amount"]), sale["sale"]["payment_method"] or ""])
        party = sale.get("party") or {}
        ctx.require("客户挂上了 Express 编码", True, bool(party.get("express_code")))
        ctx.check("客户归这家公司的账本", sale["company"], party.get("express_company"))
        if nbsp:
            ctx.require("这位客户的编码带不换行空格", True, chain.NBSP in party["express_code"])
        ctx.facts.setdefault("docnums", []).append(state.get("ledger_docnum") or "")
        ctx.facts.update(ledger=chain.LEDGER, erp_doc=sale["doc"])
    return sale


def collect(ctx, chain, conf, sale, entry, title, **params) -> dict:
    """录一笔收款并等它有结果；正常写成的顺手核写账状态、读发票时的状态、落点账套。"""
    with ctx.step(title, "收款放行后进写账队列，CC 桌面版写成收款单并回执"):
        out = chain.site(ctx, "erp_collection_post.py",
                         {**conf, "entry": entry, "customer": sale["expected"]["customer_code"], "invoice": sale["state"]["ledger_docnum"],
                          **params}, timeout=600, save=f"collection-{entry}-{params.get('variant') or 'plain'}.json")
        ctx.check("客户编码换回账本里的原样", sale["expected"]["customer_code"], out["customer"]["canonical"])
        # 11-15：发票刚开、电脑还没读回时，不许说成「读好了、没有这张」
        ctx.check("发票没列出来的那几次都不是「已读好」", 0, out["reads"]["ready_without_invoice"])
        ctx.check("能收现金", True, out["methods"]["cash"])
        if params.get("variant") == "refused":
            return out
        record = out["record"]
        ctx.require("收款状态", "written", record["status"])
        ctx.check("回显了 Express 收款单号", True, str(record.get("docnum") or "").startswith("RE"))
        # 收款单是 CC 在写入过程里自己读回核对的（`dbf_receipt._verify`，核不上整单失败），不像采购 / 销售另报 verify 元数据：
        # 写账件成功即读回过，verify_state 只许空或 ok（11-16 首跑按 ok 写错了）
        job = record.get("job") or {}
        ctx.check("CC 桌面版写账件成功（收款单写入时已逐表读回）", ["success", True], [job.get("status"), job.get("verify_state") in ("", "ok")])
        ctx.check("来源", entry, record["source"])
        chain.only_ledger(ctx, record["book_paths"])
        ctx.facts.setdefault("docnums", []).append(record["docnum"])
    return out


def run(ctx):
    chain = _chain()
    conf = chain.settings()
    kind = ctx.scenario["id"].removeprefix("acc.erp.")
    book = ctx.targets.ledger(chain.HOST, chain.LEDGER)
    chain.baseline(ctx, book, conf)
    sale = open_sale(ctx, chain, conf, nbsp=kind == "collection.nbsp")
    invoice, total = sale["state"]["ledger_docnum"], sale["expected"]["totals"]["grand_total"]
    records, received = [], 0.0

    if kind == "collection.three":
        plans = [("web", "网页收款：现金", {"cash": "300"}),
                 ("line", "LINE 收款：现金 + 转账（带凭证）", {"cash": "200", "transfer": "100"}),
                 ("cc", "CC 对话收款：编码照复述的写法交进去", {"cash": "150", "spoken": True})]
        for entry, title, params in plans:
            out = collect(ctx, chain, conf, sale, entry, title, **params)
            received += float(out["record"]["total"])
            records.append((out["record"], {invoice: total - received}))
    elif kind == "collection.nbsp":
        out = collect(ctx, chain, conf, sale, "cc", "CC 对话收款：不换行空格换成普通空格交进去", full=True, spoken=True)
        ctx.check("交进去的写法确实走样了（不是原样）", True, out["customer"]["spoken"] != out["customer"]["canonical"])
        received = float(out["record"]["total"])
        records.append((out["record"], {invoice: 0}))
    elif kind == "collection.web.twice":
        out = collect(ctx, chain, conf, sale, "web", "网页收款：同一个请求号提交两次", full=True, variant="twice")
        ctx.check("第二次提交回的是同一笔，记录只有一条", [True, 1], [out["twice"]["same_record"], out["twice"]["records_for_request"]])
        received = float(out["record"]["total"])
        records.append((out["record"], {invoice: 0}))
    elif kind == "collection.line.edit":
        out = collect(ctx, chain, conf, sale, "line", "LINE 收款：出卡后改金额再确认", cash="300", variant="edit", edit_cash="500")
        edit = out["edit"]
        ctx.check("改完版本加一", edit["version_before"] + 1, edit["version_after"])
        ctx.check("拿旧卡的版本确认被挡回重新出卡，记录还是草稿", ["changed", edit["version_after"], "draft"],
                  [edit["stale_release"], edit["stale_version"], edit["status_after_stale"]])
        ctx.check("写成的是改后的金额", 500.0, chain.money(out["record"]["total"]))
        received = float(out["record"]["total"])
        records.append((out["record"], {invoice: total - received}))
    elif kind == "collection.line.refused":
        out = collect(ctx, chain, conf, sale, "line", "LINE 草稿没确认时网页先收清同一张，再确认", full=True, variant="refused")
        ctx.require("网页那一笔写成了", "written", out["first"]["status"])
        ctx.check("再确认被拒（不放行）", "invalid", out["refused"]["status"])
        ctx.check("拒因是这张发票已不可收", True, out["refused"]["code"] in ("collection.invoice_not_found", "collection.over_collected"))
        ctx.check("草稿没有变成待写入", "draft", out["refused"]["draft_status"])
        ctx.check("草稿作废后是已撤回、没有收款单号", ["withdrawn", ""], [out["record"]["status"], out["record"].get("docnum") or ""])
        received = float(out["first"]["total"])
        records.append((out["first"], {invoice: 0}))
        ctx.facts.setdefault("docnums", []).append(out["first"]["docnum"])
    elif kind != "sales.credit.express":
        raise Unmet(f"场景 {ctx.scenario['id']} 在这个模块里没有对应的走法")

    with ctx.step("账本读回", "发票与收款单按单号读得到、字段与单据一致；预期以外的表逐字节不变"):
        after, summary = chain.copy_and_diff(ctx, book, conf, chain.chain_tables() if records else chain.sales_tables())
        chain.invoice_readback(ctx, book, after, sale, collected=bool(records))
        ctx.check("ARTRN 多的行数 = 一张发票 + 收款单数", 1 + len(records), chain.grew(summary, "ARTRN"))
        ctx.check("收款明细多的行数 = 收款单数（每张只挂这一张发票）", len(records), chain.grew(summary, "ARRCPIT") or 0)
        ctx.check("凭证多的张数 = 一张发票 + 收款单数", 1 + len(records), chain.grew(summary, "GLJNL"))
        ctx.check("客户主档多的行数", 1 if sale["expected"]["new_customer"] else 0, chain.grew(summary, "ARMAS") or 0)
        for record, remaining in records:
            chain.receipt_readback(ctx, book, after, record, remaining)
        if records:
            chain.invoice_balance(ctx, book, after, invoice, total, received)


def cleanup(ctx):
    _chain().cleanup(ctx)
