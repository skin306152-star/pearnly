# -*- coding: utf-8 -*-
"""AI 接手：前三层（键盘、Win32 读状态、读 DBF）判断不了才用。

模型调用经产品站点（CC 机器通道的新请求，共用件那一步接线），Claude API 密钥只在服务端，这台电脑拿不到；
截图只在要 AI 判断时上传，用完即删。站点那边按每张 / 每批的费用上限把关，到了回 ``budget``。

- ``check``：一张截图 + 期望 → 结构化判断（Haiku 5.5，不开思考）；
- ``take_over``：电脑操作循环（``computer_toolset_20260801``），先 Haiku 5.5（≤8 轮）再 Opus 5.5（≤14 轮），
  模型给的每个动作照样过前台守卫、落点守卫（``Desk`` 实现里写死的）。

没接站点时用 ``NoHelper``：判断不了一律「必须人工」。
"""
from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional, Protocol, Tuple

HAIKU = "claude-haiku-5-5"
OPUS = "claude-opus-5-5"
CHAIN: Tuple[Tuple[str, int, str], ...] = ((HAIKU, 8, "medium"), (OPUS, 14, "medium"))
SHOT = (1280, 720)
# 请求体只用不需要 beta 头的字段（站点转发不带任何 anthropic-beta 头；11-05 测试机：带了上下文编辑 context_management，接手第一轮
# 就被回 400）。每一轮截图都留着：一次接手最多 Haiku 8 轮、Opus 14 轮，各自从头开对话，上下文有上限
GA_FIELDS = frozenset({"model", "max_tokens", "system", "messages", "tools", "thinking", "output_config", "cache_control"})

CHECK_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["state", "what_i_see", "dialog_text", "message_no", "lines_seen", "total_seen", "docnum_seen"],
    "properties": {
        "state": {"type": "string", "enum": ["as_expected", "dialog", "wrong", "unsure"]},
        "what_i_see": {"type": "string"}, "dialog_text": {"type": "string"}, "message_no": {"type": "string"},
        "lines_seen": {"type": "integer"}, "total_seen": {"type": "string"}, "docnum_seen": {"type": "string"},
    },
}

CHECK_SYSTEM = """你在核对泰国会计软件 Express（Windows 老式界面，泰文）当前画面是否符合期望。只看图说事实，不猜。
- 有弹窗（消息框、选择框）就回 state=dialog，dialog_text 抄弹窗文字，message_no 填消息号（如 4079，没有就空）。
- 只按「期望」那一句核对；草稿单只是参考。期望没提到的部分（例如还没轮到的明细、还没保存）不算不符。
- 画面和期望不符回 wrong；看不清回 unsure；相符回 as_expected。
- lines_seen：明细表格里已有内容的行数；total_seen：画面上的含税合计（看不到就空）；docnum_seen：单号栏的内容。"""

CU_SYSTEM = """你在一台 Windows 电脑上操作泰国会计软件 Express（服务器版，界面泰文），替会计照「草稿单」录单。
草稿单的会计口径已定，你只管照录，不改科目、不改金额、不自作主张。截图坐标就是你给动作用的坐标。

Express 操作要点：
- 打开单据窗口用 express_menu 工具：先 {"read": true} 看菜单，再 {"path": [...]} 一层一项给编号或菜单文字；不要按 Alt 和数字
  （后台操作按不了组合键；单据窗口开着时 Alt+数字 是单据窗口的快捷键，会弹 Message#.40）。
- 新单单号显示「前缀**NEW**」，保存后才变正式号。日期打 日/月/佛历两位年，如 25/10/69。数字不带千分位。
- 明细表格回车进不去，要点表格第一行的品码格。单位框里回车是跳到倍数格，要点框里的「ตกลง」。
- 常见提醒 4079 / 4107 / 4108 / 605 / 1029 按「ตกลง」继续；4224 / 4091（负库存被拦）不要硬录，finish(need_human)。

安全规矩（必须遵守）：
- 只操作 Express，不点 Express 以外的任何东西，不关 Express，不按 Alt+F4，不切换公司，不进设置。
- 看到「Error -70 … Reading File」这种网络读文件错误框：什么都别点，立刻 finish(need_human)。
  标题「Message#. 70」、内容「วันที่ไม่อยู่ในงวด」是日期不在期间的普通提示，也 finish(need_human)，不要自己改日期。
- 已有明细时按 Esc 或「取消」会直接把半张单存进账：绝不按 Esc。
- 除非目标里说要保存，否则不要点保存；不要重复保存同一张单。
- 做完（或确认做不到）一定调用 finish。"""

