"""验收场景：ERP 的 CC **代录对话**里下令 → 写入计划（开始前卡片）→ 点「写入」→ CC 桌面版在测试机的 Express 界面里录成 → 账本读回 →
恢复基线。真模型（每遍上限见登记的 budget_usd）。代录的对话那一半；服务函数那一半在 `acc.erp.operator.*.express`。

  acc.erp.operator.talk.purchase   一张已提交、还没交进写账队列的现购单，代录对话里说「开始录这个月的单」

**状态：写好未跑**（11-15 晚）。要占测试机桌面（Express 窗口由那台电脑上的 CC 自己操作）；前提同 `erp_operator_express.py`。首跑要盯的：
- 代录对话的写入计划按「这家公司这个月全部待录的单」出：验收公司这个月要只有本次这一张（实验栈刚重置最干净），多了场景会判计划张数不对。
- 单据提交后没交进写账队列，服务端每分钟的巡检会不会在用户点头前把它直写掉：会的话这条记前置条件不满足，要改成从「放票」起（等识别回放）。
- Express 中途弹框带回对话、被人打断后「继续 / 停止」在对话记录里是「允许」类卡片：正常主线里不该出现，出现了场景不替点、判不通过并留证。
"""
import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
Unmet = sys.modules["acc_errors"].Unmet

SAY = {"zh": "开始录这个月的单", "th": "เริ่มบันทึกเอกสารของเดือนนี้"}
TOOLS = ["express_operator_status", "express_operator_control", "pearnly_write_plan", "pearnly_write_start"]
OPERATOR_KEY = "erp:operator:"
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
    if kind != "purchase":
        raise Unmet(f"场景 {ctx.scenario['id']} 在这个模块里没有对应的走法")
    conf = chain.settings()
    book = ctx.targets.ledger(chain.HOST, chain.LEDGER)
    chain.baseline(ctx, book, conf)

    with ctx.step("开一张还没交进写账队列的单", "单据已提交、账本栏是「排队中」、没有写账件"):
        held = chain.site(ctx, "erp_push_held.py", {**conf, "kind": kind, "run": chain.tag(ctx), "leave": "queued"}, timeout=300)
        ctx.require("单据的账本栏与写账件数", ["queued", 0], [held["state"]["ledger_state"], held["jobs"]])
        ctx.facts.update(erp_doc=held["doc"], ledger=chain.LEDGER)

    with ctx.step("开代录对话并下令", "CC 列出这个月待录的单，出写入计划；这时账本里还没有它"):
        talk = driver.open_conversation(ctx.scenario.get("driver", "real"), "erp", "operator", company=held["company"], ledger=chain.LEDGER,
                                        session={"period": time.strftime("%Y-%m")})
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
        stuck = [card for card in record.cards if card["kind"] == "allow"]
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
        base = chain.tuple_of("companion/src/companion/dbf_schema.py", "BACKUP_TABLES")
        after, summary = chain.copy_and_diff(ctx, book, conf, [name for name in base if name not in ("ARTRN", "ARBAL", "ARMAS")]
                                             + ["STLOC", "ARTRNRM", "GLBAL"])
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
        ctx.check("最后说的 Express 单号与账本一致", [], lines.b7_final_matches_ledger(record, r"\b(?:HP|RR)\d{6,7}(?:-\d{3})?\b"), soft=True)
        ctx.check("回复语言跟用户这句话", [], lines.b8_language(record), soft=True)
        ctx.check("不露内部写法", [], lines.b8_no_leak(record, TOOLS), soft=True)


def cleanup(ctx):
    _load("acc_erp_chain", ROOT / "scripts" / "acceptance" / "erp_chain.py").cleanup(ctx)
