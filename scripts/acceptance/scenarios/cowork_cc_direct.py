"""验收场景（示范 2，串联）：Cowork 的 CC 对话直写——放一张采购票 → 识别 → 复述出写入计划 → 点「写入」→ CC 桌面版写进验收账套
→ 账本读回（恰好多一张、供应商与金额对得上样票）、预期以外的表逐字节不变 → 底线 ①⑥⑦ → 恢复基线。
就是「按现版本从 CC 入口真写验证过 0 项」的那条链；跑不通本身就是要报的结果。

**状态（11-14）：写全了、没跑过、没登记**（开跑前置清单见共享笔记「验收体系-示范2-开跑清单-11-14.md」）。依赖：真模型驱动连过一次真站点（cc_driver.RealConversation 只有离线单测）、
集成分支的直写修复、验收公司在站点里接着 TEST2。登记时：class = "chain"、env = "B"、driver = "real"、
rows = ["cowork.cc.tool.cowork_push_documents", "cowork.cc.tool.pearnly_write_start"]、
resources = ["testpc2", "ledger:69EXP/TEST2"]（站点实验栈另加）、needs_server = true、budget_usd = 1.0、repeat = 1（入库与每周 3）。

参数（环境变量；都不是密钥）：
  PEARNLY_ACC_REAL_BASE / _SITE / _USER / _TARGET   真模型驱动连哪个站点、用谁登录（见 cc_driver.RealConversation）
  PEARNLY_ACC_COWORK_COMPANY   对话建在哪家公司（站点里接着验收账套的那家）
  PEARNLY_ACC_BASELINE         基线快照目录（PEARNLY_ACC_LEDGER_HOST 那台机器的本机盘）
  PEARNLY_ACC_LEDGER / PEARNLY_ACC_LEDGER_HOST   验收账套与做快照 / 还原的机器（默认 69EXP\\TEST2、测试机2）
  PEARNLY_ACC_SAMPLE           放哪张样票（默认 purchase-a1；tests/acceptance/samples.toml）
"""
import importlib.util
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
Unmet = sys.modules["acc_errors"].Unmet
Infra = sys.modules["acc_errors"].Infra
LEDGER = os.environ.get("PEARNLY_ACC_LEDGER", r"\\accserver\ACCOUNT\69EXP\TEST2")
HOST = os.environ.get("PEARNLY_ACC_LEDGER_HOST", "testpc2")
TOOLS = ["cowork_push_documents", "pearnly_write_plan", "pearnly_write_start", "cowork_recognize_attachments"]
SAY = {"th": "บันทึกให้หน่อย", "zh": "录一下"}
LAND_WAIT = 300   # 点了「写入」之后最多等这么久看到账套变化（CC 桌面版领单、写入、回执）


def _lib(name: str):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE.parent / f"{name}.py")
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


def allowed_tables() -> list[str]:
    """采购直写允许变化的表：与示范 1 同一份清单（读 CC 桌面版自己的写入清单），首跑时按实际校准。"""
    return _lib_scenario("erp_purchase_express").allowed_tables()


def _lib_scenario(name: str):
    key = f"acc_scenario_{name}"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / f"{name}.py")
        sys.modules[key] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[key])
    return sys.modules[key]


def settings() -> dict:
    need = {"company": "PEARNLY_ACC_COWORK_COMPANY", "baseline": "PEARNLY_ACC_BASELINE"}
    values = {key: os.environ.get(name, "") for key, name in need.items()}
    missing = [need[key] for key, value in values.items() if not value]
    if missing:
        raise Unmet("没配：" + "、".join(missing) + "（见场景文件头）")
    return {**values, "sample": os.environ.get("PEARNLY_ACC_SAMPLE", "purchase-a1")}


def money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


PLAN_WAIT, ASK_LIMIT = 420, 4   # 放票后最多等这么久出写入计划；出计划前最多替用户答这么多张提问卡


