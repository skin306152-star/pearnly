"""验收场景：ERP 的 CC 对话（直写）里录一笔收款 → 出写入计划 → 点「写入」→ CC 桌面版写成 Express 收款单 → 账本读回 → 恢复基线。
真模型（每遍上限见登记的 budget_usd）。

  acc.erp.cc.collection        刚开的一张赊销发票，对话里说「某客户付了某发票现金多少」：只说这一句就该走到写入计划
  acc.erp.cc.collection.nbsp   客户编码带不换行空格（对话里打出来的是普通空格）：收款单的客户编码逐字符原样

守的是 11-15 两条挡上线缺陷的**对话那一半**（服务函数那一半在 `acc.erp.collection.*`）：
- 编码带不换行空格的客户写入被拒，CC 让用户「先在 Express 里建立这个客户」；
- 刚开的发票还没读回，CC 直接说「已经收清 / 可能已收过款」。

编排 B（实验栈挂候选源码并接了真模型 + 测试机的 CC 桌面版 + 验收账套）。**状态：写好未跑**（11-15 晚）。
- Mac 实验栈没接模型：这条在接了模型的实验栈上跑（`PEARNLY_ACC_REAL_*`，见 cc_driver.RealConversation），站点脚本走 `PEARNLY_ACC_SITE_TARGET`，
  两边要指同一个站点。
- 引导式（`pearnly/cc-guided-core` 等）合入后，对话里可能先出引导卡：`reach_plan` 会按每题第一项代答并留证，首跑时看问了什么。
- 对话里说的话：「客户 <编码> 今天付了发票 <单号> 现金 <金额>」（中文）/ 泰文同义句，按登记的 lang 选。
"""
import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
Unmet = sys.modules["acc_errors"].Unmet

SAY = {"zh": "客户 {customer} 今天付了发票 {invoice}，现金 {amount}", "th": "ลูกค้า {customer} จ่ายใบกำกับ {invoice} วันนี้ เงินสด {amount} บาท"}
TOOLS = ["erp_collection_draft", "erp_collection_push", "erp_collection_invoices", "erp_collection_customers", "pearnly_write_plan",
         "pearnly_write_start"]
CASH = "400"   # 本次收的现金（发票含税 1,070.00，收一部分）


def _load(key: str, path: Path):
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, path)
        sys.modules[key] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[key])
    return sys.modules[key]


