"""验收场景：ERP 的 CC **代录对话**里下令 → 写入计划（开始前卡片）→ 点「写入」→ CC 桌面版在测试机的 Express 界面里录成 → 账本读回 →
恢复基线。真模型（每遍上限见登记的 budget_usd）。代录的对话那一半；服务函数那一半在 `acc.erp.operator.*.express`。

  acc.erp.operator.talk.purchase   一张已提交、还没交进写账队列的现购单，代录对话里说「开始录这个月的单」
  acc.erp.operator.talk.sales      一张已提交、还没交进写账队列的赊销单（新建「ร้าน…」客户、只记账的一行），同上（引导总表格 ERP-O-S-L）

**状态**：purchase 11-16 晚跑过两次（第 2 次只差「最后的回复报单号」，产品缺陷）；sales 写好未跑（第 7 批，11-17）。要占测试机桌面（Express 窗口由那台电脑上的 CC 自己操作）；前提同 `erp_operator_express.py`。首跑要盯的：
- 代录对话的写入计划按「这家公司这个月全部待录的单」出：验收公司这个月要只有本次这一张。11-16 晚首跑第 1 次站点上有前几格残留的
  待写单（计划 4 张、两张选择卡），所以开对话前先用只读站点脚本 `erp_operator_sources.py`（产品的 `operator.sources`，计划取的就是它）
  核一次，不是只有这一张就记前置条件不满足并列出是哪些。
- 销售那条的读回同 `acc.erp.operator.sales.express`：客户编码按 ERP 客户挂回的 Express 编码核——首跑缺陷 2（代录回执的码不挂回 ERP 档）
  没修好之前这条会不通过在「发票的客户编码」。
- 单据提交后没交进写账队列，服务端每分钟的巡检会不会在用户点头前把它直写掉：会的话这条记前置条件不满足，要改成从「放票」起（等识别回放）。
- Express 中途弹框带回对话、被人打断后「继续 / 停止」在对话记录里是「允许」类卡片：正常主线里不该出现，出现了场景不替点、判不通过并留证。
"""
import importlib.util
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
Unmet = sys.modules["acc_errors"].Unmet