def reach_plan(talk, seconds: int = PLAN_WAIT, limit: int = ASK_LIMIT, sleep=time.sleep, clock=time.monotonic):
    """等到出了等用户点头的写入计划 → (对话记录, 中间答过的提问卡)。等的是值，不是「这一轮停了」：
    识别超过站点的等待时限会挂到后台，这一轮看着停了、过一会儿自己接着走——接着等；
    CC 先出提问卡（开始前的选择）就照卡片默认项（每题第一个选项）作答并记下来，最多 limit 张；
    要用户点「允许」的卡片不替点（那是写操作的把关），留给场景判不通过。到时限还没计划就把手上的记录交回，由场景的断言说哪里不对。"""
    deadline, asked = clock() + seconds, []
    while True:
        try:
            talk.wait_idle(60)
        except Infra:
            pass   # 还在跑：接着等到总时限
        record = talk.record()
        if any(card["kind"] == "plan" and card.get("status") == "awaiting" for card in record.cards):
            return record, asked
        waiting = [card for card in record.cards if card["kind"] == "ask" and card.get("answered_at") is None]
        if waiting and len(asked) < limit and talk.click("ask", []):
            asked.append({"topic": waiting[-1].get("topic"), "headers": waiting[-1].get("headers"), "answered": "每题第一个选项"})
            continue
        if clock() > deadline:
            return record, asked
        sleep(10)