def run(ctx):
    chain = _load("acc_erp_chain", ROOT / "scripts" / "acceptance" / "erp_chain.py")
    driver, lines = _load("cc_driver", HERE.parent / "cc_driver.py"), _load("cc_lines", HERE.parent / "cc_lines.py")
    sales = _load("acc_scenario_erp_collection_express", HERE / "erp_collection_express.py")
    direct = _load("acc_scenario_cowork_cc_direct", HERE / "cowork_cc_direct.py")
    conf, nbsp, lang = chain.settings(), ctx.scenario["id"].endswith(".nbsp"), ctx.scenario.get("lang", "zh")
    book = ctx.targets.ledger(chain.HOST, chain.LEDGER)
    chain.baseline(ctx, book, conf)
    sale = sales.open_sale(ctx, chain, conf, nbsp=nbsp)
    invoice, total, customer = sale["state"]["ledger_docnum"], sale["expected"]["totals"]["grand_total"], sale["expected"]["customer_code"]

    with ctx.step("开对话", "建在接着验收账套的那家公司，直写方式"):
        ctx.require("拷出开完发票的现场（点头之前拿它比）", True, book.copy(chain.after_dir(ctx, "-sold")).get("ok"))
        talk = driver.open_conversation(ctx.scenario.get("driver", "real"), "erp", "direct", company=sale["company"], ledger=chain.LEDGER)
        ctx.on_failure("conversation", talk.evidence)
        ctx.facts.update(session=talk.session_id, site=getattr(talk, "site", ""))

    with ctx.step("说一句收款", "CC 找到客户与这张刚开的发票，复述后出写入计划；这时账本里还没有收款单"):
        spoken = customer.replace(chain.NBSP, " ")   # 人在对话框里打的是普通空格
        talk.say(SAY.get(lang, SAY["zh"]).format(customer=spoken, invoice=invoice, amount=CASH))
        record, asked = direct.reach_plan(talk)
        ctx.spend(talk.cost() - ctx.spent, "找客户、读发票、建草稿")
        ctx.save("before-write.record.json", record.__dict__)
        ctx.save("cards-before-plan.json", asked)
        waiting = [card for card in record.cards if card["kind"] == "plan" and card.get("status") == "awaiting"]
        ctx.require("出了一张等用户点头的写入计划", 1, len(waiting))
        ctx.check("写入计划里是一笔", 1, waiting[0].get("count"))
        # 11-15：刚开的发票没读回就说「已收清」，要用户回一句「刚开的，再读一次」才往下走
        ctx.check("只说了这一句就走到写入计划（没有让用户再催一次）", 1, len([turn for turn in record.turns if turn["role"] == "user"]), soft=True)
        ctx.check("建了收款草稿", True, any(tool["name"] == "erp_collection_draft" for tool in record.tools))
        before = chain.after_dir(ctx, "-before-consent")
        ctx.require("拷出点头之前的现场", True, book.copy(before).get("ok"))
        untouched = book.diff(chain.after_dir(ctx, "-sold"), before, allow=[], out=f"{before}.diff.json")
        ctx.require("点头之前账套逐文件没有变化", [], untouched.get("stray"))

    with ctx.step("点写入", "CC 桌面版写进验收账套并回执"):
        ctx.require("页面上有「写入」可点", True, talk.click("plan", "write"))
        clicked = time.time()
        talk.wait_idle(300)
        ctx.spend(talk.cost() - ctx.spent, "写入")
        found = chain.site(ctx, "erp_collection_lookup.py", {"invoice": invoice, "source": "cc"}, timeout=420)
        mine = [item for item in found["records"] if item["source"] == "cc"]
        ctx.require("这张发票上 CC 建的收款恰好一笔", 1, len(mine))
        receipt = mine[0]
        # 11-15：编码里的不换行空格被换成普通空格，CC 桌面版拒收（CUSTOMER_NOT_IN_ARMAS）
        ctx.require("收款状态", "written", receipt["status"])
        ctx.check("回显了 Express 收款单号", True, str(receipt.get("docnum") or "").startswith("RE"))
        ctx.check("草稿里的客户编码是账本里的原样", customer, receipt["customer"])
        ctx.check("收的是说的那个数", chain.money(CASH), chain.money(receipt["total"]))
        # 收款单写入时 CC 已逐表读回（`dbf_receipt._verify`），不另报 verify 元数据：写账件成功、verify_state 空或 ok（同 erp_collection_express）
        job = receipt.get("job") or {}
        ctx.check("CC 桌面版写账件成功（收款单写入时已逐表读回）", ["success", True], [job.get("status"), job.get("verify_state") in ("", "ok")])
        ctx.check("放行记的是对话里的那次同意", True, str(receipt.get("consent_ref") or "").startswith("cc:"))
        chain.only_ledger(ctx, receipt["book_paths"])
        talk.wait_idle(120)
        ctx.spend(talk.cost() - ctx.spent, "回执")
        ctx.facts.setdefault("docnums", []).append(receipt["docnum"])

    with ctx.step("账本读回", "发票与收款单按单号读得到、字段一致；预期以外的表逐字节不变"):
        after, summary = chain.copy_and_diff(ctx, book, conf, chain.chain_tables())
        chain.invoice_readback(ctx, book, after, sale, collected=True)
        ctx.check("ARTRN 多的行数 = 一张发票 + 一张收款单", 2, chain.grew(summary, "ARTRN"))
        chain.receipt_readback(ctx, book, after, receipt, {invoice: total - float(CASH)})
        chain.invoice_balance(ctx, book, after, invoice, total, float(CASH))

    with ctx.step("核底线", "没点头不写、只写一笔、最后说的与账本一致"):
        record = talk.record()
        record.writes = [{"at": max(clicked, time.time()), "docnum": receipt["docnum"], "ledger": chain.LEDGER, "ticket": invoice}]
        ctx.save("final.record.json", record.__dict__)
        ctx.check("没点头不写账", [], lines.b1_consent_before_write(record))
        ctx.check("没有重复写、停了不再写", [], lines.b6_stop_and_duplicates(record))
        # 只按收款单号核（回复里照例会提到被收的发票号，那不是这次写成的单）
        ctx.check("最后说的收款单号与账本一致", [], lines.b7_final_matches_ledger(record, r"\bRE\d{7}\b"))
        said = " ".join(turn["text"] for turn in record.turns if turn["role"] != "user")
        ctx.check("没有叫用户去 Express 里建客户", False, "Express 里建" in said or "สร้างลูกค้า" in said, soft=True)
        ctx.check("回复语言跟用户这句话", [], lines.b8_language(record), soft=True)
        ctx.check("不露内部写法", [], lines.b8_no_leak(record, TOOLS), soft=True)


def cleanup(ctx):
    _load("acc_erp_chain", ROOT / "scripts" / "acceptance" / "erp_chain.py").cleanup(ctx)