SAY = {"zh": "开始录这个月的单", "th": "เริ่มบันทึกเอกสารของเดือนนี้"}
TOOLS = ["express_operator_status", "express_operator_control", "pearnly_write_plan", "pearnly_write_start"]
OPERATOR_KEY = "erp:operator:"
DOCNUM = r"\b(?:HP|RR|IV|HS)\d{6,7}(?:-\d{3})?\b"   # 现购 / 赊购 / 赊销 / 现销的 Express 单号
ENTRY_WAIT = 900   # 界面录入比直写慢，还要等电脑空闲


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

    with ctx.step("开一张还没交进写账队列的单", "单据已提交、账本栏是「排队中」、没有写账件"):
        held = chain.site(ctx, "erp_push_held.py", {**conf, "kind": kind, "run": chain.tag(ctx), "leave": "queued"}, timeout=300)
        ctx.require("单据的账本栏与写账件数", ["queued", 0], [held["state"]["ledger_state"], held["jobs"]])
        ctx.facts.update(erp_doc=held["doc"], ledger=chain.LEDGER)

    period = time.strftime("%Y-%m")
    with ctx.step("核本月待录", "这家公司这个月待录的单只有本格刚造的这一张（代录的写入计划按这一份出）"):
        user = driver.real_env("USER", "erp") or conf["user"]
        found = chain.site(ctx, "erp_operator_sources.py", {"user": user, "company": held["company"], "period": period}, timeout=180)
        others = [row for row in found["documents"] if row.get("doc_id") != held["doc_id"]]
        if others:
            raise Unmet(f"这家公司 {period} 还有别的待录单 {len(others)} 张（先作废或写掉再跑）：" +
                        "、".join(f"{row.get('doc_id')}（{row.get('ref')}，{row.get('date')}）" for row in others[:10]))
        ctx.require("待录的单里有本格造的这一张", [held["doc_id"]], [row.get("doc_id") for row in found["documents"]])

    with ctx.step("开代录对话并下令", "CC 列出这个月待录的单，出写入计划；这时账本里还没有它"):
        talk = driver.open_conversation(ctx.scenario.get("driver", "real"), "erp", "operator", company=held["company"], ledger=chain.LEDGER,
                                        session={"period": period})
        ctx.on_failure("conversation", talk.evidence)
        ctx.facts.update(session=talk.session_id, site=getattr(talk, "site", ""))
        talk.say(SAY.get(lang, SAY["zh"]))
        record, asked = direct.reach_plan(talk)
        ctx.spend(talk.cost() - ctx.spent, "列待录的单、出计划")
        ctx.save("before-write.record.json", record.__dict__)
        ctx.save("cards-before-plan.json", asked)
        waiting = [card for card in record.cards if card["kind"] == "plan" and card.get("status") == "awaiting"]
        ctx.require("出了一张等用户点头的写入计划", 1, len(waiting))
        ctx.check("写入计划里只有本次这一张", 1, waiting[0].get("count"))
        ctx.require("点头之前账套逐文件没有变化", True, book.verify(conf["baseline"])["ok"])

    with ctx.step("点写入", "单交给代录；CC 桌面版在 Express 界面里录成并回执"):
        ctx.require("页面上有「写入」可点", True, talk.click("plan", "write"))
        clicked = time.time()
        done = chain.site(ctx, "erp_doc_state.py", {"doctype": held["doctype"], "doc": held["doc"], "wait": ENTRY_WAIT}, timeout=ENTRY_WAIT + 120)
        state, job = done["state"], done.get("job") or {}
        talk.wait_idle(300)
        ctx.spend(talk.cost() - ctx.spent, "代录与回执")
        record = talk.record()
        stuck = [card for card in record.cards if card["kind"] == "allow" and card.get("topic") != "pearnly_guide"]   # 写前引导卡是开始前答的选择，不是被打断
        ctx.check("正常主线里没有要人处理的卡片（弹框带回、被打断）", [], [card.get("topic") for card in stuck])
        if state.get("ledger_state") == "held":
            raise Unmet(f"代录停在等人处理：{state.get('ledger_reason') or job.get('reason')}")
        ctx.require("写账状态", "written", state.get("ledger_state"))
        ctx.check("回显了 Express 单号", True, bool(state.get("ledger_docnum")))
        ctx.check("这件写账是代录交的", True, str(job.get("request_key") or "").startswith(OPERATOR_KEY))
        ctx.check("这张单名下只有这一件写账", 1, done["jobs"])
        chain.only_ledger(ctx, done["book_paths"])
        ctx.facts.update(docnums=[state["ledger_docnum"]])

    with ctx.step("账本读回", "账套里按单号读得到这张单，与直写同一套断言；预期以外的表逐字节不变"):
        docnum, expected = state["ledger_docnum"], held["expected"]
        if kind == "sales":
            expected["customer_code"] = (done.get("party") or {}).get("express_code") or ""
            # 首跑缺陷 2：代录回执的码放在 meta.operator.masters、服务端不挂回 ERP 客户——挂不上就别往下按空编码找客户主档
            ctx.require("新客户在 ERP 档上挂回了 Express 客户编码", True, bool(expected["customer_code"]))
            after, summary = chain.copy_and_diff(ctx, book, conf, chain.sales_tables())
            chain.invoice_readback(ctx, book, after, {"state": state, "expected": expected})
            ctx.check("ARTRN 只多一行", 1, chain.grew(summary, "ARTRN"))
        else:
            after, summary = chain.copy_and_diff(ctx, book, conf, chain.purchase_tables())
            rows = chain.rows(book, after, "APTRN", f"DOCNUM={docnum}")
            ctx.save("aptrn-row.json", rows)
            ctx.require("APTRN 里这张单恰好一行", 1, len(rows))
            ctx.check("供应商编码", expected["supplier_code"], str(rows[0].get("SUPCOD") or "").strip())
            ctx.check("含税合计", chain.money(expected["totals"]["grand_total"]), chain.money(rows[0].get("NETAMT")))
            ctx.check("APTRN 只多一行", 1, chain.grew(summary, "APTRN"))

    with ctx.step("核底线", "没点头不写、只写一张、最后说的与账本一致"):
        record.writes = [{"at": max(clicked, time.time()), "docnum": docnum, "ledger": chain.LEDGER, "ticket": held["doc"]}]
        ctx.save("final.record.json", record.__dict__)
        ctx.check("没点头不写账", [], lines.b1_consent_before_write(record))
        ctx.check("没有重复写、停了不再写", [], lines.b6_stop_and_duplicates(record))
        ctx.check("最后说的 Express 单号与账本一致", [], lines.b7_final_matches_ledger(record, DOCNUM), soft=True)
        ctx.check("回复语言跟用户这句话", [], lines.b8_language(record), soft=True)
        ctx.check("不露内部写法", [], lines.b8_no_leak(record, TOOLS), soft=True)


def cleanup(ctx):
    _load("acc_erp_chain", ROOT / "scripts" / "acceptance" / "erp_chain.py").cleanup(ctx)