def run(ctx):
    lines, samples = _lib("cc_lines"), _lib("samples")
    conf = settings()
    sample = samples.registry()["samples"].get(conf["sample"])
    if sample is None:
        raise Unmet(f"样票库里没有 {conf['sample']}")
    expect, lang = sample["expect"], ctx.scenario.get("lang", "th")
    book = ctx.targets.ledger(HOST, LEDGER)
    ctx.after = f"{ctx.targets.windows(HOST).work}/runs/{ctx.folder.parent.name}-{ctx.scenario['id']}-{ctx.attempt}"

    with ctx.step("基线", "账套没人开着，内容与基线快照逐文件一致"):
        ctx.require("没有别的程序开着账套", [], book.probe().get("busy"))
        state = book.verify(conf["baseline"])
        if state.get("refused"):
            raise Unmet(f"基线快照不可用：{state['refused']}")
        if not state["ok"]:
            restored = book.restore(conf["baseline"])
            ctx.save("baseline-restore.json", restored)
            ctx.require("恢复到基线后逐文件一致", True, restored["ok"])
        ctx.check("账套 = 基线", True, True)

    with ctx.step("开对话", "建在接着验收账套的那家公司，直写方式"):
        talk = _lib("cc_driver").open_conversation(ctx.scenario.get("driver", "real"), "cowork", "direct", company=conf["company"], ledger=LEDGER)
        ctx.on_failure("conversation", talk.evidence)   # 不通过时留服务端原值：会话事件原文，对话不删
        ctx.facts.update(session=talk.session_id, site=getattr(talk, "site", ""))

    with ctx.step("放票并说一句", "识别出这张采购票，复述后出写入计划；这时账本里还没有新单"):
        talk.attach([conf["sample"]])
        talk.say(SAY.get(lang, SAY["th"]))
        record, asked = reach_plan(talk)
        ctx.spend(talk.cost() - ctx.spent, "识别与复述")
        ctx.save("before-write.record.json", record.__dict__)
        ctx.save("cards-before-plan.json", asked)   # CC 出计划前问过什么、按默认项答了什么：正常主线里问得多不多看这里
        stuck = [card for card in record.cards if card["kind"] == "allow" and card.get("answered_at") is None]
        ctx.require("出计划之前没有要用户点「允许」的卡片挂着", [], [card.get("topic") for card in stuck])
        waiting = [card for card in record.cards if card["kind"] == "plan" and card.get("status") == "awaiting"]
        ctx.require("出了一张等用户点头的写入计划", 1, len(waiting))
        ctx.check("写入计划里是一张单", 1, waiting[0].get("count"))
        ctx.require("点头之前账套逐文件没有变化", True, book.verify(conf["baseline"])["ok"])   # 底线 ① 的账本那一半

    with ctx.step("点写入", "CC 桌面版写进验收账套并回执"):
        ctx.require("页面上有「写入」可点", True, talk.click("plan", "write"))
        clicked = time.time()
        talk.wait_idle(300)
        ctx.spend(talk.cost() - ctx.spent, "写入")
        deadline, landed = time.monotonic() + LAND_WAIT, False
        while not landed and time.monotonic() < deadline:   # 等的是值：账套与基线不再一致、且没有程序还开着它
            landed = not book.verify(conf["baseline"])["ok"] and not book.probe().get("busy")
            if not landed:
                time.sleep(15)
        ctx.require(f"{LAND_WAIT} 秒内账套里出现了变化", True, landed)
        talk.wait_idle(120)
        ctx.spend(talk.cost() - ctx.spent, "回执")

    with ctx.step("账本读回", "恰好多一张采购单，供应商与金额对得上样票；预期以外的表逐字节不变"):
        ctx.require("拷出写后的现场", True, book.copy(ctx.after).get("ok"))
        summary = book.diff(conf["baseline"], ctx.after, allow=allowed_tables(), out=f"{ctx.after}.diff.json", show=["APTRN"])
        ctx.save("ledger-diff-summary.json", summary)
        ctx.check("预期以外的文件没有变化", [], summary.get("stray"))
        added = [row["added"] for row in summary.get("shown", {}).get("APTRN", []) if "added" in row and not row.get("deleted")]
        changed = [row for row in summary.get("shown", {}).get("APTRN", []) if "added" not in row]
        ctx.check("APTRN 原有的行没有被改动", [], changed)
        ctx.require("APTRN 恰好多一行", 1, len(added))
        row = added[0]
        ctx.check("供应商编码", expect["supplier_code"], row.get("SUPCOD"))
        ctx.check("税前金额", money(expect["amount"]), money(row.get("AMOUNT")))
        ctx.check("税额", money(expect["vat"]), money(row.get("VATAMT")))
        ctx.check("含税合计", money(expect["total"]), money(row.get("NETAMT")))
        ctx.check("票号记在参考号里", sample["number"], row.get("REFNUM"))
        ctx.facts.update(docnums=[row.get("DOCNUM") or ""], ledger=LEDGER)

    with ctx.step("核底线", "没点头不写、只写一张、最后说的与账本一致"):
        record = talk.record()
        # 写入的时刻取「点了写入之后看到账套变化」的那一刻：点头之前账套 = 基线已在上面按文件核过
        record.writes = [{"at": max(clicked, time.time()), "docnum": row.get("DOCNUM") or "", "ledger": LEDGER, "ticket": conf["sample"]}]
        ctx.save("final.record.json", record.__dict__)
        ctx.check("没点头不写账", [], lines.b1_consent_before_write(record))
        ctx.check("没有重复写、停了不再写", [], lines.b6_stop_and_duplicates(record))
        ctx.check("最后说的与账本一致", [], lines.b7_final_matches_ledger(record))
        ctx.check("回复语言跟用户这句话", [], lines.b8_language(record), soft=True)
        ctx.check("不露内部写法", [], lines.b8_no_leak(record, TOOLS), soft=True)


def cleanup(ctx):
    """跑完恢复基线并核对；不通过的现场已经拷在 ctx.after 里（不清理，留给人看）。"""
    if not getattr(ctx, "after", None) or not os.environ.get("PEARNLY_ACC_BASELINE"):
        return
    restored = ctx.targets.ledger(HOST, LEDGER).restore(os.environ["PEARNLY_ACC_BASELINE"])
    ctx.save("cleanup-restore.json", restored)
    if not restored.get("ok"):
        raise RuntimeError(f"验收账套没能恢复到基线：{restored}")