FINISH_TOOL = {
    "name": "finish",
    "description": "结束这次任务。resolved = 目标已达到；need_human = 需要人来处理（说明原因）；escalate = 你处理不了，交给更强的模型。",
    "strict": True,
    "input_schema": {"type": "object", "additionalProperties": False, "required": ["status", "note"],
                     "properties": {"status": {"type": "string", "enum": ["resolved", "need_human", "escalate"]},
                                    "note": {"type": "string"}}},
}


MENU_TOOL = {
    "name": "express_menu",
    "description": ("Express 的菜单（不动鼠标键盘，按菜单命令直接打开）。{\"read\": true} 读整棵菜单（缩进是层级，▸ 是子菜单，"
                    "(灰) 是灰掉的）；{\"path\": [\"1\", \"ซื้อเงินเชื่อ\"]} 从顶层起一层一项，每项给菜单编号或菜单文字，打开最后那一项。"
                    "落到子菜单时只有一个命令就打开它；有几个命令时由系统按单据定，不用你选。"),
    "input_schema": {"type": "object", "additionalProperties": False,
                     "properties": {"read": {"type": "boolean"},
                                    "path": {"type": "array", "items": {"type": "string"}, "maxItems": 5}}},
}
CUSTOM_TOOLS = ("express_menu",)  # 自定义工具（结果不带 toolset_name）
# 工具执行时这些异常原样往上抛给引擎（崩溃、被拦、人点停止；菜单子菜单多候选由引擎按单据定，不让 AI 猜）
RAISE_THROUGH = ("Crash", "Blocked", "Stopped", "MenuAmbiguous")


class BudgetStop(Exception):
    """站点回了费用上限（或本机的次数上限）：整批停，等人决定。"""


class Tools(Protocol):
    """引擎给 AI 用的电脑操作：截图（截图坐标系）和执行一个工具集成员。"""

    def screenshot(self, purpose: str) -> str: ...

    def member(self, name: str, inp: Dict) -> List[Dict]: ...


class Helper(Protocol):
    def check(self, png_b64: str, prompt: str) -> Optional[Dict]: ...

    def take_over(self, goal: str, tools: Tools) -> Tuple[str, str]: ...


class NoHelper:
    """没接站点：判断不了就交给人。"""

    calls = 0

    def check(self, png_b64: str, prompt: str) -> Optional[Dict]:
        return None

    def take_over(self, goal: str, tools: Tools) -> Tuple[str, str]:
        return "need_human", "ai not connected"


def png_block(png_b64: str) -> Dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png_b64}}


