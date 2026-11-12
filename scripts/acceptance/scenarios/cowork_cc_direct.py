"""验收场景（示范 2 的骨架）：Cowork 的 CC 对话直写——放一张采购票 → 识别 → 写入计划 → 点「写入」→ CC 桌面版写进验收账套
→ 账本读回、不变式 → 月度回读对得上 → 恢复基线。就是「按现版本从 CC 入口真写验证过 0 项」的那条链。

**状态：骨架，未登记、未跑**。依赖两样还没到位的东西：集成分支上的直写修复（主会话通知后再跑）、CC 对话驱动（cc_driver.py 只有接口）。
所以现在跑它是「没测到」。账本那一半（基线、现场、对比、不变式、恢复）与示范 1 相同，已经可用；这里只写对话那一半的形状。
登记时：class = "chain"、env = "B"、rows = ["cowork.cc.tool.cowork_push_documents", "cowork.cc.tool.pearnly_write_start"]、
resources = ["testpc1", "ledger:69EXP/TEST2"]、needs_server = true、budget_usd = 1.0、repeat = 1（入库与每周 3）。
"""
import importlib.util
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER = os.environ.get("PEARNLY_ACC_LEDGER", r"\\accserver\d$\ACCOUNT\69EXP\TEST2")
TOOLS = ["cowork_push_documents", "pearnly_write_plan", "pearnly_write_start", "cowork_recognize_attachments"]


def _lib(name: str):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE.parent / f"{name}.py")
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


def run(ctx):
    lines = _lib("cc_lines")
    with ctx.step("开对话", "选中验收公司与验收账套，直写方式"):
        talk = _lib("cc_driver").open_conversation(ctx.scenario.get("driver", "real"), "cowork", "direct", ledger=LEDGER)   # real 没接：Unmet →「没测到」
    with ctx.step("放票并说一句", "识别出一张采购票，复述后出写入计划"):
        talk.attach(["purchase-a1"])
        talk.say("บันทึกให้หน่อย")
        talk.wait_idle()
        ctx.spend(talk.cost() - ctx.spent, "识别与复述")
        ctx.check("这时账本里还没有新单", [], lines.no_writes(talk.record()))
    with ctx.step("点写入", "CC 桌面版写进验收账套并回执"):
        talk.click("plan", "write")
        talk.wait_idle(300)
        ctx.spend(talk.cost() - ctx.spent, "写入")
    with ctx.step("核底线与账本", "一张单、只在验收账套、最后说的与账本一致"):
        record = talk.record()   # writes 由账本前后对比与 CC 桌面版写入日志补上（同示范 1 的 book.copy / diff / rows）
        ctx.check("写成一张", 1, len(record.writes))
        ctx.check("没点头不写账", [], lines.b1_consent_before_write(record))
        ctx.check("只写选中的那一本", [], lines.only_ledger(record, LEDGER))
        ctx.check("最后说的与账本一致", [], lines.b7_final_matches_ledger(record))
        ctx.check("回复语言跟用户这句话", [], lines.b8_language(record), soft=True)
        ctx.check("不露内部写法", [], lines.b8_no_leak(record, TOOLS), soft=True)
