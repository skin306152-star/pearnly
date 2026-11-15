"""验收场景：ERP 的 CC 对话（直写）里把一张停下的采购 / 销售单再写进 Express → 写入计划 → 点「写入」→ CC 桌面版写成 → 账本读回 →
恢复基线。真模型（每遍上限见登记的 budget_usd）。

  acc.erp.cc.push.purchase   一张停在「没写进 Express」的现购单，对话里说「把 <单号> 写进 Express」
  acc.erp.cc.push.sales      一张停下的赊销单（新建「ร้าน…」客户），同上

产品现状（11-15 读 main 得出，主会话已确认按这个形状写）：ERP 的直写对话不识别附件；单据一提交就自动排写账；对话里的「推送」
（`erp_push_documents`）只等于单据页的「再写一次」，只推写入计划里已存在、停下或失败的单。所以这条的起点是一张停下的单
（站点脚本 `erp_push_held.py` 造），不是「放票」。引导式（P2 的 ERP 采购 / 销售引导）合入后要按新步骤表重写起点。

编排 B（接了真模型的实验栈 + 测试机的 CC 桌面版 + 验收账套）。**状态：写好未跑**（11-15 晚）。
对话里说的话：「把采购单 <系统单号> 写进 Express」/ 泰文同义句，按登记的 lang 选。
"""
import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
Unmet = sys.modules["acc_errors"].Unmet

SAY = {"zh": {"purchase": "把采购单 {number} 写进 Express", "sales": "把销售单 {number} 写进 Express"},
       "th": {"purchase": "บันทึกใบซื้อ {number} เข้า Express", "sales": "บันทึกใบขาย {number} เข้า Express"}}
TOOLS = ["erp_push_documents", "erp_list_documents", "erp_push_results", "pearnly_write_plan", "pearnly_write_start"]
DIRECT_KEYS = {"purchase": "erp:purchase:", "sales": "erp:sales:"}


def _load(key: str, path: Path):
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, path)
        sys.modules[key] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[key])
    return sys.modules[key]