class ComputerUseHelper:
    """经 ``transport(kind, request) -> response`` 调模型。``kind`` 是 ``check`` / ``turn``，``request`` 是 Messages API
    的请求体（不含密钥），``response`` 是 API 原样的响应字典。站点回 ``{"error": "budget"}`` 时抛 ``BudgetStop``。"""

    def __init__(self, transport: Callable[[str, Dict], Dict], *, max_takeovers: int = 20,
                 sleep: Callable[[float], None] = time.sleep):
        self.transport = transport
        self.max_takeovers = max_takeovers
        self.takeovers = 0
        self.calls = 0
        self.sleep = sleep

    def _call(self, kind: str, request: Dict) -> Dict:
        self.calls += 1
        response = self.transport(kind, request)
        if response.get("error") == "budget":
            raise BudgetStop("budget")
        if response.get("error"):
            raise RuntimeError(str(response["error"])[:200])
        return response

    def check(self, png_b64: str, prompt: str) -> Optional[Dict]:
        import json

        response = self._call("check", {
            "model": HAIKU, "max_tokens": 2000, "system": CHECK_SYSTEM, "thinking": {"type": "disabled"},
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": CHECK_SCHEMA}},
            "messages": [{"role": "user", "content": [png_block(png_b64), {"type": "text", "text": prompt}]}]})
        if response.get("stop_reason") == "refusal":
            return None
        text = "".join(b.get("text", "") for b in response.get("content", []) if b.get("type") == "text")
        return json.loads(text)

    def take_over(self, goal: str, tools: Tools) -> Tuple[str, str]:
        if self.takeovers >= self.max_takeovers:
            raise BudgetStop("takeovers")
        self.takeovers += 1
        for model, turns, effort in CHAIN:
            status, note = self._loop(model, turns, effort, goal, tools)
            if status != "escalate":
                return status, note
        return "need_human", note

    def _loop(self, model: str, max_turns: int, effort: str, goal: str, tools: Tools) -> Tuple[str, str]:
        messages: List[Dict] = [{"role": "user", "content": [{"type": "text", "text": goal},
                                                             png_block(tools.screenshot("takeover"))]}]
        for _ in range(max_turns):
            response = self._call("turn", {
                "model": model, "max_tokens": 8000,
                "system": [{"type": "text", "text": CU_SYSTEM, "cache_control": {"type": "ephemeral"}}],
                "tools": [{"type": "computer_toolset_20260801"}, MENU_TOOL, FINISH_TOOL], "messages": messages,
                "output_config": {"effort": effort}, "thinking": {"type": "adaptive"},
                "cache_control": {"type": "ephemeral"}})
            if response.get("stop_reason") == "refusal":
                return "need_human", "refusal"
            content = response.get("content", [])
            messages.append({"role": "assistant", "content": content})
            uses = [b for b in content if b.get("type") == "tool_use"]
            if not uses:
                messages.append({"role": "user", "content": [{"type": "text", "text": "请继续操作，或调用 finish。"}]})
                continue
            results, failed, finish, last, acted = [], False, None, None, False
            for block in uses:
                if block["name"] == "finish":
                    finish = block.get("input") or {}
                    results.append({"type": "tool_result", "tool_use_id": block["id"], "content": "OK"})
                    continue
                base = {"type": "tool_result", "tool_use_id": block["id"]}
                if block["name"] not in CUSTOM_TOOLS:
                    base["toolset_name"] = "computer"
                if failed:
                    results.append({**base, "is_error": True, "content": "Not executed: an earlier action failed."})
                    continue
                try:
                    results.append({**base, "content": tools.member(block["name"], block.get("input") or {})})
                    last = results[-1]
                    inp = block.get("input") or {}
                    acted = block["name"] not in ("screenshot", "zoom", "cursor_position", "wait") and not (
                        block["name"] == "express_menu" and (inp.get("read") or not inp.get("path")))
                except Exception as error:  # noqa: BLE001 — 回给模型；崩溃 / 拦截由引擎的异常类型往上抛
                    if type(error).__name__ in RAISE_THROUGH:
                        raise
                    failed = True
                    results.append({**base, "is_error": True, "content": f"Error: {error}"})
            if finish is not None:
                return finish.get("status", "need_human"), finish.get("note", "")
            if acted and not failed and last is not None:
                self.sleep(0.4)
                last["content"] = list(last["content"]) + [png_block(tools.screenshot("auto"))]
            messages.append({"role": "user", "content": results})
        return "escalate", f"{max_turns} turns"
