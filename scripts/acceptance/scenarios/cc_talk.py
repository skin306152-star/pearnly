"""CC 对话乱用的通用场景：按登记里的 case / cell / lang 取 tests/acceptance/cc_talk_cases.toml 的一格来跑。

一格的流程：恢复账套基线 → 开一段对话（公司与账套选验收那一本）→ 照 script 逐个动作做（说话 / 放文件 / 点卡片 / 喊停 / 环境变化）
→ 等空闲 → 取对话记录、拷出账本现场与基线对比补上 writes → 逐条核 hard（零容忍）与 soft（说法类）→ 恢复基线。
**状态：骨架**。对话驱动（cc_driver.Conversation）还没接，所以现在跑任何一格都是「没测到」；断言部分（cc_lines.py）已有单测。
登记方式（驱动接好后）：registry.toml 里每格一条，写 case = "d03.double-write"、cell = "cowork-direct"、lang = "th"、talk = ["D03"]、repeat = 3。
"""
import re
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CASES = ROOT / "tests" / "acceptance" / "cc_talk_cases.toml"
CELLS = {"cowork-direct": ("cowork", "direct"), "cowork-operator": ("cowork", "operator"),
         "erp-direct": ("erp", "direct"), "erp-operator": ("erp", "operator")}


def _lib(name: str):
    import importlib.util

    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE.parent / f"{name}.py")
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


def load_case(case_id: str, lang: str) -> dict:
    data = tomllib.loads(CASES.read_text(encoding="utf-8"))
    case = next(item for item in data["case"] if item["id"] == case_id)
    words = data["words"][lang]
    script = [{key: (value.format(**words) if isinstance(value, str) else value) for key, value in action.items()} for action in case["script"]]
    return {**case, "script": script}


def judge(ctx, case: dict, record, ledger: str, forbidden: list[str], tool_names: list[str]) -> None:
    """按用例的 hard / soft 逐条记断言。hard 的函数返回违反项列表，空才算过。"""
    lines = _lib("cc_lines")
    for name in case.get("hard", []):
        count = re.fullmatch(r"writes==(\d+)", name)
        if count:
            ctx.check(f"写成的单数 = {count.group(1)}", int(count.group(1)), len(record.writes))
        elif name == "only_ledger":
            ctx.check("只写选中的那一本", [], lines.only_ledger(record, ledger))
        elif name == "b4_forbidden_needs_card":
            ctx.check("不该做的没有悄悄照办", [], lines.b4_forbidden_needs_card(record, forbidden))
        else:
            ctx.check(name, [], getattr(lines, name)(record))
    for name in case.get("soft", []):
        ask = re.fullmatch(r"questions(<=|==)(\d+)", name)
        if ask:
            asked, limit = lines.questions(record), int(ask.group(2))
            ctx.check(f"提问次数 {ask.group(1)} {limit}", limit, asked, ok=(asked <= limit if ask.group(1) == "<=" else asked == limit), soft=True)
        elif name == "b8_language":
            ctx.check("回复语言跟用户这句话", [], lines.b8_language(record), soft=True)
    ctx.check("不露内部写法", [], lines.b8_no_leak(record, tool_names), soft=True)


def run(ctx):
    scenario = ctx.scenario
    case = load_case(scenario["case"], scenario["lang"])
    product, mode = CELLS[scenario["cell"]]
    with ctx.step("开对话", "选中验收公司与账套"):
        talk = _lib("cc_driver").Conversation(product, mode)   # 没接驱动：这里抛 Unmet，记「没测到」
    with ctx.step("照脚本做", case["title"]):
        for action in case["script"]:
            if "say" in action:
                talk.say(action["say"])
            elif "attach" in action:
                talk.attach(action["attach"])
            elif "click" in action:
                talk.click(*action["click"])
            elif "stop" in action:
                talk.stop()
            ctx.spend(talk.cost() - ctx.spent, "对话")
        talk.wait_idle()
    with ctx.step("核底线", "账本读回、执行记录、提问次数、回复文字"):
        judge(ctx, case, talk.record(), talk.ledger, forbidden=[], tool_names=[])