def run(ctx):
    chain = _load("acc_erp_chain", ROOT / "scripts" / "acceptance" / "erp_chain.py")
    driver, lines = _load("cc_driver", HERE.parent / "cc_driver.py"), _load("cc_lines", HERE.parent / "cc_lines.py")
    direct = _load("acc_scenario_cowork_cc_direct", HERE / "cowork_cc_direct.py")
    kind, lang = ctx.scenario["id"].rsplit(".", 1)[-1], ctx.scenario.get("lang", "zh")
    if kind not in ("purchase", "sales"):
        raise Unmet(f"场景 {ctx.scenario['id']} 在这个模块里没有对应的走法")
    conf = chain.settings()
    book = ctx.targets.ledger(chain.HOST, chain.LEDGER)
    chain.baseline(ctx, book, conf)

    with ctx.step("造一张停下的单", "单据已提交、账本栏是「失败」、Express 里还没有它"):
        held = chain.site(ctx, "erp_push_held.py", {**conf, "kind": kind, "run": chain.tag(ctx)}, timeout=300)
        if held.get("taken"):
            raise Unmet("那一件写账被电脑先领走了，没能留成停下的单")
        ctx.require("单据的账本栏", ["failed", ""], [held["state"]["ledger_state"], held["state"]["ledger_docnum"] or ""])
        ctx.require("账套还是基线（这张单没写进去）", True, book.verify(conf["baseline"])["ok"])
        ctx.facts.update(erp_doc=held["doc"], ledger=chain.LEDGER)

    with ctx.step("开对话并说一句", "CC 找到这张停下的单，复述后出写入计划；这时账本里还没有它"):
        talk = driver.open_conversation(ctx.scenario.get("driver", "real"), "erp", "direct", company=held["company"], ledger=chain.LEDGER)
        ctx.on_failure("conversation", talk.evidence)
        ctx.facts.update(session=talk.session_id, site=getattr(talk, "site", ""))
        talk.say(SAY.get(lang, SAY["zh"])[kind].format(number=held["number"]))
        record, asked = direct.reach_plan(talk)
        ctx.spend(talk.cost() - ctx.spent, "找单、复述")
        ctx.save("before-write.record.json", record.__dict__)
        ctx.save("cards-before-plan.json", asked)
        waiting = [card for card in record.cards if card["kind"] == "plan" and card.get("status") == "awaiting"]
        ctx.require("出了一张等用户点头的写入计划", 1, len(waiting))
        ctx.check("写入计划里是一张单", 1, waiting[0].get("count"))
        ctx.require("点头之前账套逐文件没有变化", True, book.verify(conf["baseline"])["ok"])

    with ctx.step("点写入", "CC 桌面版写进验收账套并回执"):
        ctx.require("页面上有「写入」可点", True, talk.click("plan", "write"))
        clicked = time.time()
        talk.wait_idle(300)
        ctx.spend(talk.cost() - ctx.spent, "写入")
        done = chain.site(ctx, "erp_doc_state.py", {"doctype": held["doctype"], "doc": held["doc"]}, timeout=420)
        state, job = done["state"], done.get("job") or {}
        if state.get("ledger_state") == "held":
            raise Unmet(f"推送后停在等人处理：{state.get('ledger_reason')} {state.get('ledger_needed') or ''}")
        ctx.require("写账状态", "written", state.get("ledger_state"))
        ctx.check("回显了 Express 单号", True, bool(state.get("ledger_docnum")))
        ctx.check("CC 桌面版写后读回", "ok", job.get("verify_state"))
        ctx.check("这一件是直写（不是代录）", True, str(job.get("request_key") or "").startswith(DIRECT_KEYS[kind]))
        ctx.check("写账件比造单时只多一件（撤掉的那件 + 这一件）", held["jobs"] + 1, done["jobs"])
        chain.only_ledger(ctx, done["book_paths"])
        talk.wait_idle(120)
        ctx.spend(talk.cost() - ctx.spent, "回执")
        ctx.facts.update(docnums=[state["ledger_docnum"]])

    with ctx.step("账本读回", "账套里按单号读得到这张单，与网页提交直写的同一套断言；预期以外的表逐字节不变"):
        docnum, expected = state["ledger_docnum"], held["expected"]
        if kind == "sales":
            expected["customer_code"] = (done.get("party") or {}).get("express_code") or ""
            after, summary = chain.copy_and_diff(ctx, book, conf, chain.sales_tables())
            chain.invoice_readback(ctx, book, after, {"state": state, "expected": expected})
            ctx.check("ARTRN 只多一行", 1, chain.grew(summary, "ARTRN"))
        else:
            base = chain.tuple_of("companion/src/companion/dbf_schema.py", "BACKUP_TABLES")
            after, summary = chain.copy_and_diff(ctx, book, conf, [name for name in base if name not in ("ARTRN", "ARBAL", "ARMAS")]
                                                 + ["STLOC", "ARTRNRM", "GLBAL"])
            rows = chain.rows(book, after, "APTRN", f"DOCNUM={docnum}")
            ctx.save("aptrn-row.json", rows)
            ctx.require("APTRN 里这张单恰好一行", 1, len(rows))
            ctx.check("供应商编码", expected["supplier_code"], str(rows[0].get("SUPCOD") or "").strip())
            ctx.check("含税合计", chain.money(expected["totals"]["grand_total"]), chain.money(rows[0].get("NETAMT")))
            ctx.check("APTRN 只多一行", 1, chain.grew(summary, "APTRN"))
        ctx.check("凭证多一张", 1, chain.grew(summary, "GLJNL"))

    with ctx.step("核底线", "没点头不写、只写一张、最后说的与账本一致"):
        record = talk.record()
        record.writes = [{"at": max(clicked, time.time()), "docnum": docnum, "ledger": chain.LEDGER, "ticket": held["doc"]}]
        ctx.save("final.record.json", record.__dict__)
        ctx.check("没点头不写账", [], lines.b1_consent_before_write(record))
        ctx.check("没有重复写、停了不再写", [], lines.b6_stop_and_duplicates(record))
        ctx.check("最后说的 Express 单号与账本一致", [], lines.b7_final_matches_ledger(record, r"\b(?:HP|RR|IV|HS)\d{6,7}(?:-\d{3})?\b"))
        ctx.check("回复语言跟用户这句话", [], lines.b8_language(record), soft=True)
        ctx.check("不露内部写法", [], lines.b8_no_leak(record, TOOLS), soft=True)


def cleanup(ctx):
    _load("acc_erp_chain", ROOT / "scripts" / "acceptance" / "erp_chain.py").cleanup(ctx)
